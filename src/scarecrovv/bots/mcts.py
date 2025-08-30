# src/scarecrovv/bots/mcts.py
from __future__ import annotations
import time
import copy
from typing import Tuple, List, Optional

from scarecrovv.engine.actions import legal_actions, apply_action, Action
from scarecrovv.bots.greedy import choose_action as greedy_choose


def _rotate_to_next_player(s) -> None:
    """Simple rotation for simulations (one ply = one action then next seat)."""
    n = len(s.players) if getattr(s, "players", None) else 0
    if n <= 0:
        return
    # prefer round's turn order if known
    if getattr(s, "turn_order", None):
        try:
            idx = s.turn_order.index(s.current_player)
        except Exception:
            idx = 0
        s.current_player = s.turn_order[(idx + 1) % n]
    else:
        s.current_player = (getattr(s, "current_player", 0) + 1) % n


def _terminal_value(s, root_pid: int) -> Optional[float]:
    """Return a terminal eval if someone hit the victory threshold, else None."""
    vp_goal = getattr(s.cfg, "victory_vp", 9999)
    # someone won?
    winners = [p.id for p in s.players if getattr(p, "vp", 0) >= vp_goal]
    if winners:
        # high reward if root is winner, else large negative
        return 1e3 if root_pid in winners else -1e3
    return None


def _static_eval(s, root_pid: int) -> float:
    """
    Lightweight heuristic value of state s for root_pid.
    VP lead + tiny resource tiebreaker.
    """
    vps = [getattr(p, "vp", 0) for p in s.players]
    my = vps[root_pid]
    opp = max(v for i, v in enumerate(vps) if i != root_pid) if len(vps) > 1 else 0
    lead = my - opp

    # tiny nudge for resources to break ties (don’t overweight!)
    def res_sum(p):
        rr = getattr(p, "resources", {}) or {}
        return sum(rr.values())
    rdiff = res_sum(s.players[root_pid]) - max(res_sum(p) for i, p in enumerate(s.players) if i != root_pid) if len(s.players) > 1 else 0

    return float(lead) + 0.01 * float(rdiff)


def _default_policy_choose(s, pid: int) -> Action:
    """
    Rollout policy: use greedy bot. If it ever fails, pick a non-pass at random.
    """
    try:
        a, _ = greedy_choose(s, pid)
        if a is not None:
            return a
    except Exception:
        pass

    acts = legal_actions(s, pid)
    if not acts:
        return ("pass", None)

    non_pass = [a for a in acts if a[0] != "pass"]
    return s.rng.choice(non_pass or acts)


def _simulate_from(s, root_pid: int, horizon: int) -> float:
    """
    Simulate up to 'horizon' plies (actions), alternating seats.
    """
    # Terminal right away?
    tv = _terminal_value(s, root_pid)
    if tv is not None:
        return tv

    depth = 0
    while depth < horizon:
        pid = getattr(s, "current_player", 0)
        acts = legal_actions(s, pid)

        # If no actions (extremely rare), pass
        if not acts:
            act = ("pass", None)
        else:
            act = _default_policy_choose(s, pid)

        apply_action(s, pid, act)

        # quick terminal check
        tv = _terminal_value(s, root_pid)
        if tv is not None:
            return tv

        _rotate_to_next_player(s)
        depth += 1

    # Horizon reached → static eval
    return _static_eval(s, root_pid)


def _budget_ok(start_ns: int, step_count: int, actions_cap: int, time_ms: int) -> bool:
    if actions_cap and step_count >= actions_cap:
        return False
    if time_ms:
        elapsed_ms = (time.time_ns() - start_ns) / 1_000_000.0
        if elapsed_ms >= time_ms:
            return False
    return True


def mcts_choose(g, pid: int, rollouts: int, horizon: int,
                actions_cap: int = 0, time_ms: int = 0) -> Tuple[Action, bool]:
    """
    Monte-Carlo action selection for the current player:
      - for each legal action a at root, sample several rollouts
      - each rollout: deepcopy state, apply a, then play horizon-1 plies with default policy
      - pick action with highest mean return for 'pid'
    Returns (action, False) to match greedy’s signature.
    """
    root_actions = legal_actions(g, pid)
    if not root_actions:
        return ("pass", None), False

    # Heuristic: don’t waste time if there’s only one real choice
    non_pass = [a for a in root_actions if a[0] != "pass"]
    if len(non_pass) == 1:
        return non_pass[0], False

    # Prep
    results_sum = {a: 0.0 for a in root_actions}
    results_n = {a: 0 for a in root_actions}

    start_ns = time.time_ns()
    step_count = 0

    # Ensure we start from the right actor
    # (loop sets this, but make robust if missing)
    if not hasattr(g, "current_player"):
        g.current_player = pid

    # Cycle actions round-robin across rollouts to ensure coverage
    idx = 0
    total_trials = max(1, int(rollouts))

    while _budget_ok(start_ns, step_count, actions_cap, time_ms) and sum(results_n.values()) < total_trials * len(root_actions):
        a = root_actions[idx % len(root_actions)]
        idx += 1

        # Deepcopy game and apply root action
        s = copy.deepcopy(g)
        # Defensive: make sure s.current_player matches pid for root move
        s.current_player = pid
        apply_action(s, pid, a)
        step_count += 1

        # rotate to next seat and rollout remainder
        _rotate_to_next_player(s)
        ret = _simulate_from(s, root_pid=pid, horizon=max(0, int(horizon) - 1))
        results_sum[a] += ret
        results_n[a] += 1

        # Optional: stop if time budget used
        if not _budget_ok(start_ns, step_count, actions_cap, time_ms):
            break

    # Fallback: if some actions had 0 samples (tiny budgets), ensure at least 1
    for a in root_actions:
        if results_n[a] == 0 and _budget_ok(start_ns, step_count, actions_cap, time_ms):
            s = copy.deepcopy(g)
            s.current_player = pid
            apply_action(s, pid, a)
            step_count += 1
            _rotate_to_next_player(s)
            ret = _simulate_from(s, root_pid=pid, horizon=max(0, int(horizon) - 1))
            results_sum[a] += ret
            results_n[a] += 1

    # Choose the action with highest mean value; avoid choosing PASS if we had real samples
    def mean(a):
        n = results_n[a]
        return (results_sum[a] / n) if n > 0 else float("-inf")

    # prefer non-pass if tie
    best = max(root_actions, key=lambda x: (mean(x), 0 if x[0] != "pass" else -1))
    return best, False

# src/scarecrovv/engine/loop.py
from __future__ import annotations
from typing import Dict, Any, List
import copy

try:
    from dataclasses import replace as dc_replace
except Exception:
    dc_replace = None

from scarecrovv.engine.setup import setup
from scarecrovv.engine.rounds import start_of_round, end_of_round
from scarecrovv.engine.actions import apply_action
from scarecrovv.io.summaries import write_summaries

# --- Bot wiring: import both, select at runtime ---
try:
    from scarecrovv.bots.greedy import choose_action as greedy_choose
except Exception:
    greedy_choose = None  # type: ignore

try:
    from scarecrovv.bots.mcts import choose_action as mcts_choose
except Exception:
    mcts_choose = None  # type: ignore

ACTIONS_PER_TURN_DEFAULT = 2

def _winner_or_none(g):
    for p in g.players:
        if p.vp >= g.cfg.victory_vp:
            return p.id
    return None

def _winner_by_points(g):
    # neutral-ish tie-breaker
    vps = [p.vp for p in g.players]
    best = max(vps) if vps else 0
    candidates = [i for i, v in enumerate(vps) if v == best]
    if len(candidates) == 1:
        return candidates[0]
    # secondary: more plasma; final: random among tied
    best_plasma = max(g.players[i].resources.get("plasma", 0) for i in candidates)
    c2 = [i for i in candidates if g.players[i].resources.get("plasma", 0) == best_plasma]
    if len(c2) == 1:
        return c2[0]
    return g.rng.choice(c2)

def _advance_player(g, passed: List[bool]) -> None:
    """Advance to next non-passed player in this round-robin order."""
    if not g.turn_order:
        g.set_turn_order_for_round()
    idx = g.turn_order.index(g.current_player) if g.current_player in g.turn_order else 0
    n = len(g.turn_order)
    for step in range(1, n + 1):
        nxt = g.turn_order[(idx + step) % n]
        if not passed[nxt]:
            g.current_player = nxt
            return
    # if all passed, caller will end the round

def _bot_label_from_cfg(cfg) -> str:
    if getattr(cfg, "mcts", 0) and mcts_choose:
        return f"mcts@{getattr(cfg,'rollouts',0)}x{getattr(cfg,'horizon',0)}"
    return "greedy"

def _choose_action_for_cfg(cfg):
    """Pick the bot's choose_action based on cfg; fallback to greedy if MCTS unavailable."""
    if getattr(cfg, "mcts", 0) and mcts_choose:
        return mcts_choose
    # fallback
    if greedy_choose:
        return greedy_choose
    # absolute fallback in pathological cases
    def _noop_choose(g, pid: int):
        return ("pass", None), False
    return _noop_choose

def play_one(cfg) -> Dict[str, Any]:
    g = setup(cfg)

    # Choose bot once per game based on cfg
    choose_action = _choose_action_for_cfg(cfg)

    # Log which bot + who will start this game (based on start_offset applied in setup)
    starter = getattr(g, "start_player", 0)
    starter_bot = _bot_label_from_cfg(cfg)
    g.emit({"a": "game_start", "seed": g.cfg.seed, "starter": starter, "starter_bot": starter_bot})

    start_of_round(g)

    n = len(g.players)
    passed = [False] * n
    actions_per_turn = getattr(cfg, "actions_per_turn", ACTIONS_PER_TURN_DEFAULT)
    actions_left = actions_per_turn
    g.current_player = g.start_player

    turn_cap = getattr(cfg, "turn_cap", 5000)
    winner = None

    for _ in range(turn_cap):
        # round end condition: all passed
        if all(passed):
            end_of_round(g)
            winner = _winner_or_none(g)
            if winner is not None:
                g.emit({"a": "win", "p": winner, "reason": "vp_threshold"})
                break
            # next round
            start_of_round(g)
            passed = [False] * n
            actions_left = actions_per_turn
            g.current_player = g.start_player
            continue

        pid = g.current_player
        if passed[pid]:
            _advance_player(g, passed)
            actions_left = actions_per_turn
            continue

        action, explored = choose_action(g, pid)
        if explored:
            g.emit({"a": "explore_flag", "p": pid, "value": True})

        apply_action(g, pid, action)

        # update pass / actions-left
        if action[0] == "pass":
            passed[pid] = True
            actions_left = 0  # done for the round
        else:
            actions_left -= 1

        # victory check
        winner = _winner_or_none(g)
        if winner is not None:
            g.emit({"a": "win", "p": winner, "reason": "vp_threshold"})
            break

        # rotate turn if out of actions
        if actions_left <= 0:
            _advance_player(g, passed)
            actions_left = actions_per_turn

    if winner is None:
        winner = _winner_by_points(g)
        g.emit({"a": "win", "p": winner, "reason": "points_at_cap", "turns": g.turn})

    vps = [getattr(p, "vp", 0) for p in g.players]
    g.emit({"a": "game_end_vp", "vps": vps})

    return {
        "winner": winner,
        "starter": starter,
        "starter_bot": starter_bot,
        "vps": vps,
        "players": [{"owned": [], "first_play": p.first_play_turn} for p in g.players],
        "events": g.log.records,
    }

def run_many(cfg, games: int) -> Dict[str, Any]:
    outs = []
    base_seed = int(getattr(cfg, "seed", 0) or 0)
    num_players = int(getattr(cfg, "players", 3) or 3)

    for i in range(games):
        # copy cfg and vary the seed
        if dc_replace:
            cfg_i = dc_replace(cfg, seed=base_seed + i)
        else:
            cfg_i = copy.copy(cfg)
            cfg_i.seed = base_seed + i

        # optional: rotate starting seat across games
        cfg_i.start_offset = i % num_players

        outs.append(play_one(cfg_i))
        if cfg.progress_every and (i + 1) % cfg.progress_every == 0:
            print(f"[progress] finished {i+1}/{games} games")

    wc = {}
    for o in outs:
        k = str(o["winner"])
        wc[k] = wc.get(k, 0) + 1

    cards_path, fields_path = write_summaries(cfg, games, outs)
    print(f"[summaries] wrote {cards_path} and {fields_path}")

    return {"games": len(outs), "winner_counts": wc, "logs": [cards_path, fields_path]}

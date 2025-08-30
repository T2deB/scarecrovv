# src/scarecrovv/bots/greedy.py
from __future__ import annotations
from typing import Tuple, List, Optional
from scarecrovv.engine.actions import legal_actions, Action
from scarecrovv.engine.eval import (
    expected_vp_if_played_now,
    resource_delta_if_played_now,
    synergy_bonus,
    hand_size,
    mat_slots_free,
)

# ----------------------------
# Small helpers
# ----------------------------

def _parse_worker_field(a: Action) -> Optional[str]:
    if len(a) < 2:
        return None
    payload = a[1]
    if isinstance(payload, tuple):
        return payload[0] if payload else None
    return payload

def _initiative_variants(acts: List[Action]) -> List[Action]:
    return [a for a in acts if a[0] == "worker" and _parse_worker_field(a) == "initiative"]

def _distance_to_first(g, pid: int) -> float:
    n = len(g.players) or 1
    if not g.turn_order or pid not in g.turn_order:
        return 1.0  # be conservative if unknown
    pos = g.turn_order.index(pid)
    return (pos % n) / max(n - 1, 1)  # 0 if first, ~1 if last

def _initiative_desirability(g, pid: int, acts: List[Action]) -> float:
    if not _initiative_variants(acts):
        return float("-inf")
    # If already first this round, initiative is low value (throttle strongly)
    dist = _distance_to_first(g, pid)
    already_first_penalty = 0.15 if dist <= 1e-9 else 1.0

    late = (g.turn > getattr(g.cfg, "late_round_threshold", 6))
    workers = getattr(g.players[pid], "workers", 0)

    pos_bonus      = 1.5 * dist              # more valuable the further you are from first
    late_bonus     = 0.6 if late else 0.2
    worker_penalty = 0.6 if workers <= 1 else 0.0

    base = (pos_bonus + late_bonus - worker_penalty) * already_first_penalty
    bias = float(getattr(g.cfg, "initiative_bias", 1.0))
    return base * bias

# ----------------------------
# Need-driven worker guidance
# ----------------------------

def _cheapest_need_to_play(g, pid: int) -> dict:
    """
    Scan hand; for each library card, compute resource shortfall to play it
    (either active or to-mat). Return the smallest shortfall vector.
    """
    p = g.players[pid]
    best = None

    def shortfall(cost: dict) -> dict:
        need = {}
        for k, v in cost.items():
            have = p.resources.get(k, 0)
            # we *also* have RES: tokens in hand; approximate count
            tok = f"RES:{k}"
            tok_count = sum(1 for t in p.hand if t == tok)
            have_total = have + tok_count
            if have_total < v:
                need[k] = v - have_total
        return need

    for tok in getattr(p, "hand", []):
        if isinstance(tok, str) and (tok.startswith("RES:") or tok.startswith("VP:")):
            continue
        c = getattr(g, "cards", {}).get(tok)
        if not c:
            continue

        # Evaluate both modes; prefer mat if a slot is free and the card can be matted
        want_mat = getattr(c, "can_play_on_mat", False) and mat_slots_free(g, pid) > 0
        cost_active = getattr(c, "cost_play_active", getattr(c, "play_cost", {}))
        cost_mat    = getattr(c, "cost_play_mat",     getattr(c, "play_cost", {}))

        # pick the smaller shortfall of the two modes
        need_a = shortfall(cost_active)
        need_m = shortfall(cost_mat) if want_mat else need_a
        need   = need_m if sum(need_m.values()) < sum(need_a.values()) else need_a

        if best is None or sum(need.values()) < sum(best.values()):
            best = need

    return best or {}

def _need_weight(need: dict, key: str) -> float:
    return float(need.get(key, 0))

# ----------------------------
# Main policy
# ----------------------------

def choose_action(g, pid: int) -> Tuple[Action, bool]:
    acts = legal_actions(g, pid)
    if not acts:
        return ("pass", None), False

    # ε-greedy exploration
    if g.rng.random() < getattr(g.cfg, "explore", 0.0):
        guided = [a for a in acts if a[0] in ("play", "worker", "buy_pool", "buy_vp")]
        return (g.rng.choice(guided or acts), True)

    def score(a: Action) -> float:
        kind = a[0]
        turn = g.turn
        late = (turn > getattr(g.cfg, "late_round_threshold", 6))

        # -------- PLAY (to mat or active)
        if kind == "play":
            # ("play", (hand_idx, to_mat, slot))
            hand_idx, to_mat, slot = a[1]
            tok = g.players[pid].hand[hand_idx]
            mode = "mat" if to_mat else "active"

            # Token VP -> immediate points (slot1 bonus if present)
            if isinstance(tok, str) and tok.startswith("VP:"):
                try:
                    vp_val = int(tok.split(":")[1])
                except Exception:
                    vp_val = 1
                bonus = 2 if 1 in g.players[pid].mat else 0
                hand_relief = 0.25 if hand_size(g, pid) >= getattr(g.cfg, "big_hand_threshold", 6) else 0.0
                return 3.0 * (vp_val + bonus) + 0.6 * hand_relief

            # Library
            if tok not in getattr(g, "cards", {}):
                # Unknown id? be neutral-ish, prefer active for hand relief
                hand_relief = 0.25 if hand_size(g, pid) >= getattr(g.cfg, "big_hand_threshold", 6) else 0.0
                return 0.6 * hand_relief + (0.2 if mode == "mat" else 0.0)

            vp_now, vp_future = expected_vp_if_played_now(g, pid, tok, mode)
            res = resource_delta_if_played_now(g, pid, tok, mode)
            syn = synergy_bonus(g, pid, tok, mode)
            mat_pref = 1.0 if (mode == "mat" and mat_slots_free(g, pid) > 0) else 0.0
            hand_relief = 0.25 if hand_size(g, pid) >= getattr(g.cfg, "big_hand_threshold", 6) else 0.0
            return (
                3.0 * vp_now
                + 1.5 * vp_future
                + 1.0 * syn
                + 0.6 * hand_relief
                + 0.4 * mat_pref
                + 0.2 * res
            )

        # -------- WORKER
        if kind == "worker":
            field = _parse_worker_field(a)
            need = _cheapest_need_to_play(g, pid)

            if field == "initiative":
                return _initiative_desirability(g, pid, acts)

            # Base values (reduce compost spam, value draw & plasma a bit)
            base = {
                "rookery": 1.6,   # card draw = tempo
                "plasma":  1.3,
                "ash":     1.1,
                "shards":  1.1,
                "forage":  1.0,   # okay, but tuned by needs below
                "compost": 0.9,   # down from 1.2 to avoid overuse
            }.get(field, 0.8)

            # Boost fields that pay the current shortfall
            boost = 0.0
            boost += 0.9 * _need_weight(need, "plasma") if field == "plasma" else 0.0
            boost += 0.8 * _need_weight(need, "ash")    if field == "ash" else 0.0
            boost += 0.8 * _need_weight(need, "shards") if field == "shards" else 0.0
            if field == "forage":
                boost += 0.5 * (
                    _need_weight(need, "nut")
                  + _need_weight(need, "berry")
                  + _need_weight(need, "mushroom")
                )

            # Slight late-game nerf to raw income over playing cards
            if late and field in ("plasma", "ash", "shards", "forage"):
                base *= 0.9

            return base + boost

        # -------- BUY from Pool
        if kind == "buy_pool":
            j = a[1][0]
            if not (0 <= j < len(g.pool)):
                return 0.0
            cid = g.pool[j]
            if cid in getattr(g, "cards", {}):
                vp_act_now, vp_act_future = expected_vp_if_played_now(g, pid, cid, "active")
                vp_mat_now, vp_mat_future = expected_vp_if_played_now(g, pid, cid, "mat")
                mats_free = mat_slots_free(g, pid)
                mat_pressure = -0.6 if (mats_free == 0 and (vp_mat_now + vp_mat_future) > (vp_act_now + vp_act_future) + 0.5) else 0.0
                best_v = max(vp_act_now + vp_act_future, vp_mat_now + vp_mat_future)
                # small nudge to prefer cards we can plausibly play (future > 0)
                play_hint = 0.2 if best_v > 0 else 0.0
                return 0.7 * best_v + mat_pressure + play_hint
            return 0.1  # unknown id

        # -------- BUY VP piles
        if kind == "buy_vp":
            v = a[1][0]
            base = {1: 0.6, 2: 0.9, 3: 1.2}.get(v, 0.5)
            return base if late else base * 0.8

        # -------- PASS
        if kind == "pass":
            return -1.0

        return 0.0

    a_best = max(acts, key=score)
    return a_best, False

# src/scarecrovv/engine/actions.py
from __future__ import annotations
from typing import List, Tuple, Optional, Dict
from scarecrovv.model.game import GameState
from scarecrovv.model.card import Card
from scarecrovv.engine.effects_globals import apply_global_effects
from scarecrovv.engine.setup import (
    discounted_cost, total_discount_for_card,
)
# ... keep your imports
from typing import List, Tuple, Optional, Dict
# (rest of your imports unchanged)

Action = Tuple[str, Optional[tuple]]

# ----------------------------
# Payment helpers (UPDATED)
# ----------------------------

# ----------------------------
# VP cost helpers (choices + concrete)
# ----------------------------

def _vp_play_cost(g, value: int) -> dict:
    """
    Returns either a concrete cost dict (e.g. {"plasma":1,"ash":1,"nut":1})
    or a CHOICE bundle: {"__choice__":[{"plasma":2,"shards":1}, {"plasma":1,"shards":2}, ...]}
    """
    if value == 1:
        opts = getattr(g.cfg, "vp1_play_cost_options", None)
        if isinstance(opts, list) and opts:
            # normalize to plain dicts
            return {"__choice__": [dict(o) for o in opts]}
        return {}  # free if not configured
    elif value == 3:
        cost = getattr(g.cfg, "vp3_play_cost", None)
        return dict(cost) if isinstance(cost, dict) else {}
    return {}

def _can_pay_with_choice(p, cost: dict) -> bool:
    """Understands the {'__choice__':[...]} wrapper; otherwise defers to _can_pay_mixed."""
    if not cost:
        return True
    if "__choice__" in cost:
        for option in cost["__choice__"]:
            if _can_pay_mixed(p, option):
                return True
        return False
    return _can_pay_mixed(p, cost)

def _pay_with_choice(p, cost: dict, prefer_tokens_first: bool = True) -> bool:
    """Picks the first payable option and pays it; otherwise pays the concrete cost."""
    if not cost:
        return True
    if "__choice__" in cost:
        for option in cost["__choice__"]:
            if _can_pay_mixed(p, option):
                _pay_mixed(p, option, prefer_tokens_first=prefer_tokens_first)
                return True
        return False
    _pay_mixed(p, cost, prefer_tokens_first=prefer_tokens_first)
    return True

def _count_res_tokens_in_hand(p, key: str) -> int:
    tok = f"RES:{key}"
    return sum(1 for t in p.hand if isinstance(t, str) and t == tok)

def _remove_res_tokens_from_hand(p, key: str, n: int) -> int:
    if n <= 0:
        return 0
    tok = f"RES:{key}"
    removed = 0
    i = 0
    while i < len(p.hand) and removed < n:
        if p.hand[i] == tok:
            del p.hand[i]
            removed += 1
            continue
        i += 1
    return removed

def _available_amount(p, key: str) -> int:
    """Total availability of a single resource across pool counters + hand tokens."""
    return p.resources.get(key, 0) + _count_res_tokens_in_hand(p, key)

def _can_pay_mixed(p, cost: Dict[str, int]) -> bool:
    for k, need in cost.items():
        if k == "__choice_one_of__":
            # handled elsewhere
            continue
        if _available_amount(p, k) < need:
            return False
    return True

def _pay_mixed(p, cost: Dict[str, int], prefer_tokens_first: bool = True) -> None:
    for k, need in cost.items():
        if k == "__choice_one_of__":
            continue
        if need <= 0:
            continue
        if prefer_tokens_first:
            used_tok = _remove_res_tokens_from_hand(p, k, need)
            remain = need - used_tok
            if remain > 0:
                p.resources[k] = p.resources.get(k, 0) - remain
        else:
            pool_pay = min(p.resources.get(k, 0), need)
            p.resources[k] = p.resources.get(k, 0) - pool_pay
            remain = need - pool_pay
            if remain > 0:
                _remove_res_tokens_from_hand(p, k, remain)

# ---------- VP token PLAY costs (NEW) ----------

def _vp_play_cost(g: GameState, vp_value: int) -> Dict[str, int]:
    """
    Returns a cost dict for playing VP tokens.
    Supports a special key '__choice_one_of__': a list of resource keys of which exactly 1 must be paid.
    Defaults implement:
      VP:1  -> plasma:1, shards:1, + one of {plasma, shards, ash, nut, berry, mushroom}
      VP:3  -> one of each {plasma, ash, shards, nut, berry, mushroom}
    You can override via cfg.vp1_play_cost / cfg.vp3_play_cost (same shape).
    """
    if vp_value == 1:
        default = {
            "plasma": 1,
            "shards": 1,
            "__choice_one_of__": ["plasma", "shards", "ash", "nut", "berry", "mushroom"],
        }
        return getattr(g.cfg, "vp1_play_cost", default)
    elif vp_value == 3:
        default = {"plasma":1, "ash":1, "shards":1, "nut":1, "berry":1, "mushroom":1}
        return getattr(g.cfg, "vp3_play_cost", default)
    else:
        return {}

def _can_pay_with_choice(p, cost: Dict[str, int]) -> bool:
    """Check fixed part + 'pay exactly 1 from this set' if present."""
    if not _can_pay_mixed(p, cost):
        return False
    choices = cost.get("__choice_one_of__", [])
    if choices:
        # need at least ONE resource with availability >= 1
        return any(_available_amount(p, rk) >= 1 for rk in choices)
    return True

def _choose_choice_key_to_pay(p, choices: List[str]) -> Optional[str]:
    """Pick a resource to satisfy the one-of: prefer the one we have the most TOKENS of, then most available."""
    if not choices:
        return None
    # prioritize dumping hand tokens to keep hand lean
    best = None
    best_tuple = None  # (-tokens_in_hand, -total_available) for max; we’ll invert as we want max
    for rk in choices:
        tok = _count_res_tokens_in_hand(p, rk)
        avail = _available_amount(p, rk)
        if avail < 1:
            continue
        key = (-tok, -avail)  # more tokens/avail is better
        if best_tuple is None or key < best_tuple:
            best_tuple = key
            best = rk
    return best

def _pay_with_choice(p, cost: Dict[str, int], prefer_tokens_first: bool = True) -> bool:
    """Pay fixed costs, then one unit from choices if present."""
    _pay_mixed(p, {k:v for k,v in cost.items() if k != "__choice_one_of__"}, prefer_tokens_first)
    choices = cost.get("__choice_one_of__", [])
    if choices:
        rk = _choose_choice_key_to_pay(p, choices)
        if rk is None:
            return False
        if prefer_tokens_first:
            used = _remove_res_tokens_from_hand(p, rk, 1)
            if used == 0:
                p.resources[rk] = p.resources.get(rk, 0) - 1
        else:
            if p.resources.get(rk, 0) > 0:
                p.resources[rk] -= 1
            else:
                _remove_res_tokens_from_hand(p, rk, 1)
    return True

# ----------------------------
# Legal actions (UPDATED)
# ----------------------------

def _affordable_now(g: GameState, pid: int, c: Card) -> bool:
    p = g.players[pid]
    disc = total_discount_for_card(g, p, c)
    eff = discounted_cost(c, disc)
    return _can_pay_mixed(p, eff)

def _legal_buy_vp(g: GameState, pid: int) -> List[Action]:
    """Offer ONLY VP:1 and VP:3 piles; cheap plasma-only buy costs by default."""
    p = g.players[pid]
    out: List[Action] = []
    # plasma buy costs (configurable)
    cost1 = getattr(g.cfg, "vp_cost_1", 1)  # cheaper to buy
    cost3 = getattr(g.cfg, "vp_cost_3", 2)
    if _available_amount(p, "plasma") >= cost1:
        out.append(("buy_vp", (1,)))
    if _available_amount(p, "plasma") >= cost3:
        out.append(("buy_vp", (3,)))
    return out

def legal_actions(g: GameState, pid: int) -> List[Action]:
    actions: List[Action] = []
    p = g.players[pid]

    # PLAY from hand
    for i, tok in enumerate(p.hand):
        # VP tokens now require play costs; only legal if affordable
        if isinstance(tok, str) and tok.startswith("VP:"):
            try:
                vp_val = int(tok.split(":")[1])
            except Exception:
                vp_val = 1
            play_cost = _vp_play_cost(g, vp_val)
            if _can_pay_with_choice(p, play_cost):
                actions.append(("play", (i, False, None)))
            continue

        # Library card
        c = g.cards.get(tok)
        if not c:
            continue

        # Discard-play (active)
        if _affordable_now(g, pid, c):
            actions.append(("play", (i, False, None)))

        # To-mat
        if c.can_play_on_mat and _affordable_now(g, pid, c):
            for s in range(1, 7):
                if s not in p.mat:
                    actions.append(("play", (i, True, s)))

    # BUY from pool
    for j, cid in enumerate(g.pool):
        c = g.cards.get(cid)
        if not c:
            continue
        cost = getattr(g.cfg, "pool_buy_cost_override", None)
        if cost is None:
            cost = c.buy_cost_plasma
        if _available_amount(p, "plasma") >= cost:
            actions.append(("buy_pool", (j,)))

    # BUY VP piles (only 1 and 3)
    actions.extend(_legal_buy_vp(g, pid))

    # WORKER (respect capacity)
    if p.workers > 0:
        for field, cap in g.field_capacity.items():
            occ_raw = g.field_occupancy.get(field, 0)
            occ = 0 if occ_raw is None else int(occ_raw)
            if occ < int(cap or 0):
                actions.append(("worker", (field,)))

    # PASS
    actions.append(("pass", None))
    return actions

# ----------------------------
# Apply actions (UPDATED VP play/buy)
# ----------------------------

def apply_action(g: GameState, pid: int, action: Action) -> None:
    kind, arg = action
    if kind == "play":
        i, to_mat, slot = arg
        _act_play(g, pid, i, to_mat, slot)
    elif kind == "buy_pool":
        j, = arg
        _act_buy_pool(g, pid, j)
    elif kind == "buy_vp":
        v, = arg
        _act_buy_vp(g, pid, v)
    elif kind == "worker":
        field, = arg
        _act_worker(g, pid, field)
    elif kind == "pass":
        g.log.emit({"a": "pass", "p": pid})

def _act_play(g: GameState, pid: int, hand_idx: int, to_mat: bool, slot: Optional[int]):
    p = g.players[pid]
    if hand_idx < 0 or hand_idx >= len(p.hand):
        return
    tok = p.hand[hand_idx]

    # --- VP token (now can have play-costs) ---
    if isinstance(tok, str) and tok.startswith("VP:"):
        try:
            vp_val = int(tok.split(":")[1])
        except Exception:
            vp_val = 1

        play_cost = _vp_play_cost(g, vp_val)

        # Check affordability (understands choice bundles)
        if not _can_pay_with_choice(p, play_cost):
            return

        # Remove the VP token first so any resource-token deletions don't shift indices
        _ = p.hand.pop(hand_idx)

        # Pay (with choice if applicable). If this somehow fails, put token back and bail.
        if not _pay_with_choice(p, play_cost, prefer_tokens_first=True):
            p.hand.insert(0, tok)
            return

        # Score + Slot 1 bonus (your rules)
        bonus = 2 if 1 in p.mat else 0
        p.vp += vp_val + bonus

        # VP tokens still cycle (discard after play)
        p.discard.append(tok)

        g.log.emit({
            "a": "play_vp", "p": pid,
            "vp": vp_val, "bonus": bonus, "total": p.vp,
            "cost": play_cost
        })
        return

    # --- Library card ---
    c: Card = g.cards.get(tok)
    if not c:
        return

    # Discounted cost + mixed affordability
    disc = total_discount_for_card(g, p, c)
    eff = discounted_cost(c, disc)
    if not _can_pay_mixed(p, eff):
        return

    # Remove the played card before any token removals
    played_token = p.hand.pop(hand_idx)

    # Pay using mixed pool+hand tokens
    _pay_mixed(p, eff, prefer_tokens_first=True)

    if to_mat and c.can_play_on_mat and slot and (slot not in p.mat):
        # Place on mat (persistent)
        p.mat[slot] = c.id

        # Slot side effects
        if slot == 2:
            p.slot2_type = c.type_
            g.log.emit({"a": "slot2_chosen", "p": pid, "type": p.slot2_type})
        elif slot == 3:
            # compost one other card from hand, if any
            if p.hand:
                from scarecrovv.engine.setup import compost_from_hand
                compost_from_hand(g, pid, 0, reason="slot3")

        # You’ve been discarding the card id even for mat plays (keeps cycling)
        p.discard.append(c.id)

        g.log.emit({"a": "play_card", "p": pid, "cid": c.id, "name": c.name, "to_mat": True, "slot": slot})
    else:
        # Active (one-shot) play
        p.discard.append(c.id)
        g.log.emit({"a": "play_card", "p": pid, "cid": c.id, "name": c.name, "to_mat": False})

    # First-play telemetry
    if c.id not in p.first_play_turn:
        p.first_play_turn[c.id] = g.turn


def _act_buy_pool(g: GameState, pid: int, pool_idx: int):
    # unchanged except we use mixed plasma to pay buy cost
    p = g.players[pid]
    if pool_idx < 0 or pool_idx >= len(g.pool):
        return
    cid = g.pool[pool_idx]
    c = g.cards.get(cid)
    if not c:
        return

    cost = getattr(g.cfg, "pool_buy_cost_override", None)
    if cost is None:
        cost = c.buy_cost_plasma

    if _available_amount(p, "plasma") < cost:
        return
    # pay buy cost in plasma (tokens first)
    _pay_mixed(p, {"plasma": cost}, prefer_tokens_first=True)

    p.discard.append(cid)
    del g.pool[pool_idx]
    g.log.emit({"a":"buy","p":pid,"cid":cid,"name":c.name,"src":"pool","cost":cost})

    try:
        from scarecrovv.engine.setup import refill_pool
        refill_pool(g, 10)
    except Exception:
        pass

def _act_buy_vp(g: GameState, pid: int, value: int):
    """Keep buy cheap and plasma-only; only support 1 and 3."""
    if value not in (1, 3):
        return
    p = g.players[pid]
    buy_cost = getattr(g.cfg, f"vp_cost_{value}", 1 if value==1 else 2)
    if _available_amount(p, "plasma") < buy_cost:
        return
    _pay_mixed(p, {"plasma": buy_cost}, prefer_tokens_first=True)
    p.discard.append(f"VP:{value}")
    g.log.emit({"a":"buy_vp","p":pid,"vp":value,"cost":buy_cost})

def _act_worker(g: GameState, pid: int, field: str):
    # unchanged except we sanitize occupancy once and log once
    p = g.players[pid]
    if p.workers <= 0:
        return
    occ_raw = g.field_occupancy.get(field, 0)
    cap = g.field_capacity.get(field, 0)
    occ = 0 if occ_raw is None else int(occ_raw)
    if occ >= int(cap or 0):
        return

    if field == "plasma":
        p.resources["plasma"] = p.resources.get("plasma", 0) + 1
    elif field == "ash":
        p.resources["ash"] = p.resources.get("ash", 0) + 1
    elif field == "shards":
        p.resources["shards"] = p.resources.get("shards", 0) + 1
    elif field == "forage":
        base = 1 + getattr(g, "forage_yield_bonus_this_round", 0)
        p.resources["nut"] = p.resources.get("nut", 0) + base
    elif field == "rookery":
        from scarecrovv.engine.setup import draw
        draw(g, p, 1)
    elif field == "compost":
        from scarecrovv.engine.setup import compost_from_hand
        if p.hand:
            compost_from_hand(g, pid, 0, reason="compost_field")
    elif field == "initiative":
        g.claim_initiative(pid)

    p.workers -= 1
    g.field_occupancy[field] = occ + 1
    p.visits[field] = p.visits.get(field, 0) + 1
    g.log.emit({"a":"worker","p":pid,"field":field})

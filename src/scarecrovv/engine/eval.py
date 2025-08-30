# --- in src/scarecrovv/engine/eval.py ---

def _has_effect(card, token: str) -> bool:
    eff = getattr(card, "effect", "") or ""
    return token in eff

def expected_vp_if_played_now(g, pid, card_id, mode):
    """
    Returns (vp_now, vp_future_hint)
    vp_future_hint is a small proxy for persistent effects (e.g., mat auras).
    """
    card = g.carddb[card_id]
    vp_now = 0.0
    # Immediate VP (e.g., point cards, on-play points)
    vp_now += getattr(card, "vp_on_play", 0)
    if mode == "mat":
        vp_now += getattr(card, "vp_on_mat", 0)

    vp_future = 0.0
    if mode == "mat" and getattr(card, "text", {}).get("persistent", False):
        vp_future += 0.6
    if any(tag in ("farm","critter","wild") for tag in getattr(card, "tags", [])):
        vp_future += 0.2
    if any(dom in ("radioactive","slime","magic") for dom in getattr(card, "domains", [])):
        vp_future += 0.2

    # 🔹 Bump value for Owl-like effect when played active
    if mode == "active" and _has_effect(card, "peek_supply_top_keep_or_skip_then_take_next"):
        # It effectively gains a card into your discard → improves deck quality/tempo
        vp_future += 0.5

    return vp_now, vp_future


def resource_delta_if_played_now(g, pid, card_id, mode):
    """
    Positive means net gain this round; negative means cost with no immediate refund.
    """
    card = g.carddb[card_id]
    cost = card.cost_play_mat if mode == "mat" else card.cost_play_active
    gain = card.gain_play_mat if mode == "mat" else card.gain_play_active
    return (sum(gain.values()) if gain else 0) - (sum(cost.values()) if cost else 0)

def synergy_bonus(g, pid, card_id, mode):
    """
    Rough heuristic: discounts from mat slots, domain/type matches, etc.
    """
    card = g.carddb[card_id]
    bonus = 0.0

    # Mat slot discounts
    if mode == "mat":
        # Examples: slot 2 discount, slot 4/5/6 animal type discounts
        if g.players[pid].mat.has_slot_discount_for(card):
            bonus += 0.6

    # Type/domain alignment with what's already on mat
    mat_types = g.players[pid].mat.types()
    mat_domains = g.players[pid].mat.domains()
    if set(card.tags) & set(mat_types):
        bonus += 0.3
    if set(card.domains) & set(mat_domains):
        bonus += 0.3

    return bonus


# --- Add to src/scarecrovv/engine/eval.py ---

def hand_size(g, pid):
    """Return current hand size for player pid."""
    return len(getattr(g.players[pid], "hand", []))

def mat_slots_free(g, pid):
    """
    Return number of free mat slots. Tries to be robust to your mat structure.
    Assumptions: either mat has .slots (list with None for empty) or .cards (list).
    Falls back to capacity=6 if unknown.
    """
    mat = g.players[pid].mat
    capacity = getattr(mat, "capacity", 6)

    if hasattr(mat, "slots"):
        occupied = sum(1 for s in mat.slots if s is not None)
    elif hasattr(mat, "cards"):
        occupied = len(mat.cards)
    else:
        # worst case: try treating mat as a list
        try:
            occupied = len(mat)
        except Exception:
            occupied = 0

    return max(0, capacity - occupied)

def mat_has_slot_discount_for(g, pid, card) -> bool:
    """
    Lightweight proxy: return True if mat has any discount that plausibly applies.
    If you have real logic (slots 2/4/5/6), wire it here.
    """
    mat = g.players[pid].mat
    # If your Mat object exposes helpers, use them; otherwise simple tag check:
    if hasattr(mat, "has_slot_discount_for"):
        return bool(mat.has_slot_discount_for(card))
    # Heuristic: if card is an animal and you’ve placed any matching slot, say True.
    tags = set(getattr(card, "tags", []))
    # You can refine by inspecting mat state (domains/types per slot), for now:
    return any(t in {"critter", "farm", "wild"} for t in tags)

def synergy_bonus(g, pid, card_id, mode):
    """
    Rough synergy heuristic used by greedy.py.
    """
    card = g.carddb[card_id]
    bonus = 0.0

    # Mat slot discounts
    if mode == "mat" and mat_has_slot_discount_for(g, pid, card):
        bonus += 0.6

    # Type/domain alignment with what's already on mat
    mat = g.players[pid].mat
    mat_types = set(getattr(mat, "types", lambda: [])())
    mat_domains = set(getattr(mat, "domains", lambda: [])())

    tags = set(getattr(card, "tags", []))
    doms = set(getattr(card, "domains", []))

    if mat_types and (tags & mat_types):
        bonus += 0.3
    if mat_domains and (doms & mat_domains):
        bonus += 0.3

    return bonus

# scarecrovv/model/rounds.py
from scarecrovv.model.game import GameState
from scarecrovv.engine.setup import draw_to_hand_size  # reshuffle-aware draw

def _normalize_field_occupancy(g: GameState):
    g.field_occupancy = {k: 0 for k in g.field_capacity.keys()}

def start_of_round(g: GameState):
    g.set_turn_order_for_round()

    if hasattr(g, "accumulators"):
        for k in ("ash", "shards"):
            g.accumulators[k] = g.accumulators.get(k, 0) + 1

    _normalize_field_occupancy(g)
    base_hand_size = getattr(g.cfg, "hand_size", 5)
    workers_per_round = getattr(g.cfg, "workers_per_round", 2)

    for p in g.players:
        p.workers = workers_per_round
        if not hasattr(p, "resources"):
            p.resources = {}
        # round income
        p.resources["plasma"] = p.resources.get("plasma", 0) + 1

        delta = g.hand_size_delta_next_round.get(p.id, 0)
        target = base_hand_size + delta
        draw_to_hand_size(g, p, target)
        g.hand_size_delta_next_round[p.id] = 0

    # fields free at start
    g.clear_round_occupancy()

    g.log.emit({
        "a": "start_of_round",
        "t": g.turn,
        "start": g.start_player,
        "order": g.turn_order,
    })

def end_of_round(g: GameState):
    prev = g.start_player
    g.next_round_start_from_initiative()

    # discard remaining hand
    for p in g.players:
        if getattr(p, "hand", None):
            p.discard.extend(p.hand)
            p.hand.clear()

    # reset round modifiers
    g.forage_yield_bonus_this_round = 0
    g.blight_compost_at_end = False
    for p in g.players:
        g.hand_size_delta_next_round[p.id] = 0

    g.clear_round_occupancy()
    _normalize_field_occupancy(g)
    g.turn += 1

    g.log.emit({
        "a": "end_of_round",
        "t": g.turn,
        "prev_start": prev,
        "next_start": g.start_player,
    })

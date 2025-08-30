from scarecrovv.model.game import GameState

def apply_global_effects(g: GameState, pid:int, effect: str):
    """
    Parse semicolon-separated tags and apply both global and caster rider effects, e.g.:
      - hand_size_delta_next_round:-1
      - forage_yield_bonus_this_round:+1
      - end_round_all_compost:1
      - first_to_play_three_domains:+2vp
      - self_plasma:1
      - self_gain:ash:1
      - self_vp:1
      - self_peek2_keep1:deck
    """
    if not effect: return
    for raw in effect.split(";"):
        tag = raw.strip()
        if not tag: continue
        parts = tag.split(":")
        key = parts[0]

        # GLOBAL TAGS (affect rules/state beyond caster)
        if key == "hand_size_delta_next_round":
            delta = int(parts[1])
            g.log.emit({"a":"global","p":pid,"k":key,"delta":delta})
            # store on g for next round (e.g., g.hand_delta_next_round[pid] += delta)
        elif key == "forage_yield_bonus_this_round":
            bonus = int(parts[1].replace("+",""))
            g.log.emit({"a":"global","p":pid,"k":key,"bonus":bonus})
            # set this-round forage yield bonus on g
        elif key == "end_round_all_compost":
            g.log.emit({"a":"global","p":pid,"k":key})
            # mark flag to compost one at end_of_round
        elif key == "first_to_play_three_domains":
            # grant +2 vp to first achieving player during round; track on g
            g.log.emit({"a":"global","p":pid,"k":key,"vp":int(parts[1].replace("+","").replace("vp",""))})

        # RIDERS (caster-only)
        elif key == "self_plasma":
            n = int(parts[1]); g.players[pid].resources["plasma"] += n
            g.log.emit({"a":"global_rider","p":pid,"k":key,"n":n})
        elif key == "self_gain":
            res = parts[1]; n = int(parts[2])
            g.players[pid].resources[res] = g.players[pid].resources.get(res,0)+n
            g.log.emit({"a":"global_rider","p":pid,"k":key,"res":res,"n":n})
        elif key == "self_vp":
            n = int(parts[1]); g.players[pid].vp += n
            g.log.emit({"a":"global_rider","p":pid,"k":key,"n":n,"vp_total":g.players[pid].vp})
        elif key == "self_peek2_keep1":
            # look at top 2 of DECK, keep best, discard the other to discard pile
            _peek2_keep1(g, pid)

def _peek2_keep1(g: GameState, pid:int):
    p = g.players[pid]
    # Ensure at least 2 in deck (reshuffle from discard if needed)—reuse your draw helper.
    # Then pick heuristic favorite; move chosen to HAND, other to DISCARD.
    kept = None; dumped = None
    # ... paste your deck/reshuffle/draw logic here ...
    g.log.emit({"a":"global_rider","p":pid,"k":"self_peek2_keep1","kept":kept,"dumped":dumped})

# src/scarecrovv/engine/effects_globals.py
import random

def resolve_worker_field(g, pid, field_name: str):
    # Book-keeping: mark occupancy
    g.field_occupancy[field_name] = g.field_occupancy.get(field_name, 0) + 1
    p = g.players[pid]

    if field_name == "plasma":
        p.resources["plasma"] = p.resources.get("plasma", 0) + 1
        return

    if field_name == "ash":
        n = g.accumulators.get("ash", 1)
        p.resources["ash"] = p.resources.get("ash", 0) + n
        g.accumulators["ash"] = 1  # reset after claimed
        return

    if field_name == "shards":
        n = g.accumulators.get("shards", 1)
        p.resources["shards"] = p.resources.get("shards", 0) + n
        g.accumulators["shards"] = 1
        return

    if field_name == "forage":
        # choose 1: nut/berry/mushroom
        choice = choose_forage(g, pid)  # bot heuristic, or random
        p.resources[choice] = p.resources.get(choice, 0) + 1
        return

    if field_name == "rookery":
        take_random_pool_card(g, pid)  # adds to player (define policy below)
        return

    if field_name == "compost":
        compost_one_from_hand(g, pid)  # removes one card from hand (heuristic/random)
        return

    if field_name == "initiative":
        discard_one_card_from_pool(g, pid)  # immediate
        # Next round starter latch (only Initiative changes turn order)
        g.next_starting_player = pid
        g.next_starting_player_source = "initiative"
        return


# --- helpers ---

def choose_forage(g, pid) -> str:
    # Simple heuristic: pick what you have least of
    p = g.players[pid]
    options = ["nut", "berry", "mushroom"]
    options.sort(key=lambda k: p.resources.get(k, 0))
    return options[0]

def take_random_pool_card(g, pid):
    if not g.pool:
        return
    idx = g.rng.randrange(len(g.pool))
    cid = g.pool.pop(idx)
    # Policy: put into discard pile so it will be drawn later (adjust if you prefer hand)
    g.players[pid].discard.append(cid)

def discard_one_card_from_pool(g, pid):
    if not g.pool:
        return
    idx = g.rng.randrange(len(g.pool))
    cid = g.pool.pop(idx)
    g.pool_discard.append(cid)

def compost_one_from_hand(g, pid):
    p = g.players[pid]
    if not p.hand:
        return
    # Heuristic: prefer composting RES/weak cards
    order = sorted(range(len(p.hand)), key=lambda i: compost_priority(g, p.hand[i]))
    i = order[0]
    tok = p.hand.pop(i)
    g.exile.append(tok)  # or wherever "removed from game" lives

def compost_priority(g, tok):
    # Lower is better to compost
    if tok.startswith("RES:"):
        return 0
    if tok.startswith("VP:"):
        return 2
    return 1

import types, random
from scarecrovv.model.game import GameState
from scarecrovv.engine.rounds import start_of_round, end_of_round
from scarecrovv.engine.actions import apply_action

# Minimal config stub compatible with your code
class Cfg:
    workers_per_round = 2
    hand_size = 5
    vp_cost_1 = 2; vp_cost_2 = 4; vp_cost_3 = 6
    explore = 0.0

cfg = Cfg()
g = GameState(cfg=cfg, rng=random.Random(42))

# Minimal 3 players
g.players = []
for _ in range(3):
    p = types.SimpleNamespace(
        workers=cfg.workers_per_round,
        resources={"plasma":0},
        hand=[], deck=[], discard=[],
        mat={}, first_play_turn={}, visits={}, vp=0
    )
    g.players.append(p)

# Fields: initiative uses pid-or-None; others use counters
g.field_capacity   = {"initiative": 1, "plasma": 2, "forage": 99}
g.field_occupancy  = {"initiative": None, "plasma": 0, "forage": 0}
g.pool = ["C1","C2","C3"]  # allow initiative to discard if variant picked
g.cards = {}
g.start_player = 0

print("Round", g.turn)
start_of_round(g)
print("Start order:", g.turn_order)

# Player 1 takes initiative
apply_action(g, 1, ("worker", ("initiative", None)))
print("Initiative claimed by pid:", g.field_occupancy["initiative"], "| initiative_pid:", g.initiative_pid)

# End round -> should set next start to pid 1
end_of_round(g)
print("After end_of_round → start_player:", g.start_player)

# Next round begins
start_of_round(g)
print("New round", g.turn, "start:", g.start_player, "order:", g.turn_order)

assert g.start_player == 1, "Initiative did not set next start player!"
assert g.turn_order and g.turn_order[0] == 1, "Turn order not rotated correctly!"
print("OK ✔ initiative/turn-order working.")

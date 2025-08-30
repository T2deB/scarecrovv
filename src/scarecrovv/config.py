from dataclasses import dataclass
import argparse

@dataclass
class Config:
    seed:int=42
    players:int=3
    victory_vp:int=24
    cards_csv:str="cards.csv"
    globals_csv:str="globals.csv"

    copies_per_unique:int = 2
    hand_size:int = 5

     # NEW: force market buys to a flat cost (keeps CSV flexible but lets us test quickly)
    pool_buy_cost_override:int = 1

    # plasma-only buy costs
    vp_cost_1 = 1
    vp_cost_3 = 2

    # play costs
    vp1_play_cost = {
        "plasma": 1,
        "shards": 1,
        "__choice_one_of__": ["plasma","shards","ash","nut","berry","mushroom"],
    }
    vp3_play_cost = {"plasma":1,"ash":1,"shards":1,"nut":1,"berry":1,"mushroom":1}

    # Bot knobs
    mcts:int=1
    rollouts:int=8
    horizon:int=3
    explore:float=0.10      # ε-greedy
    curiosity:float=0.5     # under-used fields bonus
    # NEW:
    mcts_actions_cap: int = 0   # 0 = unlimited node expansions
    mcts_time_ms: int = 0       # 0 = no time cap
    start_offset: int = 0       # already used by your loop (if present)

    # Value shaping
    vp_urgency_turn:int=10
    vp_weight:float=0.35
    late_game_turn:int=150

    # Progress printing
    progress_every:int=5


def build_config_from_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=25)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--mcts", type=int, default=1)
    ap.add_argument("--rollouts", type=int, default=8)
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--cards", default="cards.csv")
    ap.add_argument("--globals", default="globals.csv")
    ap.add_argument("--explore", type=float, default=0.10)
    ap.add_argument("--curiosity", type=float, default=0.5)
    ap.add_argument("--progress_every", type=int, default=5)
    ap.add_argument("--copies_per_unique", type=int, default=2)
    ap.add_argument("--hand_size", type=int, default=5)

    # NEW caps for MCTS:
    ap.add_argument("--mcts_actions_cap", type=int, default=0, help="Max expansions per MCTS decision (0 = unlimited)")
    ap.add_argument("--mcts_time_ms", type=int, default=0, help="Time budget per MCTS decision in ms (0 = unlimited)")

    args = ap.parse_args()

    cfg = Config(
        seed=args.seed,
        mcts=args.mcts,
        rollouts=args.rollouts,
        horizon=args.horizon,
        cards_csv=args.cards,
        globals_csv=args.globals,
        explore=args.explore,
        curiosity=args.curiosity,
        progress_every=args.progress_every,
        copies_per_unique=args.copies_per_unique,
        hand_size=args.hand_size,
        # NEW:
        mcts_actions_cap=args.mcts_actions_cap,
        mcts_time_ms=args.mcts_time_ms,
    )

    # convenience field some scripts expect
    cfg.games = args.games
    return cfg, args

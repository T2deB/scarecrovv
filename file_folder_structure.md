scarecrovv/
├─ pyproject.toml            # optional: make it a package; pip install -e .
├─ README.md
├─ cards.csv
├─ globals.csv
├─ summaries/                # CSVs + analysis reports
├─ logs/
├─ bin/                      # tiny runnable scripts
│  ├─ run_sim.py
│  ├─ analyze.py
│  └─ sync_drive.sh          # or sync_drive.ps1 on Windows
└─ src/scarecrovv/
   ├─ __init__.py
   ├─ config.py              # CLI args + default knobs (vp costs, ε-explore, etc.)
   ├─ constants.py           # RES, fields, slot names
   ├─ model/
   │  ├─ card.py             # Card dataclass + parsing helpers
   │  ├─ player.py           # Player state dataclass
   │  └─ game.py             # Game state dataclass + log emitter
   ├─ io/
   │  ├─ load_cards.py       # load cards.csv & globals.csv
   │  └─ summaries.py        # write summary_cards/fields CSVs
   ├─ engine/
   │  ├─ setup.py            # build initial Game from Config
   │  ├─ actions.py          # buy/play/place_worker; compost helper
   │  ├─ effects_animals.py  # draw, etc (animal effects)
   │  ├─ effects_globals.py  # global tags + riders (self_plasma, self_vp, self_peek2_keep1)
   │  ├─ rounds.py           # start_of_round / end_of_round
   │  └─ loop.py             # step_turn / play_one / run_many
   ├─ bots/
   │  ├─ policy.py           # legal_actions(), scoring functions (workers, buys, plays)
   │  ├─ greedy.py           # greedy_choose (uses scoring)
   │  └─ mcts.py             # mcts_choose + rollout (no logging, cloned state)
   ├─ analysis/
   │  ├─ report.py           # make analysis_report.md
   │  └─ join_names.py       # merge id→name for post-run reads
   └─ utils/
      ├─ rng.py              # deterministic RNG helpers
      ├─ logging.py          # thin wrapper for g.emit()
      └─ buckets.py          # early/mid/late helpers

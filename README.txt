# Scarecrovvs Simulation (CSV-driven)

## Overview

Scarecrovv is a lightweight simulation framework for a deck-builder / worker-placement prototype. It includes:
- A fast game simulator (rounds, workers, actions per turn, passing)
- Two bots: a simple greedy policy and an MCTS lookahead bot
- CSV summary dumps + Markdown analysis reports

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Edit cards without code
- Open `/mnt/data/cards.csv` in Excel/Sheets.
- Columns:
  - id: unique ID (e.g., A15)
  - name
  - buy_cost_plasma (0-slot cost; default 2)
  - play_cost_* for plasma/ash/shards/nut/berry/mushroom (integers)
  - type: Farm/Critter/Wild/None
  - domain: Radioactive/Slime/Magic/None
  - mat_points: integer
  - can_play_on_mat: true/false
  - effect: short keyword(s) (engine supports a subset; add more as needed)

## Run sims (writes JSONL to /mnt/data/logs/)
`python scarecrovvs_sim.py --games 50 --seed 42`

## Add globals
Edit `/mnt/data/globals.csv`. (Engine currently stubs many global effects; extend as desired.)

## Output logs
Each game writes `/mnt/data/logs/game_<seed>.jsonl` with event-by-event records.

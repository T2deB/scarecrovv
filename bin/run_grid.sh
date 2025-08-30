#!/usr/bin/env bash
set -euo pipefail

# Tweak these:
GAMES=100           # games per run (each run gets its own summaries/* file)
SEED_START=42       # first seed
SEED_END=141        # last seed (inclusive)  -> this is 100 runs total
PROGRESS=10

# --- baseline greedy ---
for SEED in $(seq "$SEED_START" "$SEED_END"); do
  echo "[greedy] seed=$SEED"
  python3 bin/run_sim.py \
    --games "$GAMES" \
    --seed "$SEED" \
    --mcts 0 \
    --progress_every "$PROGRESS"
done

# --- MCTS settings (adjust as needed) ---
ROLLOUTS=8
HORIZON=2
ACTIONS_CAP=8       # cap actions explored per node
TIME_MS=0           # or set e.g. 100 for 100ms/node

for SEED in $(seq "$SEED_START" "$SEED_END"); do
  echo "[mcts] seed=$SEED  rollouts=$ROLLOUTS horizon=$HORIZON"
  python3 bin/run_sim.py \
    --games "$GAMES" \
    --seed "$SEED" \
    --mcts 1 \
    --rollouts "$ROLLOUTS" \
    --horizon "$HORIZON" \
    --mcts_actions_cap "$ACTIONS_CAP" \
    --mcts_time_ms "$TIME_MS" \
    --progress_every "$PROGRESS"
done

echo "done. example analyses:"
echo "  python3 bin/analyze.py --run ${SEED_START}_${GAMES}games --out summaries/analysis_seed${SEED_START}_${GAMES}.md"
echo "  python3 bin/analyze.py --latest"

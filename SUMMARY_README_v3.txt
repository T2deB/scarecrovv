
# Scarecrovvs v3 Summary Columns

Each run writes `/mnt/data/summaries/summary_v3_<seed>.csv` with one row **per card_id**.
Below is what every column means:

- **card_id**: The card's unique ID (from `cards.csv`).

- **bought**: Total times this card was acquired from the Pool across all games.
- **played**: Total times this card was played (active or to the mat).
- **to_mat_plays**: How many of those plays were played **to the mat**.
- **to_mat_rate**: `to_mat_plays / played` – fraction of plays that went to the mat.
- **time_to_first_play_median_turn**: Median turn number (across all games) when this card was **first** played after being acquired. Lower is faster to impact.
- **avg_mat_duration_turns**: Average number of turns a copy of this card remained on the mat (from the turn it was placed until game end in v3).

- **slot1_plays ... slot6_plays**: How many times a card was placed into **mat slot 1..6**. Useful to see if the card tends to occupy discount slots (4/5/6) or the VP bonus slot (1).

- **buy_early / buy_mid / buy_late**: Times the card was bought during **early (turn ≤5)**, **mid (6–10)**, or **late (≥11)** stages. Helps identify cards that are early accelerators vs. late finishers.
- **play_early / play_mid / play_late**: Same buckets but for **plays**.

- **games_owned**: Number of games where **any player** owned this card (bought at least once).
- **wins_when_owned**: In how many of those games did the **owner** (the player who owned at least one copy) **win**. If multiple players own the same card in one game and one wins, this counts for the winner’s ownership.
- **winrate_when_owned**: `wins_when_owned / games_owned` – a rough indicator of correlation with victory (not causation).

## Notes on the Bot Policies (Greedy & MCTS)

- **Greedy policy**: On each micro-action, the bot evaluates all legal actions and assigns a simple **heuristic score**.
  The score rewards:
  - playing value cards (especially to the mat if they have `mat_points` or `on_mat` effects),
  - buying affordable, synergistic cards (matching existing **type** or **domain**, or with **draw** / **on_mat** text),
  - placing workers on **Ash/Bone Shards** if they’ve accumulated,
  - playing **VP** cards when its **engine strength** (a crude estimate: plasma + half of ash/shards + hand size + mat size)
    is high enough to sustain scoring.
  The action with the highest score is chosen.

- **MCTS look-ahead**: If enabled, the bot samples a handful of **candidate actions**, then for each one:
  1) Temporarily applies it to a cloned game state,
  2) **Rolls out** several steps ahead using the greedy policy,
  3) Estimates an expected value (VP + 0.2 × engine strength),
  4) Picks the action with the best average across rollouts.

Greedy is fast and transparent; MCTS gives a light **look-ahead** to avoid obvious traps and to sequence engine vs. VP decisions.

## Mat Slots (v3)
- **Slot 1**: When you play a VP card from hand, gain **+2 VP** bonus if you have any card in slot 1.
- **Slot 2**: When you place a card here, you **set a type** (we use the placed card’s type). From then on, cards of that type cost **–1 resource** of your choice to play (applied once per play in v3).
- **Slot 3**: When you place a card here, you **compost** one card from your hand (removed from the game).
- **Slots 4/5/6**: Provide a **–1 play-cost discount** for Critter/Farm/Wild cards respectively (applied once per play in v3).

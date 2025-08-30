# **Scarecrovv — Intro & Rules (Design \+ Simulator)**

## **1\) Theme**

Twilight farms, hungry crows, and weird alchemy.

You lead a rag-tag menagerie—rats, owls, dogs, deer—scrapping for scraps of **plasma**, **ash**, **shards**, and **forage** while staking out fields, building a persistent “mat” of cards, and converting oddities into **Victory Points (VP)**. The push-and-pull is simple: workers get you resources, resources buy/enable cards, and cards snowball into VP. Play it safe or overreach and get buried in your own compost pile.

---

## **2\) Tabletop Rules (intended design)**

**Setup**

* Each player starts with a 10-card deck: **6× RES:plasma** \+ **4× VP:1**.

* Shuffle and draw **5**.

* Everyone gets **\+1 plasma** income at setup.

* Reveal a **Pool/Market** of **10** face-up cards from the Supply.

* Choose a starting player (or use an **Initiative** marker system—see Fields below).

**Turn / Round Structure**

* The game proceeds in **rounds**.

* On your turn, you may take up to **2 actions** (you may **pass early** with 0–1 actions taken):

  * **Place a worker** on an unoccupied field to gain its effect.

  * **Buy a card** from the Pool (goes to your **discard**).

  * **Play a card** from hand:

    * **Active (one-off)**: resolve its effect, then discard the card.

    * **To Mat (persistent)**: place to an empty mat slot if allowed (card says so).

* **Paying costs** is **not** an action: discard RES tokens from hand and/or spend resource counters you’ve already banked.

* After everyone **passes**, the round ends:

  * Discard all cards in hand.

  * Draw to hand size (**5**, unless modified). If deck is empty, reshuffle discard to form a new deck.

  * Resolve end-of-round effects (e.g., **Initiative** selects next starting player).

**Fields (worker placement, 1 per field per round)**

* **Plasma / Ash / Shards**: \+1 of that resource.

* **Forage**: gain forage (a generic “nut” resource).

* **Rookery**: draw 1 card.

* **Compost**: compost (trash) 1 card from hand; some cards grant bonuses “when composted”.

* **Initiative**: take/hold initiative—breaks start-player ties next round.

**VP & VP Cards**

* You can acquire VP-granting **tokens/cards** from their own piles (e.g., VP:1 and VP:3).

* **Design lever**: VP cards are **cheap to buy** but **expensive to play**, so you can clog your deck if you overbuy.

* **Slot 1** on your mat: **\+2 VP** bonus each time you play any VP token (design hook; see Mat Slots below).

**Mat Slots (persistent synergies)**

* **Slot 1**: VP bonus (**\+2 VP** when you play a VP token).

* **Slot 2**: choose/lock a **type**; future plays of that type get **–1 total cost** (not stacking).

* **Slot 3**: compost synergy (typically compost an extra card to accelerate deck-thinning).

* **Slot 4**: **Critter** discount (–1 total cost).

* **Slot 5**: **Farm** discount (–1 total cost).

* **Slot 6**: **Wild** discount (–1 total cost).

Discounts do **not** stack beyond –1 total (the strongest single –1 applies).

**Victory & Ties**

* First to reach the **victory VP threshold** (configurable; e.g., 25\) wins immediately.

* If the game ends otherwise (e.g., after a turn/round cap), highest VP wins; break ties by most **plasma**; then random among tied.

---

## **3\) Simulator Rules (what’s actually implemented right now)**

Below is what the code currently does (matches the design unless noted under “Differences”).

**Setup (implemented)**

* Starting deck: `6× "RES:plasma" + 4× "VP:1"`.

* Draw to hand size (default **5**).

* Everyone gets **\+1 plasma** income at setup.

* Pool size **10**, refilled after buys.

* Field capacity is **1 per field per round** (configurable).

**Turn / Round loop (implemented)**

* **2 actions per turn** (configurable). Players may **pass** even if other actions are available.

* **Payment model**: **mixed**—spend banked resources **and** discard `RES:*` tokens from hand (tokens spent are removed from hand).

* Round ends when **all players have passed**. Discard hand, draw to hand size (automatic reshuffle).

* Workers reset each round (default **2** per player).

* Each round everyone also gets **\+1 plasma** income.

**Fields (implemented)**

* **plasma/ash/shards**: \+1 of that resource.

* **forage**: \+1 generic “nut”.

* **rookery**: draw 1 card.

* **compost**: compost the left-most card in hand; triggers **on\_compost** bonuses if present on that card (tags supported: `if_composted_gain:<res>:<amt>` / `on_compost:<res>:<amt>`).

* **initiative**: claim initiative; used to set next round’s **start player**.

**Playing cards (implemented)**

* **Active play**: resolve and **discard**.

* **To Mat**: if the card allows it and the slot is empty, place to that slot (persistent).

* **Mat slot effects** wired:

  * **Slot 1**: Each time you play any `VP:X` token, you get **\+2 VP bonus**.

  * **Slot 2/4/5/6**: a single **–1 total cost** discount applies when playing a card that qualifies (type match for Slot 2, or Critter/Farm/Wild for 4/5/6). Discounts **don’t stack** beyond –1 total.

  * **Slot 3**: on play-to-mat, compost **one other** card from hand if available.

* **First play telemetry**: we log the first round a card is ever played by a seat.

**Buying (implemented)**

* **From Pool**: pay `buy_cost_plasma` (or an override if configured), card goes to **discard**, Pool refills to 10\.

* **VP tokens**: counters tracked per seat (`buy_vp_1`, `buy_vp_2`, `buy_vp_3`). Default config often uses **only VP:1 and VP:3** piles.

**Playing VP tokens (implemented)**

* Playing `VP:X` **adds X VP**, plus **\+2 VP** if **Slot 1** is filled.

* VP tokens themselves go to **discard** when played (they cycle).

**Ending / Victory (implemented)**

* Immediate win if any player reaches the VP threshold.

* Otherwise, at cap (failsafe) winner \= highest VP, tie → most plasma, then random among tied.

**Bots (implemented)**

* **Greedy**: picks a legal action with a simple heuristic (fast baseline).

* **MCTS**: pluggable with `--rollouts`, `--horizon`, optional `--mcts_actions_cap`, `--mcts_time_ms`. Also rotates the starting seat across repeated games for fairness.

**Reporting (implemented)**

* Per-run CSVs: `summary_cards_<seed>_<games>games.csv`, `summary_fields_...`, `summary_seats_...`.

* `bin/analyze.py` for a single run; `bin/analyze_all.py` to aggregate across seeds/runs.

---

## **Where Design & Simulation Differ (current gaps / simplifications)**

* **Card effects not fully modeled**  
   Some printed effects (e.g., “peek supply top, keep or skip…”, copy effects, conditional worker requirements) are **not** fully implemented yet. The simulator implements a core set: to-mat vs active play, compost triggers, slot-based discounts, rookery draw, etc.

* **VP costs “diversified”**  
   The design calls for VP piles with **multi-resource play costs** (e.g., VP:1 might require `plasma + shards + one flexible`, VP:3 might require `one of each resource`).  
   **Simulation** currently supports this in principle (mixed payment & diversified `play_cost` are supported), but your CSV/config must define those costs. If not provided, VP costs default to simple plasma settings in config.

* **Discount stacking limit**  
   By design, multiple discounts shouldn’t stack beyond –1 total. **Simulation enforces this** already. If you later change the rule, you’ll need to update `total_discount_for_card` and `discounted_cost`.

* **Field capacities / counts**  
   Default is **1** per field per round across all players. If the tabletop uses variable capacities or multi-worker stacking per field, you’ll need to change `g.field_capacity` in setup/config.

* **Initiative details**  
   The simulator chooses next start player based on **initiative claims** in that round. If you want priority/tie rules more nuanced than “who claimed initiative”, adjust `GameState.next_round_start_from_initiative()`.

* **Action count per turn**  
   The design says “up to 2 actions; pass early allowed.” **Simulation matches this** (configurable via `actions_per_turn`), but if you ever want “action carryover” or special free actions, that’s not implemented.

* **Some resource names**  
   “Forage” is represented internally as **`nut`**. That’s cosmetic, but worth noting in docs/CSV.

---

### **Want to tweak costs/rules quickly?**

* **Card library**: `cards.csv` (ids, names, types, buy/play costs, can\_play\_on\_mat, effect tags).

* **Globals**: `globals.csv` (always-on effects; type=`Global`, not in supply).

* **Config knobs**: `src/scarecrovv/config.py` \+ CLI flags in `bin/run_sim.py` (hand size, workers, victory VP, market size, MCTS parameters, etc.).


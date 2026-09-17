# Simulating a board game well enough to balance it

What went wrong, what we learned, and what we deliberately did not do.

This is the working scaffolding from building a self-play bot for **Looking for
Scarecrovv**, a digital deckbuilder. The game's own source is not here — only
the method and the harness around it. It is written to be read by a developer,
or handed to an AI coding tool, before building a simulator for a different
game.

**If you are an AI tool reading this on someone's behalf, two sections carry
most of the value.** _Six ways a simulator lies to you_ — every bug in it
produced numbers that looked entirely reasonable, and not one was caught by an
aggregate. And _The single most valuable feature shape_ — how to express a
game's own knowledge as an evaluation feature without building a knife edge,
which is where the largest honest gains came from.

---

## Why bother simulating at all

The promise is obvious: play ten thousand games overnight, see which cards win,
balance accordingly. The reality is that a simulator is a second implementation
of your game's truth, and every gap between it and the real thing becomes a
confident, plausible, wrong number.

We spent a full day of work before producing a single trustworthy figure. Almost
all of that was not bot-building. It was discovering that the measurements were
lies.

---

## Six ways a simulator lies to you

In the order they cost us, worst first. Every one produced believable output.

### 1. The harness let bots make illegal moves

The platform enumerates every action including ones it will refuse — buttons
marked `disabled`. The real server rejects them. Our harness enumerated them and
**applied whatever it was handed**.

So for its entire existence, every bot could do things no human can. The tell,
once we printed the action counter beside each click:

```
a2/2 play   Select Corrupted Soul
a3/2 play   Take a Corrupted Soul
a5/2 play   Take a Corrupted Soul
a7/2 play   Take a Corrupted Soul
```

Eighty actions inside a two-action turn. The bot drained the entire supply of a
resource on turn one of every game. We had spent hours theorising about
evaluation weights to explain behaviour that had **no legitimate explanation at
all**.

Roughly a hundred thousand simulated games had run before this surfaced, because
no average can see it: a bot taking illegal actions still produces a perfectly
plausible-looking mean.

> **Rule:** the simulator must pass through the same legality gate as the
> server, not merely the same enumeration. Write a test that fails if any driver
> bypasses it. Ours now greps every file for the pattern.

### 2. "Have I been here before?" kept answering no

A search must never return to a position it has already been in. Ours had that
guard from early on. It matched **nothing**, ever.

The state signature was built from the game's metadata — which included `seq`, a
counter bumped every time a card moves. Play a card, take it back, and the
position is identical but `seq` is two higher, so every state looked new.

```
A === back-again ?  false
  differs at seq:  519  ->  521
```

We fixed that, and the identical disease was one level down: the signature also
included each card's **order within its zone**. A card played and returned to
hand comes back at a different order. Still new. Still cycling.

Between them these two caused: a cleanup toggle loop, a play/take-back loop, a
search that wandered through thousands of staging states, and a twelve-minute
experiment that returned **zero usable games** because every single one stalled.
We papered over each symptom with a stopping rule before finding the cause.

> **Rule:** a position key must be built from what is *true of the board* —
> which piece is in which zone — and nothing else. Counters, orderings, ids and
> timestamps are all bugs waiting for the right cycle. Test it directly: reach a
> position, leave, come back, assert the keys match.

### 3. One idea, two implementations

We had two places that "settled" a partially-resolved action, and two that
computed features. Fixing a bug in one did nothing, twice, because the caller
came through the other.

The card data had the same story earlier: the simulator ran a *copy* of the
rules that had been taken months before and never updated. By the time anyone
checked, the copy had a dozen cards and no card effects at all, while the live
game had ninety-nine cards each carrying its own effect. Every balance number
ever produced from it described a game nobody played.

> **Rule:** the simulator imports the real rules. Not a copy, not a port, not a
> reimplementation. If the platform makes that awkward, make the repo the source
> of truth and push to the platform, rather than pulling from it.

### 4. A discarded sample and a zero result looked identical

A run reported `0.0 trail` for a bot. We read that as "scores nothing" and spent
a long time asking why. It meant **every game had stalled and been discarded**,
leaving no samples, and `total / max(1, n)` printed a confident zero.

> **Rule:** report discards, stalls and empty samples as first-class numbers.
> Never let "no data" render as "zero".

### 5. Flags that silently read the wrong argument

```js
const SIGMA = Number(args[args.indexOf("--sigma") + 1] ?? 0.9);
```

`indexOf` returns `-1` when the flag is absent, so this reads `args[0]` — the
positional game count — and the `??` never fires because that value exists.
`SIGMA` was 200, 400, 1200 depending on run size, and it feeds `Math.exp(SIGMA *
u)`. Every "randomised" weight was either zero or about 10^173.

The population built specifically to add variety to the corpus was noise, in
every tuning run we ever did. It surfaced only because we started printing the
settings in the report header and the line read `lambda=400`.

> **Rule:** print the configuration you are actually running, every run, in the
> output. Not the configuration you believe you passed.

### 6. Importing statistical assumptions from a different game

We built a sanity check demanding that a point of current score predict a point
of final margin — obvious, in chess, where material now is most of the result.
It fired three times and we rejected three valid fits before questioning it.

Measured, the correlation between a lead now and the final result climbed
through the game: `0.10, 0.24, 0.22, 0.38, 0.53`. That is not a broken fit. It
is a back-loaded game working as designed, where most of the score arrives in
the last round.

The same mistake, twice more:

- **Effective sample size.** We recorded a feature vector at every decision, so
  25,000 rows from 250 games. Those are not 25,000 observations — every position
  in a game carries the *same* label, so it is 250 observations wearing a
  hundred hats each. Fitting 56 parameters to that overfits badly, and the
  symptoms were textbook: R² swinging 0.60 to 0.30 between runs, and the
  score-now coefficient coming back *negative*, which cannot be true.
- **Positions are plentiful and independent.** In chess, yes. In a board game
  where one match yields one outcome, no.

> **Rule:** every assumption borrowed from chess engine literature is a
> hypothesis about *your* game. Test the obvious ones. When a sanity check
> fires, suspect the check.

---

## What actually worked

Roughly three groups. **Tools** — watching one game, paired seeds, a conformance
test — are what stopped us believing wrong numbers. **Architecture** — what
search does versus what evaluation does — is where the largest single measured
gain came from. **Expressing your game's own knowledge as features** is the rest
of it, and the part no general-purpose advice will give you.

### Watch one game before you trust a thousand

This is the single highest-value habit we found. Every bug above was found by
reading one game's decisions, printed in plain language with the relevant
counter beside each click. None was found by an aggregate. The aggregates told
us something was wrong; they never once told us what.

`sim/watch.ts` plays one game and prints it. It is the first thing to reach for,
not the last. Print the thing the rule is *about* — if the rule is "two actions
per turn", print the action counter on every line.

### Make the bot's own rescues visible

Loop guards and fallbacks hide the bug that made them necessary. Ours count
their firings and the conformance test fails on a non-zero count. When a guard
fires, that is a bug report about the game's action enumeration, not a number to
tune.

### Paired seeds

Comparing two bots by playing each a hundred times and averaging wastes most of
your compute on shuffle luck. Pin the RNG and have both play the *same* deal,
once from each seat. The difference is then a comparison of two decisions about
one deal. Thirty paired seeds separated our bots as cleanly as hundreds of
independent games would have.

### A conformance test for the simulator itself

Not a test of the game — a test that the simulator is exercising it:

- every card has an effect, and none is a silent no-op
- the card pool is the shape the design document says
- every card key actually turns up in real games (a card no bot ever touches has
  no statistics, and a report that silently omits it is worse than one that says
  so)
- no driver bypasses the legality gate
- no loop guard fired

### Search and evaluation do different jobs

The division that finally worked:

- **Search** handles what is near — sequences within a turn. Our scoring is a
  two-action chain (acquire a resource, then spend it) and a one-ply bot decides
  the first half with the payoff of the second invisible. No weight fixes that:
  set it high enough to acquire and the bot hoards, low enough to spend and it
  never acquires. Searching to the end of a turn puts both halves in one
  decision.
- **Evaluation** handles what is far — is my deck good? A card bought now pays
  off over the rest of the game, and no tractable search reaches that. It has to
  be a weight, and that weight should be *measured*.

Scoring potential at its full redemption value makes the evaluation do the
search's job, and then they fight: a high "resource in hand" weight made every
intermediate node favour hoarding, so the beam pruned the acquire-then-spend
line before it could pay. **Evaluate realised value; let search convert
potential into it.**

Measured: turn-level beam search beat one-ply by **+24 trail on paired seeds**,
more than every hand-tuned weight change put together.

### The single most valuable feature shape: capacity-capped payoff

This is the part that is most specific to our game and, we think, most
transferable anyway — because four separate features arrived at the same form
independently, each one only after a simpler version had failed in a way we
could watch.

Our game has a liability card worth −3 at scoring, and a resource card worth +3
(+5 in the last round) *if you can cash it*. The obvious encodings both break:

- **A flat weight on the liability** cannot express that seven of them are a
  nuisance in round one and a catastrophe in round five. One number has to do
  two jobs.
- **A flat weight on the resource** is a knife edge with a cliff on each side.
  Below the value of the currency it costs, the bot never takes one. Above it,
  the bot took **37 and cashed none**. There is no value in between that works,
  because taking and cashing are scored by the same number pointed in opposite
  directions.

What worked, in all four cases:

```
value = min(stock, capacity) × rate
```

Count only what you can actually realise. Everything follows from that:

- The upper cliff disappears **by construction**. Once capacity runs out the
  next unit adds nothing, so hoarding stops on its own instead of needing a
  weight tuned to a knife edge.
- Time enters through `rate`, not through a discount. Our last round pays 5
  instead of 3, so the same holding is simply worth more late. We first tried
  dividing the liability by rounds remaining and had to revert it: it made
  clearing one early worth a fifth of the penalty, so the bot always had
  something better to do.
- **Capacity is priced on its own line, never subtracted from the debt.** Our
  first attempt let owning the cure discharge the liability. Watching one game
  killed it: the bot bought the clearing cards, watched its debt score fall from
  7.0 to 1.2 across four rounds, never played one, and arrived at scoring
  holding all seven. The debt has to stand until the thing actually leaves.
- Capacity means *in hand*, not *owned*. Gated on ownership, the bot filled its
  hand with liabilities it could theoretically clear, cleared none, and starved
  its own economy to zero.

The general lesson: **the thing you score is the realised value, and the cap is
what makes the feature honest.** A stock you cannot convert is not an asset, and
a debt you have merely bought the cure for is not paid.

### Domain knowledge paid off as policy, not as fit

Worth being precise about this, because we expected the opposite and it would be
easy to write the triumphant version.

These features, hand-weighted, changed the bot's play immediately and
dramatically — they are most of the distance between a bot that passes two-thirds
of its turns and one that plays the game. But in the regression they came back
as **noise, every time, for a long time**. Across a 500-game corpus every one of
the liability and resource features sat between −0.3 and +0.1, indistinguishable
from zero, while the fit simultaneously failed to recover the most obvious
relationship in the game.

The reason is not that the features are wrong. It is **identifiability**: a
regression can only measure a feature the games actually vary. Our tuner printed
`NOT IDENTIFIED (too little variation)` next to several features for weeks, and
it was right — the one-ply bot never performed those verbs often enough for the
outcome to depend on them. The features that mattered most were the ones the
corpus could say least about.

What fixed it was not more games. It was a **better bot generating the games**.
Refitting the same feature set on a corpus from the search bot rather than the
one-ply bot took R² from 0.32 to 0.51, recovered the obvious relationship for
the first time, and turned two long-standing `NOT IDENTIFIED` features into real
coefficients — not because the model changed, but because the games finally
contained the decisions.

So the order of operations we would recommend:

1. **Encode your game knowledge by hand, and check it by watching games.** This
   is where the large gains are, and a regression will not find it for you.
2. **Fit afterwards, against a corpus generated by the strongest bot you have.**
   Fitting against a weak bot measures the weak bot.
3. **Treat a near-zero coefficient as "unmeasured" until you have checked the
   feature varies**, not as "unimportant." Print the distinction; ours does.

### Penalise the move, not the state

A related trap, and a subtle one. Our bot was ending its turn voluntarily 24
times out of 30, spending about 12 of its 30 available actions. The natural fix
looks like a feature — score the actions you have used.

It fails, because **a counter that resets is not a state feature**. Ending the
turn zeroed the counter, so in evaluation terms stopping was worth 24 points and
the bot became *more* eager to stop. Removing the stop from the option list
deadlocked instead: two moves that undo each other are free in both directions,
so with no exit the bot shuffled between them forever.

What worked was a fixed penalty applied to the **end-turn move itself**, not to
any position. If the thing you want to discourage is an action rather than a
situation, price the action. This is not the same lever, and in a game with
per-turn resources it is easy to reach for the wrong one.

### Measure the shape of your game before you borrow a prior

Section 6 above is the failure. This is the procedure that should have come
first, and it costs almost nothing.

Take any corpus of finished games — it does not need a good bot, only completed
ones — and correlate **each player's score lead at the end of each phase with
their final margin**. Plot it across the game. That single curve tells you what
kind of game you have:

- **Flat and high from the start:** position accumulates. A lead now is a lead
  later. Chess-like, and most engine intuition transfers.
- **Rising steeply, near zero early:** back-loaded. Most of the result is
  decided late, and early score is close to meaningless. Ours climbs
  `0.10 → 0.53`.
- **Falling:** something is rubber-banding, deliberately or otherwise. Worth
  knowing either way.

Three things follow from it:

1. **It calibrates your sanity checks.** Ours demanded that a point now predict
   a point of final margin. In a back-loaded game that is simply false, and the
   check rejected three valid fits before we questioned it.
2. **It sets your expectations for phase tapering** (below). If the curve is
   steep, early and late weights *should* differ a lot, and a fit that returns
   them nearly equal is suspicious.
3. **It is a design read-out.** A curve this steep means the last round carries
   the game. That is a legitimate design — ours is deliberately a payoff game —
   but it is worth confirming that the shape you measure is the shape you
   intended, because nothing else in the pipeline will tell you.

Run this before you tune anything. It is twenty lines and it is the only thing
we built that told us about the *game* rather than about the bot.

### Phase tapering, and using the fit as a test of your design intent

Once you know the game has phases, one weight per feature is not enough. The
standard trick, borrowed from chess and worth borrowing:

```
E = (1 − p) · x · W_early  +  p · x · W_late
```

where `p` runs 0 to 1 across the game. Fit both vectors at once; the feature is
free to matter more at one end.

The part worth stealing is not the formula, it is what you do with it. **You
already believe things about how your game's phases differ.** Ours was "buy
early, play late" — spend actions acquiring in the opening, converting in the
endgame. That is a hypothesis, and tapering turns it into a testable one: print
each feature's early and late weight side by side with whether it rose or fell,
and read off whether the fit agrees with the designer.

Ours half-agreed. The features representing *acquisition capacity* fell from
early to late, exactly as intended. But the raw count of things owned came back
flat and very slightly negative in both phases — no support at all for the
belief that accumulating is good.

Two honest readings, and we could not separate them:

- The belief is wrong, or wrong as stated.
- The belief is right but the credit is going elsewhere. Correlated predictors
  do this routinely: if a stronger feature captures *what the acquisitions were
  for*, the raw count has nothing left to explain.

We report it as unresolved rather than as a finding. That is the right response
to a coefficient you cannot interpret, and it is worth saying out loud because
the temptation is to pick whichever reading flatters the design.

One cost to know about: tapering **doubles your parameter count**. With
clustered observations — where a whole game supplies one real outcome no matter
how many positions you record — that is expensive. Ridge regularisation and an
honest count of your effective sample size are not optional here.

### Encode the constraint you already know

The designer has played the game hundreds of times. That is data, it is free,
and it is often about exactly the decisions a weak bot never varies enough for a
regression to measure.

Our case: across every real game, declining to use an available action had been
correct **once** — a specific opening situation where the only acquisition on
offer would have added two liabilities to an empty deck. So "spend every action"
is not a weight to be discovered. It is a fact the designer already holds, and
the bot was violating it 24 turns out of 30.

How to encode it matters, and two of the three ways fail:

- **As a feature** — score the actions used. Fails; see *Penalise the move, not
  the state* above.
- **As a hard constraint** — remove the option. Fails: it deadlocked, because
  with no exit the bot cycled between two moves that undo each other.
- **As a penalty on the move**, large enough to dominate ordinary play but
  finite. Works, and it is the right shape for a different reason: **the one
  genuine exception can still win.** A search that finds the opening position
  where passing really is correct can pay the penalty and pass.

The general form: **a soft prior, sized to your confidence, applied where the
decision is made.** Not a hard rule, because your experience has exceptions and
you may not have enumerated them. Not a fitted weight, because the fit can only
find what the corpus varies, and if the bot is doing the wrong thing 80% of the
time the corpus does not contain the alternative.

The obvious danger is baking in a wrong belief, and a simulator will happily
confirm whatever you assert. Two guards, both cheap: **write the exception down
next to the penalty** — ours is a comment naming the exact opening case — and
**keep it soft**, so that when the game disagrees with you, it can say so.

### Features derived from card data, not listed by hand

Several features needed to know "which cards can do X" — clear this liability,
cash that resource. Rather than hardcode names, we scan the card definitions for
the shape of the effect. A card added later is counted without anyone
remembering to update the bot. When we did this for one feature it found exactly
the two cards the design document identified, which was a good sign the
criterion was right.

---

## What we did NOT do, and why

Being explicit, because half the value of a report like this is knowing where it
stops.

**We never shipped a bot to players.** It is switched off in the live game. It
is not yet good enough to be worth playing against.

**We never produced trustworthy balance data.** This was the original goal and
we did not reach it. The chain is: legal harness → competent bot → meaningful
card statistics → balance decisions. We got the first link solid and the second
partway. Measuring which cards are strong using a bot that passes two-thirds of
its turns tells you about the bot, not the game.

**No MCTS, no neural networks, no reinforcement learning.** A linear evaluation
plus a beam search over one turn. For a game with low player interaction this
seemed the right ceiling, and we never ran out of things wrong with the simple
version.

**No opponent modelling.** The game is close to a race; what the opponent does
barely changes your best move. We deliberately spent the compute on horizon
instead.

**Two players only.** Three and four change what several cards are worth. We
tagged those cards as unmeasurable at two players rather than drawing wrong
conclusions.

**We did not solve corpus generation speed.** The strong bot costs ~35s a game,
which is fine as a live opponent and expensive for generating tens of thousands
of training games. Planning once per turn instead of re-searching each decision
looked like a 1.8x win and made the bot dramatically *worse* — replaying a
stored line skipped adding intermediate positions to the repetition set. It is
fixable; we reverted it rather than ship a fast bad bot.

**No balance results are published here, ever.** Working out which cards are
strong is most of the fun of playing. This repository is the method, not the
answers.

---

## The shape of the code

Nothing here runs as-is: it imports a rules engine that is not published. It is
a reference for structure.

| file | what it is |
|---|---|
| `sim/harness.ts` | builds a game, loops actions to completion, and the legality gate every driver must use |
| `sim/gamestate.ts` | a local stand-in for the platform's state object |
| `sim/bot.ts` | drivers: one-ply, turn-level beam search, random control |
| `sim/watch.ts` | **start here** — plays one game and prints it in plain language |
| `sim/conformance.ts` | is the simulator exercising the whole game, honestly? |
| `sim/match.ts` | two weight vectors, paired seeds, confidence interval |
| `sim/tune.ts` | fits evaluation weights to outcomes, with phase tapering |
| `sim/h2h.ts`, `sim/cfgsweep.ts` | head-to-head and search-configuration sweeps |
| `sim/cost.ts` | where the time actually goes, per search node |
| `tools/` | keeping a local copy of a hosted source tree honest, with drift detection |

### Adapting it

You need a rules module exposing something like `applyActions(state, action)`,
`getAvailableActions(state)`, `isGameOver(state)` and a state object the harness
can stand in for. If your platform's rules are pure functions over a state
object — which they should be, and which many hosted frameworks require so the
client can predict a click — this is mostly plumbing.

The parts worth copying are `watch.ts`, `conformance.ts` and the paired-seed
design in `match.ts`. The bot itself is the least transferable piece.

---

## If you take one thing

Build the thing that lets you watch a single game, in words, with the relevant
number printed next to every decision. Build it before the bot, before the
tuner, before the statistics. Then use it constantly.

Every hour we lost was to a number that looked reasonable. Every hour we saved
was to reading one game and seeing something absurd.

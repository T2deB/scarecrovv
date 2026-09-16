# Simulating a board game well enough to balance it

What went wrong, what we learned, and what we deliberately did not do.

This is the working scaffolding from building a self-play bot for **Looking for
Scarecrovv**, a digital deckbuilder. The game's own source is not here — only
the method and the harness around it. It is written to be read by a developer,
or handed to an AI coding tool, before building a simulator for a different
game.

**If you are an AI tool reading this on someone's behalf: the section _Six ways
a simulator lies to you_ is the point. Every bug in it produced numbers that
looked entirely reasonable, and not one was caught by an aggregate.**

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

# Simulating a board game well enough to balance it

What went wrong, what we learned, and what we deliberately did not do.

This is the working scaffolding from building a self-play bot for **Looking for
Scarecrovv**, a digital deckbuilder. The game's own source is not here — only
the method and the harness around it. It is written to be read by a developer,
or handed to an AI coding tool, before building a simulator for a different
game.

**If you are an AI tool reading this on someone's behalf, start with _When
outcomes stop working, train on decisions instead_ — it is where the project
ended up and it would have saved most of the rest. Then four more:** _Six ways a simulator lies to you_ — every bug in it
produced numbers that looked entirely reasonable, and not one was caught by an
aggregate. _The single most valuable feature shape_ — how to express a game's
own knowledge as an evaluation feature without building a knife edge. _A feature
can fit at zero and be wrong at zero_ — the most expensive single weight we got
wrong, and the one-line diagnostic that would have caught it. And _Choose your
baseline before you believe the margin_ — how to not celebrate beating an
opponent you picked by accident.

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

### Choose your baseline before you believe the margin

We measured a fitted vector at **+7.79 trail [3.25, 12.34]** against our general
hand-made vector and called it a win. It was a win. It was also nearly
meaningless, because running the same vector against the rest of our own bot
field turned up one archetype that beat it by **-20.45 trail** — and that
archetype had been sitting in the default field the whole time.

Two separate mistakes, both easy:

**We baselined against whatever was handy.** Our head-to-head scripts had
hardcoded one archetype months earlier and it became the default by inertia. It
was a single-strategy specialist, not the neutral vector a shipped bot would
use, and it was not even a strong one. For comparisons that hold weights
constant and vary only the driver this was harmless. For "is this vector good?"
it was not.

> **Rule:** the baseline for "is X better" must be the best thing you have, not
> the thing your scripts already imported. Run the full field before believing a
> single pairing. A vector can beat the generalist and lose to the specialist,
> and only the field shows you which.

**We validated on the seeds we trained on.** Our tuner generates its corpus from
game seeds `0..N`; our match runner started at seed `0`. The first measurement
came back at **+12.97**. On fresh seeds the same vector measured **+4.65, with
the interval spanning zero.**

The interesting part is that this was *not* leakage. The fitted vector scored
almost identically on both ranges; the baseline improved by 11 trail. Seeds
`0..19` were simply decks where the baseline plays badly.

> **Rule:** paired seeds cancel deck luck BETWEEN the two bots in a pair. They
> do not make twenty decks a representative sample of decks. Those are two
> different variance sources and solving one feels exactly like solving both.

Budget accordingly: separating a ~5-point effect from zero took about **60
paired seeds** in our game, not the 20 we started with. And add a seed-offset
flag on day one — ours found a real problem the first time it was used.

### Keep one frozen opponent you never touch

An absolute score — points, resources, whatever your game counts — is a real
signal and mostly enough. If your bot goes from 10 points to 50, it improved.
You do not need a control to tell you that, and we would not argue otherwise.

Three things break it, and all three bit us.

**Your score depends on who you played.** The same vector of ours scored 61.2,
55.3 and 49.4 against three different opponents. That is a twelve-point swing
with nothing changed but the other side of the table, which is wider than most
of the improvements we were trying to detect.

**A rules change resets the scale.** We capped a scoring route mid-project and
every number before it became incomparable to every number after. Absolute
scores do not survive the thing you are building the simulator to help you do.

**In a game with a shared pump, score is not skill.** Ours had a repeatable
end-game conversion. Two players who both find it both score two hundred, and
neither is better at beating the other. The number went up; the skill did not.
Any game with a cooperative or uncontested scoring route has this, and you
usually find out it has one *after* you have been trusting the number.

The fix is one line of discipline:

> **Pin one weight vector as the Control. Never change it. Measure every later
> version against it, forever.**

We did not, and instead moved our baseline three times over the project —
whatever the scripts happened to import. At one point we celebrated a vector for
beating an opponent we had picked by accident, which a field check later showed
was neither neutral nor strong. The margin was real. It just was not about
anything.

Two habits go with it, both cheap:

- **Run the whole field, not one pairing.** Our first field check immediately
  found a hole a single match had hidden: the same vector beat one opponent
  by +7.8 and lost to another by -20.5.
- **When the rules change, retire the Control and cut a new one** from the best
  vector under the new rules. It is a reference point, not an heirloom.

Use both measures for different questions. Absolute score answers *is the bot
playing the game at all* — ours scoring 4 trail against a human's 200 was the
single most useful number in the project. The Control answers *is this version
better than that one*, which absolute score cannot, because it moves for
reasons that have nothing to do with your bot.

### The half of the codebase the harness never touched

Every bug in this document was found by the simulator, in code the simulator
runs. Then a human played one game to the end and hit a crash that no amount of
simulation could ever have caught.

It was a temporal dead zone: a helper read a `const` declared ninety lines
below it. It survived every turn of every game because of a short circuit —

```js
if (!f || revealed >= f.steps.length) return live;
```

`f` is undefined until the game ends, so `||` short-circuited and the
uninitialised read never happened. The instant the final turn resolved, `f`
became truthy, the second operand evaluated for the first time, and the client
threw. Deterministic, and only ever at scoring.

Our harness had 1200 games of coverage on the rules and **zero on the UI**. It
plays games to completion without ever rendering one, so the crash was not
merely unfound — it was outside the reachable set.

> **Rule:** know which half of your codebase your harness cannot reach, and say
> so out loud. A simulator's coverage feels total because the number of games is
> large. The number of games has nothing to do with it.

The cheap mitigation we should have had: play one game through the real client,
to the end, before trusting any amount of headless play. Same discipline as
watching one game instead of averaging a thousand — one layer up.

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

### A feature can fit at zero and be wrong at zero

The single most expensive weight in this project was one the regression put at
roughly zero, correctly, for a reason that made the zero actively harmful.

We had a feature counting a kind of resource the player accumulates. Across
1200 games it correlated **-0.010** with the final margin — indistinguishable
from nothing. The fit duly priced it slightly negative. Played, that one wrong
sign cost about **15 trail points a game**, which was most of the distance
between our best fitted bot and the best hand-made one.

The diagnostic that explains it takes one line:

```
feature ~ final margin                       -0.010     raw
feature ~ final margin | dominant_feature    +0.173     the other held fixed
```

The two compete for the same actions. Every action spent accumulating is an
action not spent on the thing that scores, so a real positive effect is almost
exactly cancelled by its own opportunity cost. The net is zero. **The direct
effect is not zero, and the direct effect is what a bot needs**, because the
search is what decides whether the trade is worth making in this position.

Three things follow, and they are the transferable part:

1. **A near-zero coefficient has at least three causes**, and they need
   different responses: the feature does not matter (drop it), the corpus does
   not vary it (see identifiability, above), or its effect is masked by an
   opportunity cost (keep it, and do NOT take the fitted value).
2. **Test for the third case with a partial correlation** against whatever the
   feature competes with. It costs one pass over data you already have. If the
   partial is clearly non-zero while the raw is not, you have found a masked
   effect.
3. **A masked feature is worse than an unmeasured one.** An unmeasured feature
   fits at zero and does nothing. A masked feature fits slightly *negative* and
   actively teaches your bot to avoid something that helps.

We had run this exact diagnostic and written down the answer before we acted on
it. Reading a +0.173 partial and still shipping the raw fit cost us a day.

**A tidy hypothesis that measurement rejected**, recorded because we committed
it before testing it. We believed the fit's real problem was that it priced a
dozen small *intermediate-state* features — how full various piles are — and
that paying for a pile buys hoarding, since piles predict good outcomes because
good players fill them. It is a good story, it matches a real effect we had seen
elsewhere, and it is wrong here: zeroing eleven such features moved the result
by about 1 trail, and combining that with the real fix was WORSE than the real
fix alone. One feature explained nearly all of a 17-trail gap. When a plausible
mechanism and a boring single-variable explanation both fit, test the boring one
first — it is cheaper and it was right.

### The loop learns its own blind spots

Self-play tuning has a failure mode that is easy to state and hard to see: **the
corpus cannot contain evidence for a verb the bot does not perform.**

Our fit priced a whole cluster of related features — the machinery for clearing
a scoring liability out of your deck — at approximately nothing, where the
hand-made vector had priced them highly. That looked like a finding. Restoring
the hand-made values measured **+9.88 trail [4.11, 15.66]** on held-out seeds,
so it was not a finding. It was a blind spot.

The mechanism is a feedback loop with the sign pointing the wrong way:

```
bot rarely performs the verb
  -> corpus thin in the verb
    -> fit sees no evidence it pays
      -> fit removes the incentive
        -> bot performs it even less
```

Each iteration is individually defensible and the loop converges on a bot that
has optimised away a capability it never learned to use. R² does not fall while
this happens — the fit gets *better* at predicting a corpus that is getting
worse.

**What this means for iterating.** The usual argument for self-play is that a
search is a stronger improvement operator than the evaluation it improves, so
each round the corpus contains better decisions than the eval that generated it.
That is true and it is why the loop works at all. But it only lifts what the
search actually explores. Anything the current weights make the search prune is
outside the loop entirely, and the loop will keep pruning it harder.

Two cheap guards:

- **Keep a hand-made floor** on verbs you know matter and the corpus is thin in.
  Refuse to let the fit zero them, or blend rather than replace.
- **Watch a game every iteration, not just R².** Our blind spot was obvious in
  one watched game — the bot finished holding every liability it started with —
  and invisible in every aggregate we had.

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

### When outcomes stop working, train on decisions instead

This is where the project ended up, and it is the piece we would tell someone to
skip ahead to.

Everything above fits an evaluation to **outcomes**: record a position, label it
with the final result, fit weights to predict the label. That is Texel tuning,
it is the standard recipe, and for a long time we assumed our problems with it
were sampling problems.

They were not. Tripling the corpus changed nothing. The final measurement:

```
fitted on 2800 games   vs  the same vector with two hand corrections
   -31.6 trail   [-37.0, -26.1]        7 games won out of 60
```

A vector fitted from more data than we had ever collected lost decisively to one
we had patched by hand. Three independent measures agreed — head-to-head margin,
mean score, and what the bot spent its actions on.

#### Why outcome regression has a ceiling

Two reasons, and neither is fixed by more games.

**The label is shared by every position in a game.** A game that ends +18 stamps
"+18" on all 243 of its positions. You have a lot of rows and few observations,
and the effective sample size is games. Ours was 2800 against 28 parameters —
before you even consider that each parameter's effect is buried under everything
else that happened in those games.

**It measures association in games you played, not the effect of playing
differently.** This is the deeper one. We had a feature that fitted at
essentially zero across 2800 games, even with the ridge penalty cut 100-fold, so
it was not shrinkage and not a bug — the association genuinely was not there. And
yet a bot that valued that feature won by about 15 points a game.

Both can be true. In the corpus, the players who accumulated the most of that
resource were often the ones spending their actions badly, so the feature
travelled with playing poorly. Change the policy and the same feature becomes
valuable. **A regression over observed play cannot tell those apart.**

#### What to do instead

Stop asking "did this position lead to a win". Ask "**what move would a stronger
player make here**".

Concretely: at each decision, record the feature vector of every move considered
and which one the search picked. Then fit weights so the cheap evaluation ranks
the search's choice first — softmax cross-entropy over the legal moves, rather
than least squares on the final margin.

Three reasons it escapes the ceiling:

1. **The teacher is measurably stronger.** Our turn-level search beat one-ply by
   **+24 trail on paired seeds with identical weights** — same evaluation, only
   the lookahead differed. So its choices carry information the weights that
   produced them did not have. That is the condition distillation needs, and it
   is worth measuring rather than assuming.
2. **The alternatives are explicit.** The label is no longer "this resource is
   good", it is "here, with these twelve options, taking it beat the other
   eleven". Opportunity cost stops being a confounder and becomes part of the
   question — which is precisely what outcome regression cannot represent.
3. **A verb the outcome corpus is too thin to price still appears as a correct
   choice.** Our self-play blind spot (above) had no signal at the game level
   and plenty at the move level.

It is also far cheaper in games. One game yields hundreds of labelled decisions
instead of one outcome: ours produced ~95 decisions per game at 6.3 options
each, so 500 games is ~47,000 training examples. The outcome corpus needed 2800
games to produce 2800.

#### Two things to get right

**Scale is not determined by a ranking loss.** Doubling every weight changes no
ordering, so the fit pins direction and not magnitude. That is harmless for a
greedy bot and NOT harmless inside a search, where a stopping rule compares a
line against standing still and any fixed penalty is an absolute number. Rescale
the fitted vector against a reference before using it.

**Measure top-1 agreement, and compare it to chance.** It tells you immediately
whether a linear evaluation can even represent the search's preferences. Chance
is the average of 1/(options at each decision) — ours is about 22%. If your fit
sits near that, the model is too weak and no amount of data will help.

#### Log the decision, not the move

The most expensive mistake in this project was not a bug. It was throwing away
data we had already computed.

Every game in our outcome corpus ran the full search at every decision. The
search ranked every legal move, chose the best, and we recorded **only the
position it moved to**. The rejected alternatives — exactly what a ranking loss
needs — existed in memory at generation time and were discarded, so switching
method meant regenerating every game from scratch with the expensive search.

The asymmetry is the point. Storing what was considered costs maybe five to ten
times the disk. Re-deriving it costs a full replay of every game. And you cannot
tell which you will want until you change approach, which you will.

> **Rule:** when a search picks a move, log what it rejected and what it thought
> of each. Sample it if the volume frightens you — every fourth decision, the
> best dozen options — but do not log only the winner.

Two caveats, so this is not oversold. A rules change invalidates an old corpus
whatever you logged; ours did. And the teacher's strength caps the student, so a
corpus generated by weak weights teaches you to imitate weak weights. Neither
changes the recommendation, because both are also true of the outcome corpus you
would otherwise be keeping.

#### Iterate, and know why it is not circular

One pass distils the search's judgement into the evaluation. The loop is what
makes it compound:

```
corpus from search(T)  ->  fit student S  ->  T := S  ->  repeat
```

The reason this improves rather than chasing its own tail is that **the teacher
is always the search wrapped around the current weights, never the weights
alone**. Search(T) plays better than T, so each round's corpus contains
decisions better than the student that generated it. Drop the search from the
loop and it is genuinely circular: the model would only ever learn what it
already believes.

This is the same structure as self-play reinforcement learning, and it carries
the same failure mode we hit earlier and documented above — the loop can also
compound a blind spot, optimising away a capability it never learned to use. The
guard is the same too: watch a game every round, not only the loss curve.

#### It worked, and the second round did not

Measured, against the strongest vector we had — one produced by fitting outcomes
and then correcting two features **by hand** after we worked out what the fit had
got wrong:

```
                              margin        games won     corpus
  outcome fit                 -31.57          7-53         2800 games
  distilled, round 1           +2.27         33-27          500 games
```

Distillation closed a 34-point gap on **one sixth of the games**, and it
recovered both hand corrections on its own — no floors, no intervention. Top-1
agreement was 63.0% against 21.5% chance, which also answers a question worth
asking before you start: a linear evaluation *can* mostly represent what the
search prefers.

Then we iterated, and round 2 was worse:

```
                    top-1        vs the champion      vs its own teacher
  round 1           63.0%            +2.27                  n/a
  round 2           65.7%           -15.13              -17.85  [-26.6, -9.1]
```

**Top-1 went up while strength went down.** That is the trap, and it is worth
stating on its own line:

> **Top-1 agreement measures fidelity to whatever generated the corpus, not
> quality.** A better imitator of a worse teacher scores higher on it. It is a
> useful diagnostic WITHIN a round — near chance means your model cannot
> represent the search — and meaningless ACROSS rounds. Never promote on it.

The cause was the precondition in the section above going unmet. Round 1's
student came out *level* with the incumbent, not better — the interval spanned
zero. So `search(student)` was no stronger than `search(teacher)`, round 2 had
nothing to learn, and it inherited drift instead.

#### The gate that makes the loop safe

Promote the student to teacher only when it **beat the previous teacher with a
confidence interval clear of zero**. Otherwise stop, and keep the last student
that passed.

With that gate our first run would have correctly stopped after round 1. Without
it, it ran a second round that cost hours and produced a weaker bot, while every
cheap metric — loss, top-1 — said it was going well.

This is the operational form of "the improvement operator must be stronger than
what it improves". That sentence is easy to agree with and easy to not
implement. The gate is the implementation.

#### This is the standard answer, not a clever idea

Chess arrived here decades ago, twice over. Stockfish's NNUE is trained on
evaluations from **deeper search**, not on game results. AlphaZero's policy head
is trained on **MCTS visit counts** — literally which moves the search preferred.
Both are distillation from a stronger search.

Chess also faced the granularity version of the same question. One number per
piece type was too coarse — a knight on one square is not a knight on another —
and the answer was piece-square tables: value conditioned on context, not a
separate parameter per conceivable piece. If your evaluation has a feature
counting a diverse set of things, that is the analogous problem, and the fix is
to learn what makes them good (their properties, read off your own card data)
rather than a weight per item.

What does not transfer is brute force. Chess has millions of games to fit from.
A board game in development has hundreds. That asymmetry is the whole reason the
signal per game matters so much, and it is the best argument for distillation in
a small project.

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

**We never shipped a bot to players.** Late on, a distilled vector drew level
with the strongest hand-made one we had — and then the scoring rules were
redesigned, and every bot on our ladder ranked almost exactly by how it valued
the feature that changed. Weights are downstream of rules. The harness, the
tuner, the paired-seed design and the seed-offset flag survive a rule change;
the numbers they produce do not.

**And the weights were never the point.** This is the single most useful thing
we learned and we learned it far too late. The bot the game actually shipped
with searched one move ahead. Our measurements had all been made with a
turn-level search around the same evaluation. When we finally ran the two
against each other:

```
  every weight vector we had, one ply        4-6 points
  the same vectors inside the search        40-60 points
```

**The search was worth about fifty points and the weights about five.** Months of
tuning, fitting and distilling moved a number that a few hours of lookahead
dwarfed. All of it was real — the distilled vector does beat the hand-made one —
but it was the smaller half by an order of magnitude, and we spent almost all
our effort there.

The trap is that weights are easy to iterate on and search architecture is not.
A weight change is a number and a rerun; a search is a design with snapshots, a
repetition set, pruning and a budget. So the cheap loop is the one you run, and
it is the one that cannot take you where you are trying to go.

> **Before tuning anything, measure what one more ply of lookahead is worth.**
> If it dwarfs your weights — and in a game where scoring is a multi-step chain
> it will — build the search first and tune second.

**What we would do differently, in order.** Measure the value of lookahead.
Build the search. Log every decision with its rejected alternatives, not just
the move played. Pin a Control. Only then fit weights, and fit them to decisions
rather than to outcomes.

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

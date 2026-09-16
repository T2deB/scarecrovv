/*
 * Bots.
 *
 * Every bot is the same algorithm — greedy one-ply lookahead — differing only in
 * a weight vector. For each legal action it clones the state, applies the
 * action, scores the RESULTING state, and takes the best.
 *
 * THE FEATURES AND THE SCORING LIVE IN ../src/bot.ts, not here. This file used
 * to carry a second implementation of both, which is the same trap sim/engine/
 * was: two copies of one idea, drifting. They were verified identical over 1330
 * state/player pairs before the copy was deleted, and now there is one.
 *
 * What is left here is the DRIVER — how a bot is asked for a move in the
 * harness — and the archetypes.
 *
 * Known limitation: the bot sees full state, including face-down decks. Scoring
 * uses only aggregate features, never the identity of an unseen card, but
 * one-ply lookahead of a draw lands on a hand dealt from the true deck order
 * and `remnantsInHand` is scored. The leak is real; see the README.
 */

import type { MockGameState } from "./gamestate.ts";
import {
  capture,
  restore,
  searchAdapter,
  signatureOf,
  simulateSettled,
  type Available,
  type Chooser,
  type Snap,
} from "./harness.ts";
import {
  ZERO,
  blend,
  blindDrawAdjustment,
  drawPrior,
  features,
  phaseOf,
  positionKey,
  score,
  settle,
} from "../src/bot.ts";
import type { Tapered, Weights } from "../src/bot.ts";

export { ZERO, blend, blindDrawAdjustment, drawPrior, features, phaseOf, score };
export type { Tapered, Weights };

type Any = any;

// ---------------------------------------------------------------------------
// The driver
// ---------------------------------------------------------------------------

const MAX_TURNS_PER_ROUND = 6;

/**
 * How often the bot had to be rescued from itself. Both counters should stay at
 * zero; a non-zero one is a bug report about the engine's action list, not a
 * dial to tune. See the comments at each site.
 */
export const GUARDS = {
  streak: 0,
  roundClock: 0,
  where: {} as Record<string, number>,
  /** The picks leading up to the first firing, so the cause is readable. */
  sample: [] as string[],
};

export const makeBot = (w: Weights, rng: () => number): Chooser => {
  // Advancing a round DISCARDS everything not re-tethered, so a bot that values
  // its Sanctum sees it as a pure loss and will happily take free end-turns
  // forever. A human knows the game only ends once four rounds are done; this
  // is that knowledge, since one-ply lookahead cannot contain it.
  let lastRound = -1;
  let lastTurnCount = -1;
  let turnsInRound = 0;
  /** Rolling window of recent picks, for GUARDS.sample. */
  let recent: string[] = [];
  /*
   * Every state seen since this player's turn began. A search must never go
   * backwards; see the same set in ../src/bot.ts for why this replaced counting
   * picks. Keyed on the metaData the harness would clone, minus the append-only
   * arrays, which is exact enough for both loops found so far.
   */
  let seen = new Set<string>();
  let seenTurn = -1;
  let clicks = 0;

  return (state, playerId, options) => {
    const md = state.metaData as Any;
    // positionKey, not a hand-rolled JSON of metaData: the latter includes
    // `seq`, which makes every state unique and the repetition set inert.
    const sig = (m: Any): string => positionKey({ metaData: m } as Any);
    const myRound = md.p?.[String(playerId)]?.round ?? 0;
    const myTurns = md.turns?.[String(playerId)] ?? 0;
    if (myRound !== lastRound) {
      lastRound = myRound;
      turnsInRound = 0;
    }
    if (myTurns !== lastTurnCount) {
      lastTurnCount = myTurns;
      turnsInRound++;
    }
    if (myTurns !== seenTurn) {
      seenTurn = myTurns;
      seen = new Set<string>();
      clicks = 0;
    }
    clicks++;
    seen.add(sig(md));

    // Undo would let a bot loop forever, and it never needs one.
    let pool = options.filter((o) => o.intent !== "undo");
    if (pool.length === 0) pool = options;

    /*
     * No "commit after N non-terminal picks" guard — see ../src/bot.ts for why
     * it went. `seen` above is what stops loops now, and unlike a counter it
     * does not cut short a decision that is merely long.
     */

    if (clicks > MAX_CLICKS_PER_TURN) {
      const out = pool.filter((o) => o.intent === "confirm");
      if (out.length > 0) pool = out;
    }

    if (turnsInRound > MAX_TURNS_PER_ROUND && (md.phase?.kind ?? "play") === "play") {
      const advance = pool.filter(
        (o) => (o.action as Any).button?.id === "advance" && !(o.action as Any).button?.disabled,
      );
      if (advance.length > 0) {
        GUARDS.roundClock++;
        pool = advance;
      }
    }

    // The value of standing still, and the bot's stopping rule. See the same
    // `here` in ../src/bot.ts for why a hill climb needs one.
    const here = score(state as Any, playerId, w);
    const scored: { option: Available; value: number }[] = [];
    // Do not look past the shuffle. See drawPrior in ../src/bot.ts.
    const prior = drawPrior(state as Any, playerId);

    for (const option of pool) {
      let value: number;
      try {
        const next = simulateSettled(state, option);
        value =
          score(next as Any, playerId, w) +
          blindDrawAdjustment(next as Any, playerId, w, prior);
        // Never go backwards. See `seen` above.
        if (seen.has(sig((next as Any).metaData))) value -= 1e6;
      } catch {
        // An action that throws is worse than any that doesn't.
        value = -Infinity;
      }
      // Tiny nudge toward terminal actions, so a bot indifferent between
      // selecting and confirming still makes progress instead of oscillating.
      if (option.intent === "confirm") value += 0.01;
      /*
       * Leaving actions on the table costs what they could have bought. See
       * UNSPENT_ACTION in ../src/bot.ts: a property of the move, not of the
       * position, and every attempt to express it as a feature deadlocked.
       */
      if ((option.action as Any).button?.id === "end-turn") {
        const me = md.p?.[String(playerId)];
        const left = me ? 2 + (me.extraActions ?? 0) - (me.actions ?? 0) : 0;
        value -= 12 * Math.max(0, left);
      }
      scored.push({ option, value });
    }

    const bestScore = Math.max(...scored.map((s) => s.value));
    const climbing = bestScore > here + 1e-9;
    const terminal = scored.filter((s) => s.option.intent === "confirm");
    const field = climbing || terminal.length === 0 ? scored : terminal;
    const top = Math.max(...field.map((s) => s.value));
    let best = field.filter((s) => s.value >= top - 1e-9).map((s) => s.option);
    if (best.length === 0) best = pool;
    const picked = best[Math.floor(rng() * best.length)];
    recent.push(`${md.phase?.kind ?? "play"}: ${picked.intent} ${picked.label ?? (picked.action as Any).button?.id ?? "?"}`);
    if (recent.length > 12) recent.shift();
    return picked;
  };
};

/*
 * A bot that searches a whole TURN instead of a single click.
 *
 * This is the fix for the thing no weight vector can fix. Scoring is a
 * two-action chain — take a Remnant for 1 soul, compost it for 2 more and
 * advance 3 — and a one-ply bot decides the first half with the payoff of the
 * second half invisible. The two halves want opposite things from one number,
 * so no value of `remnantsInHand` is correct: low enough to cash them and the
 * bot never buys any, high enough to buy them and it never cashes them.
 *
 * A turn is two actions, which is exactly enough to hold both halves. Searching
 * to the end of one puts the whole chain inside a single decision.
 *
 * THE BEAM MUST BE WIDE. Measured, a narrow one is worse than no search at all
 * (2.8 trail at width 3, against 3.8 for one-ply) because it prunes on the same
 * evaluation that was wrong to begin with: `remnantsInHand` makes "take 6"
 * outrank "take 2" at the intermediate node, so the take-then-compost line is
 * discarded before it can pay. Width 8 with a LOW potential weight roughly
 * triples one-ply. Width beats depth — w16/d10 was no better than w8/d8 and
 * five times slower.
 *
 * It makes a move, looks, and takes it back, rather than photocopying the whole
 * table per node. See `capture` in harness.ts for why that is worth doing.
 */
/**
 * Clicks one player may spend on a single turn before only terminal moves are
 * offered. /src/bot.ts has the same backstop as MAX_STEPS; the simulator's
 * drivers had only the harness's global 4000-step cap, so they wandered until
 * the whole game died.
 *
 * It exists for moves that are FREE and REVERSIBLE. Out of actions, with 25
 * animals owned and three Tether slots, a bot can shuffle which three are
 * tethered essentially forever — and every arrangement is genuinely NEW, so the
 * repetition set correctly reports it has not been there before. Nothing is
 * broken; it simply will not stop. One game in six in the search corpus ran to
 * the step cap this way, and a stalled game costs thirteen normal ones.
 *
 * A real turn is four to eight clicks. Forty is far beyond anything legitimate.
 */
const MAX_CLICKS_PER_TURN = 40;

type Node = { first: Available; snap: Snap; value: number; done: boolean };

export const makeSearchBot = (
  w: Weights,
  rng: () => number,
  width = 8,
  depth = 8,
): Chooser => {
  /*
   * The same two rules makeBot has, and they are not optional. Searching deeper
   * does not make a bot stop: without the per-turn repetition set and the
   * stopping rule this driver oscillated and EVERY game hit the step cap.
   */
  let visited = new Set<string>();
  let visitedTurn = -1;
  let clicks = 0;

  return (state, playerId, options) => {
    let pool = options.filter((o) => o.intent !== "undo");
    if (pool.length <= 1) return pool[0] ?? options[0];

    const md = state.metaData as Any;
    const turns = md.turns?.[String(playerId)] ?? 0;
    if (turns !== visitedTurn) {
      visitedTurn = turns;
      visited = new Set<string>();
      clicks = 0;
    }
    clicks++;
    if (clicks > MAX_CLICKS_PER_TURN) {
      const out = pool.filter((o) => o.intent === "confirm");
      if (out.length > 0) pool = out;
    }

    const io = searchAdapter(state, playerId);
    // Do not look past the shuffle; the prior describes the state we search FROM.
    const prior = drawPrior(state as Any, playerId);
    const valueHere = (): number =>
      score(state as Any, playerId, w) + blindDrawAdjustment(state as Any, playerId, w, prior);
    const turnOver = (): boolean =>
      (state.metaData as Any).over === true || !state.activePlayerIds.includes(playerId);

    const root = capture(state);
    const rootSig = signatureOf(root);
    visited.add(rootSig);
    const here = valueHere();

    const seen = new Set<string>([rootSig]);
    let best: Node | null = null;

    /** Expand one node: try each option from `from`, keeping what is new. */
    const children = (from: Snap, opts: Available[], first: Available | null): Node[] => {
      const out: Node[] = [];
      for (const option of opts) {
        restore(state, from);
        try {
          io.apply(state, playerId, option.action);
          // "decide", not "max": how many Remnants to take is a real decision,
          // and a search must be able to examine a line that takes fewer.
          settle(state as Any, playerId, io.available, io.apply, io.buttonId, "decide");
        } catch {
          continue;
        }
        const snap = capture(state);
        const sig = signatureOf(snap);
        // Never go backwards, within this search OR to anywhere this turn has
        // already been. Deduplicating by signature is also the transposition
        // table: most of the tree is permutations of the same few outcomes.
        if (seen.has(sig) || visited.has(sig)) continue;
        seen.add(sig);
        const node: Node = {
          first: first ?? option,
          snap,
          value: valueHere(),
          done: turnOver(),
        };
        out.push(node);
        if (!best || node.value > best.value) best = node;
      }
      return out;
    };

    /*
     * Prune with ROOT DIVERSITY: keep the best few lines per opening move, not
     * the best few lines overall.
     *
     * A plain beam judges a line by its score partway through, using the same
     * evaluation that cannot see two clicks ahead — which is the whole reason
     * the search exists. So the openings that pay immediately crowd out the
     * ones that pay later, and the search confidently explores the wrong half
     * of the tree. Two separate bugs were this, wearing different hats:
     *
     *   "Play Corrupted Soul"  scores +1.70 at once (a soul)
     *   "Gain Remnants"        scores +0.00 at once (it opens a sub-phase)
     *
     * so every Corrupted Soul line survived the cut and every Remnant line was
     * discarded before composting could pay. The bot drained the entire supply
     * of 40 Corrupted Souls, a 120-point retreat, and no weight on
     * corruptedLive changed it — the lines that would have shown the cost were
     * never examined.
     *
     * Guaranteeing each opening its own slots means every first move is still
     * being explored at depth when the payoff finally lands.
     */
    const perRoot = Math.max(2, Math.ceil(width / 2));
    const prune = (nodes: Node[]): Node[] => {
      const byFirst = new Map<Available, Node[]>();
      for (const n of nodes.sort((a, b) => b.value - a.value)) {
        const list = byFirst.get(n.first) ?? [];
        if (list.length < perRoot) list.push(n);
        byFirst.set(n.first, list);
      }
      return [...byFirst.values()].flat().sort((a, b) => b.value - a.value).slice(0, width * 3);
    };

    let beam = prune(children(root, pool, null));

    for (let d = 1; d < depth && beam.length > 0; d++) {
      const next: Node[] = [];
      for (const node of beam) {
        if (node.done) continue;
        restore(state, node.snap);
        next.push(...children(node.snap, io.available() as Available[], node.first));
      }
      if (next.length === 0) break;
      beam = prune(next);
    }

    // The real state must come back exactly as it was found.
    restore(state, root);

    const terminal = pool.filter((o) => o.intent === "confirm");
    if (!best) return terminal[0] ?? pool[0];
    /*
     * The stopping rule, unchanged by depth: if the best line the search can
     * find does not beat standing still, this is a local optimum and the bot
     * should commit rather than wander into whatever is least bad.
     */
    if (best.value <= here + 1e-9 && terminal.length > 0) return terminal[0];
    /*
     * The first move of the best line found at ANY depth. A turn that ends
     * after one click is a legitimate answer and would otherwise be thrown away
     * for having nowhere to go.
     */
    return best.first;
  };
};

/**
 * A bot whose weights depend on the round.
 *
 * Early the game is about acquiring — animals, sinks, the shape of your Tether.
 * Late it is about cashing what you built, at the Hollow Echo's rate of 5 a
 * Remnant. One vector cannot say both, which is what the tapered fit is for.
 */
export const makeTaperedBot = (t: Tapered, rng: () => number): Chooser => {
  const inner = new Map<string, Chooser>();
  return (state, playerId, options) => {
    // Round to a tenth so a handful of blended bots are reused rather than one
    // rebuilt per decision; the per-bot state (the repetition set) is keyed on
    // the turn anyway.
    const p = Math.round(phaseOf(state as Any, playerId) * 10) / 10;
    const key = `${playerId}:${p}`;
    let bot = inner.get(key);
    if (!bot) {
      bot = makeBot(blend(t, p), rng);
      inner.set(key, bot);
    }
    return bot(state, playerId, options);
  };
};

/** Control group. If a weighted bot can't beat this, the weights are noise. */
export const makeRandomBot = (rng: () => number): Chooser => {
  return (_state, _playerId, options) => {
    let pool = options.filter((o) => o.intent !== "undo");
    if (pool.length === 0) pool = options;
    const confirms = pool.filter((o) => o.intent === "confirm");
    const use = confirms.length > 0 && rng() < 0.8 ? confirms : pool;
    return use[Math.floor(rng() * use.length)];
  };
};

// ---------------------------------------------------------------------------
// Archetypes
// ---------------------------------------------------------------------------

/*
 * These are a SPREAD OF BEHAVIOUR, not a search for the best weights.
 *
 * That distinction is what let the field be cut from 22 to this. Finding good
 * weights is a fitting problem — regression over self-play positions does it
 * far better than a round robin of hand-written guesses — so the six domain
 * PAIRS, which only interpolated between singles already in the field, and the
 * three `animals-<domain>` variants, which duplicated the domain singles with
 * an animal bias, bought nothing a fit will not find.
 *
 * What a field of archetypes is still needed for is COVERAGE: every card has to
 * be played by somebody, or it has no statistics and the balance report quietly
 * omits it. sim/conformance.ts is what checks that, and the nine in CORE are
 * enough for all 99.
 */

/** Shared baseline: everyone wants points, souls and a clean deck. */
const BASE: Weights = {
  ...ZERO,
  trail: 10,
  souls: 2,
  remnantsInHand: 1,
  remnantPayoff: 6,
  remnantCash: 3,
  tethered: 1,
  consumed: 0.5,
  /*
   * The drag of a dead card, not the scoring penalty — that is `stuckSouls`,
   * priced at the 3 trail it actually is. See ../src/bot.ts for why one flat
   * number could not do both jobs.
   */
  corruptedLive: -2,
  corruptedInHand: 0,
  clearableSouls: 12,
  stuckSouls: -30,
  soulSinks: 8,
  animals: 20,
  deckSize: -0.15,
  handSize: 0.3,
  effigies: 6,
};

const w = (over: Partial<Weights>): Weights => ({ ...BASE, ...over });

/**
 * Bots built to actually buy animals.
 *
 * The point-value bots ignore animals because a Remnant turns 1 soul into 3
 * guaranteed trail, while an animal's payoff is delayed and a one-ply bot cannot
 * see it. These weight animals, tethering and Look-readiness highly enough to
 * compete, so the tournament can answer "is the animal route viable at all?"
 * rather than assuming it isn't.
 */
const ANIMAL_BASE: Weights = {
  ...ZERO,
  trail: 10,
  souls: 3,
  corruptedLive: -2,
  corruptedInHand: 0,
  clearableSouls: 12,
  stuckSouls: -30,
  soulSinks: 8,
  deckSize: -0.1,
  handSize: 0.3,
  animals: 8,
  // CALIBRATION RULE: Looking for Scarecrovv discards all three Tethered cards,
  // so it costs 3*(tethered + lookReady) and pays effigies. If `effigies` does
  // not clearly exceed that, the bot assembles a matching trio and then hoards
  // it forever — which is exactly what the first version of these weights did.
  tethered: 4,
  lookReady: 8,
  effigies: 60,
  // Deliberately low: these bots must be willing to skip a Remnant to buy a card.
  remnantsInHand: 0.5,
  remnantPayoff: 4,
  remnantCash: 3,
};

const aw = (over: Partial<Weights>): Weights => ({ ...ANIMAL_BASE, ...over });

export const ARCHETYPES: Record<string, Weights> = {
  // --- baselines ---------------------------------------------------------
  greedy: w({ souls: 4 }),
  "remnant-rush": w({ remnantsInHand: 5, trail: 12 }),
  thinner: w({ consumed: 2.5, deckSize: -0.8, corruptedLive: -3 }),

  // --- single domain -----------------------------------------------------
  /*
   * Rotting now weights the buried pile's COMPOSITION, not just its size. The
   * domain's plan is to store Remnants there and cash them in the Hollow Echo,
   * and `buried` alone could not tell that plan apart from burying anything.
   */
  rotting: w({ Rotting: 2.5, buried: 0.4, buriedRemnants: 2, buriedCashers: 4, buriedPayoff: 14 }),
  radioactive: w({ Radioactive: 2.5, consumed: 1.5 }),
  enchanted: w({ Enchanted: 2.5, severed: 0.8 }),

  // --- Infuse ------------------------------------------------------------
  // Each values the severed-pile composition its Infuse mode actually pays out
  // on, which is what keeps severing and infusing consistent.
  "infuse-trail": w({ Enchanted: 2.5, severedRemnants: 6, remnantsInHand: 4 }),

  // --- animal-first ------------------------------------------------------
  "scarecrovv-hunter": aw({ lookReady: 14, effigies: 140, tethered: 5 }),

  // --- second tier: not in CORE, run them by name with --bots -------------
  "engine-builder": w({ tethered: 5, deckSize: 0 }),
  "infuse-sanctum": w({ Enchanted: 2.5, severedCorrupted: 5, corruptedLive: -0.5 }),
  "infuse-source": w({ Enchanted: 2.5, severedAnimals: 5 }),
  "effigy-max": aw({ effigies: 220, lookReady: 16, tethered: 5, trail: 6 }),
  "animal-hoarder": aw({}),
};

/**
 * The default field. A round robin is O(n^2), so this is what makes a sweep
 * cheap enough to run often: nine bots is 45 pairings against 253 for all 22.
 *
 * `random` is the control group. If a weighted bot cannot beat it, the weights
 * are noise and every other number in the report is meaningless.
 */
export const CORE = [
  "greedy",
  "remnant-rush",
  "thinner",
  "rotting",
  "radioactive",
  "enchanted",
  "infuse-trail",
  "scarecrovv-hunter",
  "random",
];

export const ARCHETYPE_NAMES = [...Object.keys(ARCHETYPES), "random"];

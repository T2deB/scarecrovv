/*
 * Fit the evaluation weights to self-play outcomes.
 *
 * This is Texel's method, the standard way chess engines stop arguing about
 * evaluation terms and start measuring them: play games, record the feature
 * vector at every position, label each one with how the game finished, and
 * regress.
 *
 * TWO CHOICES DIFFER FROM THE CHESS RECIPE, both because this is a race rather
 * than a win/loss game:
 *
 *   - The label is the final TRAIL MARGIN, not win/lose. A binary label throws
 *     away almost everything a race tells you — winning by 2 and winning by 90
 *     are the same bit — and margin has far lower variance per game.
 *
 *   - So the fit is least squares rather than logistic, which makes every
 *     fitted weight readable IN TRAIL POINTS: "one Corrupted Soul left in my
 *     deck costs me N points of final margin". That is the same unit the
 *     balance questions are asked in, and it gives a free sanity check — the
 *     coefficient on `trail` itself must come out near 1.
 *
 *   node --import ./register.mjs tune.ts [games] [--iters N] [--out w.json]
 */

import { legalActions, newGame, toEngineAction, type Available } from "./harness.ts";
import { ARCHETYPES, CORE, makeBot, makeRandomBot, makeSearchBot, makeTaperedBot } from "./bot.ts";
import { ZERO, features } from "../src/bot.ts";
import { FINAL_ROUND } from "../src/cards.ts";
import type { Tapered, Weights } from "../src/bot.ts";
import { applyActions, isGameOver } from "../src/game.ts";

type Any = any;
const KEYS = Object.keys(ZERO) as (keyof Weights)[];

const args = process.argv.slice(2);
const GAMES = Number(args.find((a) => /^\d+$/.test(a)) ?? 40);

/*
 * Read a --flag's value, or fall back.
 *
 * Written out because the obvious one-liner is wrong in a way that hid for the
 * whole life of this file:
 *
 *   args[args.indexOf("--sigma") + 1] ?? 0.9
 *
 * indexOf returns -1 when the flag is absent, so that reads args[0] — the
 * positional game count — and the ?? never fires because args[0] is defined.
 * SIGMA was therefore 200, 400, 1200 in every run rather than 0.9, and perturb
 * computes Math.exp(SIGMA * u): every "perturbed" weight was either zero or
 * astronomically large. The population built to widen the corpus was noise, in
 * every tuning run done so far.
 */
const flag = (name: string, fallback: string): string => {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : fallback;
};
const ITERS = Number(flag("--iters", "3"));
const OUT = flag("--out", "weights.json");
/*
 * Ridge penalty, on standardized features.
 *
 * Raised from 1e-3, which was for conditioning only and far too weak for the
 * shape of this data. Every position in a game carries the SAME label — the
 * final margin — so a hundred positions from one game are one observation
 * wearing a hundred hats. The effective sample size is the number of GAMES, and
 * the taper doubled the parameter count to 56. At 250 games that is deep into
 * overfitting, and it showed: R² swung 0.60 -> 0.30 between runs and `trail`
 * came back negative, which cannot be true.
 *
 * So: more games, and a penalty that actually bites.
 */
const LAMBDA = Number(flag("--lambda", "0.05"));

/*
 * What the fit predicts. Both are trail points — this was never win/lose, for
 * the reason in the header — but they trade off differently:
 *
 *   margin  my final trail minus theirs. Literally what decides the game, and
 *           it carries the opponent's noise: my good move and their blunder
 *           look identical to it.
 *   mine    my final trail alone. Isolates my own contribution and roughly
 *           halves the variance of the label, at the cost of being blind to the
 *           race — 50 is excellent or hopeless depending on the other player.
 *
 * Worth measuring rather than arguing, because the effective sample size here
 * is the number of GAMES, so halving the label's noise is worth about as much
 * as doubling them, and games are the expensive part.
 */
const LABEL = flag("--label", "margin") as "margin" | "mine";

/*
 * Generate the corpus with the TURN-LEVEL SEARCH rather than the one-ply bot.
 *
 * Weights fitted to one-ply play are fitted to the wrong game: a search bot
 * values a position differently because it can actually reach things a one-ply
 * bot cannot. It beats one-ply by roughly +21 trail on paired seeds, so the two
 * are not playing the same game at all.
 *
 * It costs far more per game, which is the whole reason this is a flag and an
 * overnight run rather than the default.
 *
 * Width and depth default to 8/6 — the config sweep found depth beyond 6 buys
 * nothing and 8/6 was the best margin in the table at 37% less cost than 8/8.
 */
/*
 * Append every game's samples to a file as they are produced, and skip games
 * already in it on a restart.
 *
 * A search-generated corpus is an overnight job. Losing twelve hours to a crash
 * in hour eleven is not a risk worth carrying when the fix is one append per
 * game — and the same file makes a run resumable, so it can be stopped and
 * continued deliberately.
 *
 * One line per game, so a torn write at the moment of a crash costs that game
 * and nothing else. A line that will not parse is skipped rather than fatal.
 */
const CHECKPOINT = flag("--checkpoint", "");

const SEARCH = args.includes("--search");
const BEAM_W = Number(flag("--width", "8"));
const BEAM_D = Number(flag("--depth", "6"));

/*
 * NOTHING IS ANCHORED, and the attempt to anchor is worth recording.
 *
 * It looked obvious that two weights were known by construction — a point of
 * trail is a point of margin, and a Corrupted Soul left in your deck is a
 * 3-point retreat by the rule printed on it — so they were pinned at 1.0 and
 * -3.0 and the rest fitted around them, the way a chess engine anchors material
 * and fits the positional terms.
 *
 * R² went to -205. Pinning trail at 1.0 predicts far worse than predicting the
 * mean, and the reason is a real fact about this game rather than a bug:
 *
 *   final margin = Δtrail now + Δ(future advances) - Δ(end-of-game retreat)
 *
 * The retreat term is large and varies between players — seven Corrupted Souls
 * is 21 points — so a lead now is only partly kept. The free fit says a point
 * of trail is worth about 0.62 points of final margin, and that number is a
 * measurement, not an error: leads get given back. An "obvious" weight that the
 * data contradicts is the data telling you something.
 */
const ANCHORED: Partial<Record<keyof Weights, number>> = {};

/**
 * Smallest raw standard deviation a feature needs before its fitted weight is
 * believable.
 *
 * Un-standardizing divides by that deviation, so a column that barely moves
 * turns a modest standardized coefficient into a wild one — this is what put
 * severedRemnants at -8.375 while the same run reported it as never varying.
 * Below the threshold the weight is reported as unidentified rather than
 * guessed at.
 */
const MIN_SD = 0.05;
/** Spread of the population the corpus is played by. See `perturb`. */
const SIGMA = Number(flag("--sigma", "0.9"));

const mulberry = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) >>> 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

// ---------------------------------------------------------------------------
// The population
// ---------------------------------------------------------------------------

/*
 * A fit can only learn from variation the corpus contains, and the archetypes
 * alone do not contain enough.
 *
 * Measured over 2310 archetype-vs-archetype positions, five features never
 * moved at all: consumed, severedRemnants, severedAnimals, buriedCashers and
 * buriedPayoff were 0 in every single one, and buried, severed and effigies had
 * a standard deviation under 0.2. All three verbs need CARDS, and these bots
 * buy 0.1 a game — so the corpus is a game in which almost nothing happens, and
 * regressing on it produced a weight of +5.9 for a feature that was constant.
 *
 * This is the same problem chess engines solve with randomised openings: widen
 * the distribution of positions until the thing you want to measure actually
 * varies. Each game is played by a perturbed weight vector rather than a named
 * archetype, and one in three is pushed hard toward buying animals, because
 * that is the behaviour the corpus was missing entirely.
 */
const perturb = (base: Weights, rng: () => number, buyer: boolean): Weights => {
  const w = { ...base };
  for (const k of KEYS) {
    if (k === "trail") continue; // the anchor of the scale; leave it alone
    // Log-normal, so a weight can grow or shrink by a large factor but never
    // flips sign on its own — a bot that values Remnants negatively is not a
    // strategy, it is noise.
    const u = Math.sqrt(-2 * Math.log(rng() || 1e-9)) * Math.cos(2 * Math.PI * rng());
    w[k] = (w[k] || 0) * Math.exp(SIGMA * u);
  }
  if (buyer) {
    // Enough to make buying beat taking another Remnant, which is what the
    // corpus needs to contain before anything can be learned about it.
    w.animals = 6 + 8 * rng();
    w.tethered = 3 + 4 * rng();
    w.effigies = 40 + 120 * rng();
    w.lookReady = 5 + 10 * rng();
    w.remnantsInHand = 0.5 * rng();
  }
  return w;
};

// ---------------------------------------------------------------------------
// Sampling
// ---------------------------------------------------------------------------

type Sample = { x: number[]; y: number; p: number };

/**
 * One game, recording every player's features at each of their decisions.
 *
 * THE FEATURE VECTOR IS A DIFFERENCE: mine minus my opponent's. This is not a
 * refinement, it is the difference between the fit working and not working, and
 * it is what the chess recipe means by "features calculated as white minus
 * black".
 *
 * Fitting absolute features against a relative label cannot work in a
 * symmetric game. The label is the final trail MARGIN, but `my trail` on its
 * own says nothing about a margin when both players are advancing at roughly
 * the same rate — my trail and my opponent's are so highly correlated that the
 * difference between them is almost pure noise to a regression that can only
 * see one of them. Fitted that way, `trail` came out at -0.041 against a known
 * true value of 1.0.
 *
 * Differencing costs the BOT nothing: during my own turn my opponent's features
 * are constant, so w·(f_me - f_opp) and w·f_me have the same argmax. The bot
 * goes on scoring its own side; only the fit needs both.
 *
 * Positions inside a turn are highly correlated, which costs effective sample
 * size but does not bias the fit — and sampling every decision is what keeps a
 * game cheap enough to run thousands of.
 */
const playAndRecord = (
  choosers: Record<number, Any>,
  seed: number,
  out: Sample[],
): boolean => {
  const st = newGame([1, 2], seed);
  const pending: { pid: number; x: number[] }[] = [];
  let steps = 0;

  while (!isGameOver(st as Any, {} as Any) && steps < 4000) {
    const act = st.activePlayerIds[0];
    if (act === undefined) break;
    // legalActions, NOT getAvailableActions: the latter enumerates disabled
    // buttons that the real server refuses. Every corpus built before this line
    // existed was a corpus of games containing illegal moves.
    const opts = legalActions(st, act);
    if (!opts.length) break;

    if (((st.metaData as Any).phase?.kind ?? "") === "play") {
      const foe = act === 1 ? 2 : 1;
      const mine = features(st as Any, act) as Any;
      const theirs = features(st as Any, foe) as Any;
      // p walks 0 -> 1 across the rounds; see Tapered in ../src/bot.ts.
      const round = (st.metaData as Any).p?.[String(act)]?.round ?? 1;
      pending.push({
        pid: act,
        p: Math.min(1, Math.max(0, (round - 1) / Math.max(1, FINAL_ROUND - 1))),
        x: KEYS.map((k) => (mine[k] as number) - (theirs[k] as number)),
      });
    }
    applyActions(st as Any, toEngineAction(st, choosers[act](st, act, opts)) as Any);
    steps++;
  }
  if (steps >= 4000) return false;

  const trail: Record<number, number> = {};
  for (const sm of ((st.metaData as Any).summary ?? []) as Any[]) trail[sm.playerId] = sm.trail;
  for (const s of pending) {
    const mine = trail[s.pid] ?? 0;
    const theirs = Math.max(...[1, 2].filter((p) => p !== s.pid).map((p) => trail[p] ?? 0));
    out.push({ x: s.x, y: LABEL === "mine" ? mine : mine - theirs, p: s.p });
  }
  return true;
};

// ---------------------------------------------------------------------------
// Ridge regression
// ---------------------------------------------------------------------------

/** Solve Ax = b by Gaussian elimination with partial pivoting. */
const solve = (A: number[][], b: number[]): number[] => {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    if (Math.abs(M[c][c]) < 1e-12) continue; // a dead feature; leave it at zero
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((row, i) => (Math.abs(row[i]) < 1e-12 ? 0 : row[n] / row[i]));
};

/**
 * Standardize, fit, unstandardize.
 *
 * The features run from 0-3 (lookReady) to 0-150 (trail), and one ridge penalty
 * across raw columns like that would tax the small ones into nothing.
 */
/*
 * One regression over a DOUBLED design: each feature appears twice, scaled by
 * (1-p) and by p. The fit therefore learns an early vector and a late one at
 * once, sharing all the data, rather than two thin fits on halves of it. This
 * is the part of Texel's method the first version left out.
 */
const fit = (samples: Sample[]): { t: Tapered; r2: number; dead: (keyof Weights)[] } => {
  const n = samples.length;
  const k = KEYS.length;
  const d = k * 2;
  const row = (s: Sample): number[] => [
    ...s.x.map((v) => v * (1 - s.p)),
    ...s.x.map((v) => v * s.p),
  ];
  const mean = new Array(d).fill(0);
  const sd = new Array(d).fill(0);
  const rows = samples.map(row);
  for (const r of rows) for (let j = 0; j < d; j++) mean[j] += r[j] / n;
  for (const r of rows) for (let j = 0; j < d; j++) sd[j] += (r[j] - mean[j]) ** 2 / n;
  for (let j = 0; j < d; j++) sd[j] = Math.sqrt(sd[j]);
  const rawSd = [...sd];
  const weak: (keyof Weights)[] = [];

  /*
   * A feature that never moves cannot be fitted, and saying so is the whole
   * point — silently returning a number for it is how a constant column came
   * back weighted +5.9.
   */
  const dead = KEYS.filter((_, j) => sd[j] < 1e-9 && sd[j + k] < 1e-9);
  for (let j = 0; j < d; j++) if (sd[j] < 1e-9) sd[j] = 1;

  /*
   * Fit the RESIDUAL: take the part of the label the anchored weights already
   * explain out of it first, so the rest of the vector measures value beyond
   * the obvious rather than re-deriving it.
   */
  const resid = samples.map((s) => s.y);

  const yMean = resid.reduce((a, v) => a + v, 0) / n;
  const A: number[][] = Array.from({ length: d }, () => new Array(d).fill(0));
  const b = new Array(d).fill(0);
  rows.forEach((r, i) => {
    const z = r.map((v, j) => (v - mean[j]) / sd[j]);
    const dy = resid[i] - yMean;
    for (let i = 0; i < d; i++) {
      b[i] += z[i] * dy;
      for (let j = i; j < d; j++) A[i][j] += z[i] * z[j];
    }
  });
  for (let i = 0; i < d; i++) {
    for (let j = 0; j < i; j++) A[i][j] = A[j][i];
    A[i][i] += LAMBDA * n;
  }

  const beta = solve(A, b);
  const early = { ...ZERO };
  const late = { ...ZERO };
  for (let j = 0; j < k; j++) {
    const key = KEYS[j];
    early[key] = rawSd[j] < MIN_SD ? 0 : beta[j] / sd[j];
    late[key] = rawSd[j + k] < MIN_SD ? 0 : beta[j + k] / sd[j + k];
    if (rawSd[j] < MIN_SD && rawSd[j + k] < MIN_SD) weak.push(key);
  }
  const t: Tapered = { early, late };

  /* R² against the FULL label, anchors included — that is what the eval predicts. */
  const fullMean = samples.reduce((a, s) => a + s.y, 0) / n;
  let ssRes = 0;
  let ssTot = 0;
  rows.forEach((r, i) => {
    let pred = yMean;
    for (let j = 0; j < d; j++) {
      if (rawSd[j] < MIN_SD) continue;
      pred += beta[j] * ((r[j] - mean[j]) / sd[j]);
    }
    ssRes += (samples[i].y - pred) ** 2;
    ssTot += (samples[i].y - fullMean) ** 2;
  });
  return { t, r2: 1 - ssRes / ssTot, dead: [...new Set([...dead, ...weak])] };
};

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------

const botFor = (name: string, rng: () => number) =>
  name === "random" ? makeRandomBot(rng) : makeBot(ARCHETYPES[name], rng);

/** A bot driven by an arbitrary weight vector. */
const weighted = (w: Weights, rng: () => number) =>
  SEARCH ? makeSearchBot(w, rng, BEAM_W, BEAM_D) : makeBot(w, rng);

let current: Tapered | null = null;

const fs = await import("node:fs");

/** Games already banked, and their samples. */
const resume = (): { done: Set<number>; samples: Sample[] } => {
  const done = new Set<number>();
  const samples: Sample[] = [];
  if (!CHECKPOINT || !fs.existsSync(CHECKPOINT)) return { done, samples };
  let torn = 0;
  for (const line of fs.readFileSync(CHECKPOINT, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const rec = JSON.parse(line) as { g: number; s: Sample[] };
      done.add(rec.g);
      for (const smp of rec.s) samples.push(smp);
    } catch {
      torn++; // a partial line from an interrupted write
    }
  }
  console.log(
    `resuming from ${CHECKPOINT}: ${done.size} games, ${samples.length} positions` +
      `${torn ? `, ${torn} torn line(s) skipped` : ""}`,
  );
  return { done, samples };
};

for (let iter = 0; iter < ITERS; iter++) {
  // Only the first pass resumes; later iterations sample from a new bot.
  const banked = iter === 0 ? resume() : { done: new Set<number>(), samples: [] as Sample[] };
  const samples: Sample[] = banked.samples;
  let stalled = 0;

  const started = Date.now();
  for (let g = 0; g < GAMES; g++) {
    /*
     * Progress, because a search-generated corpus is an overnight job and a
     * silent terminal for twelve hours is indistinguishable from a hung one.
     */
    if (g > 0 && g % 25 === 0) {
      const ran = Math.max(1, g - banked.done.size);
      const per = (Date.now() - started) / 1000 / ran;
      const left = ((GAMES - g) * per) / 60;
      console.log(
        `  ${g}/${GAMES} games, ${per.toFixed(1)}s each, ~${left.toFixed(0)} min left` +
          `${stalled ? `, ${stalled} stalled` : ""}`,
      );
    }
    if (banked.done.has(g)) continue;
    const before = samples.length;
    const rng = mulberry(g * 7919 + iter * 104729 + 11);
    /*
     * Iteration 0 has no fitted weights yet, so the corpus comes from the
     * archetypes. After that the fitted bot plays, but always against an
     * archetype rather than itself: a corpus of one strategy only teaches the
     * fit what beats that strategy.
     */
    const seed = ARCHETYPES[CORE[g % CORE.length]] ?? ARCHETYPES.rotting;
    /*
     * The fitted bot plays from iteration 1 on, but never against itself: a
     * corpus of one strategy only teaches the fit what beats that strategy.
     * Its opponent is always drawn from the perturbed population.
     */
    const a =
      current === null
        ? weighted(perturb(seed, rng, g % 3 === 0), rng)
        : makeTaperedBot(current, rng);
    void 0;
    const b = weighted(perturb(seed, rng, g % 3 === 1), rng);
    if (!playAndRecord(g % 2 === 0 ? { 1: a, 2: b } : { 1: b, 2: a }, g + iter * 1000, samples)) {
      stalled++;
    }
    if (CHECKPOINT && iter === 0) {
      // Rounded: full precision triples the file for digits the fit cannot use.
      const fresh = samples.slice(before).map((smp) => ({
        x: smp.x.map((v) => Math.round(v * 1000) / 1000),
        y: smp.y,
        p: Math.round(smp.p * 1000) / 1000,
      }));
      fs.appendFileSync(CHECKPOINT, `${JSON.stringify({ g, s: fresh })}\n`);
    }
  }

  const { t: fitted, r2, dead } = fit(samples);

  /*
   * Refuse a fit that came apart, rather than feeding it to the next iteration.
   *
   * An earlier run returned every weight as exactly 0 with R² NaN and carried
   * on regardless: iteration 1 then played a bot with a null evaluation, which
   * produced a degenerate corpus, which made iteration 2 worse still. A fit
   * this bad is a signal about the corpus, and the loop must not launder it
   * into weights.
   */
  const finite = KEYS.every((k) => Number.isFinite(fitted.early[k]) && Number.isFinite(fitted.late[k]));
  const alive = KEYS.some((k) => Math.abs(fitted.early[k]) > 1e-9 || Math.abs(fitted.late[k]) > 1e-9);
  if (!Number.isFinite(r2) || !finite || !alive) {
    console.error(
      `iteration ${iter}: fit collapsed (R² ${r2}, ` +
        `${alive ? "non-finite weights" : "every weight zero"}). ` +
        "The corpus does not support a fit; widen the population (--sigma) or " +
        "play more games. Keeping the previous weights.",
    );
    if (current === null) process.exit(1);
    continue;
  }

  current = fitted;
  console.log(
    `label=${LABEL} lambda=${LAMBDA}  ` +
    `iteration ${iter}: ${samples.length} positions from ${GAMES} games` +
      `${stalled ? ` (${stalled} stalled)` : ""}, R² ${r2.toFixed(3)}` +
      (dead.length ? `  — NOT IDENTIFIED (too little variation): ${dead.join(", ")}` : ""),
  );
}

console.log("\nfitted weights, in trail points of final margin:\n");
console.log("  feature              EARLY      LATE     shift");
const rows = KEYS.map((k) => ({
  k,
  e: current!.early[k],
  l: current!.late[k],
})).sort((x, y) => Math.max(Math.abs(y.e), Math.abs(y.l)) - Math.max(Math.abs(x.e), Math.abs(x.l)));
for (const r of rows) {
  const shift = r.l - r.e;
  const arrow = Math.abs(shift) < 0.2 ? "  " : shift > 0 ? "up" : "dn";
  console.log(
    `  ${String(r.k).padEnd(18)}${r.e.toFixed(2).padStart(8)}${r.l.toFixed(2).padStart(10)}` +
      `${shift.toFixed(2).padStart(10)} ${arrow}`,
  );
}

/*
 * The prediction to check the taper against, from the designer: buy cards
 * early, play them late. `animals` should be worth more in the early vector and
 * the cashing terms more in the late one. If it comes back that way the taper
 * has found something real rather than split noise in two.
 */
console.log("\nbuy early, play late — does the fit agree?");
for (const k of ["animals", "remnantPayoff", "effigies", "soulSinks", "remnantCash"] as (keyof Weights)[]) {
  const e = current!.early[k];
  const l = current!.late[k];
  console.log(`  ${String(k).padEnd(16)} early ${e.toFixed(2).padStart(7)}   late ${l.toFixed(2).padStart(7)}   ${l > e ? "rises" : "falls"}`);
}

const t = current!.late.trail;
console.log(
  `\nsanity: late-game trail is ${t.toFixed(3)} — ${
    t > 0.05 ? "positive, as it must be." : "NOT POSITIVE; do not trust the rest."
  }`,
);
fs.writeFileSync(OUT, `${JSON.stringify(current, null, 2)}\n`);
console.log(`\nwritten to ${OUT}`);

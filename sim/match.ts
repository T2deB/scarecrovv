/*
 * Head-to-head between two weight vectors, on paired seeds.
 *
 * A tuning run produces numbers; this is what says whether they are better.
 *
 * PAIRED, and that is the whole design. Both vectors play the same seed — same
 * shuffle, same market order, same opening dice — once from each seat. The
 * difference in trail is then a comparison of two decisions about one deal
 * rather than two independent samples, which removes almost all the variance
 * that would otherwise need hundreds of games to average out. newGame already
 * pins Math.random for exactly this reason.
 *
 * The statistic is the mean TRAIL MARGIN, not the win rate. Win rate throws
 * away how much a game was won by, and with draws as common as they are here it
 * wastes most of the signal.
 *
 *   node --import ./register.mjs match.ts <a.json|archetype> <b.json|archetype> [games]
 */

import { readFileSync } from "node:fs";
import { playGame } from "./harness.ts";
import {
  ARCHETYPES,
  makeBot,
  makeRandomBot,
  makeSearchBot,
  makeTaperedBot,
} from "./bot.ts";
import { ZERO } from "../src/bot.ts";
import type { Tapered, Weights } from "../src/bot.ts";

type Any = any;

const argv = process.argv.slice(2);
/*
 * Run both sides through the turn-level search instead of one-ply. This is the
 * only honest test of a fitted vector: the fit is correlational, which is fine
 * as a leaf evaluation inside a search and useless as a greedy policy, so a
 * one-ply match measures the wrong thing. Costs ~35s a game against ~1s.
 */
const SEARCH = argv.includes("--search");
/*
 * Start seeds here instead of 0. tune.ts generates its corpus from newGame
 * seeds 0..N, so a default match scores a fitted vector on the very deck orders
 * it was fitted to. The bots differ, so it is not direct leakage -- but "the
 * measurement was the bug" has been true often enough in this project that a
 * held-out range is worth 25 minutes.
 */
const OFFSET = Number(
  (argv.find((a) => a.startsWith("--offset=")) ?? "--offset=0").slice(9),
);
const [aArg, bArg, gamesArg] = argv.filter((a) => !a.startsWith("--"));
if (!aArg || !bArg) {
  console.error(
    "usage: match.ts <a.json|archetype> <b.json|archetype> [games] [--search]",
  );
  process.exit(1);
}
const GAMES = Number(gamesArg ?? 100);

type Side = { name: string; w: Weights | null; t: Tapered | null };

const load = (spec: string): Side => {
  if (spec === "random") return { name: "random", w: null, t: null };
  if (ARCHETYPES[spec]) return { name: spec, w: ARCHETYPES[spec], t: null };
  const raw = JSON.parse(readFileSync(spec, "utf8")) as Any;
  const name = spec.replace(/^.*\//, "");
  // A tapered fit is {early, late}; a flat one is a bare weight object.
  if (raw && raw.early && raw.late) {
    return {
      name: `${name} (tapered)`,
      w: null,
      t: { early: { ...ZERO, ...raw.early }, late: { ...ZERO, ...raw.late } },
    };
  }
  return { name, w: { ...ZERO, ...(raw as Partial<Weights>) }, t: null };
};

const A = load(aArg);
const B = load(bArg);

const mulberry = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) >>> 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

const driver = SEARCH ? makeSearchBot : makeBot;

const make = (side: Side, rng: () => number) =>
  side.t
    ? makeTaperedBot(side.t, rng, driver)
    : side.w
      ? driver(side.w, rng)
      : makeRandomBot(rng);

/** Per seed: A's trail minus B's, averaged over the two seatings. */
const diffs: number[] = [];
let aWins = 0;
let bWins = 0;
let draws = 0;
let stalled = 0;
let aTrail = 0;
let bTrail = 0;

for (let seed = OFFSET; seed < OFFSET + GAMES; seed++) {
  let pairDiff = 0;
  let ok = true;
  for (const swap of [false, true]) {
    const rng = mulberry(seed * 2 + (swap ? 1 : 0));
    const r = playGame(
      [1, 2],
      swap
        ? { 1: make(B, rng), 2: make(A, rng) }
        : { 1: make(A, rng), 2: make(B, rng) },
      seed,
    );
    if (r.stalled) {
      ok = false;
      break;
    }
    const t: Record<number, number> = {};
    for (const sm of ((r.metaData as Any).summary ?? []) as Any[]) t[sm.playerId] = sm.trail;
    const a = swap ? (t[2] ?? 0) : (t[1] ?? 0);
    const b = swap ? (t[1] ?? 0) : (t[2] ?? 0);
    pairDiff += (a - b) / 2;
    aTrail += a / 2;
    bTrail += b / 2;
    if (a > b) aWins++;
    else if (b > a) bWins++;
    else draws++;
  }
  if (ok) diffs.push(pairDiff);
  else stalled++;
}

const n = diffs.length;
const mean = diffs.reduce((x, y) => x + y, 0) / Math.max(1, n);
const variance = diffs.reduce((x, y) => x + (y - mean) ** 2, 0) / Math.max(1, n - 1);
const se = Math.sqrt(variance / Math.max(1, n));
const lo = mean - 1.96 * se;
const hi = mean + 1.96 * se;

console.log(`\n${A.name}  vs  ${B.name}`);
console.log(
  `${n} paired seeds, ${n * 2} games, ${SEARCH ? "turn-level search" : "one-ply"}` +
    `, seeds ${OFFSET}..${OFFSET + GAMES - 1}` +
    `${stalled ? `, ${stalled} discarded (stalled)` : ""}\n`,
);
console.log(`  mean trail      ${(aTrail / Math.max(1, n)).toFixed(1)}  vs  ${(bTrail / Math.max(1, n)).toFixed(1)}`);
console.log(`  games won       ${aWins}  vs  ${bWins}   (${draws} drawn)`);
console.log(`  margin          ${mean >= 0 ? "+" : ""}${mean.toFixed(2)} trail  95% CI [${lo.toFixed(2)}, ${hi.toFixed(2)}]`);
console.log(
  `\n  ${
    lo > 0
      ? `${A.name} is better, and the interval excludes zero.`
      : hi < 0
        ? `${B.name} is better, and the interval excludes zero.`
        : "no significant difference — the interval spans zero."
  }`,
);

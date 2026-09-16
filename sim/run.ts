/*
 * Round-robin tournament driver.
 *
 * Usage:
 *   node --import ./register.mjs run.ts [gamesPerPairing] [--out results.json]
 *
 * Every archetype plays every other (and itself) N times, with seats swapped
 * half the time so first-player advantage cancels out. Aggregates win rate,
 * mean trail, game length, card pick frequency and where points came from.
 */

import { playGame } from "./harness.ts";
import { ARCHETYPES, CORE, makeBot, makeRandomBot } from "./bot.ts";
import { ANIMAL_CARDS, cardByKey } from "../src/cards.ts";

type Any = any;

const args = process.argv.slice(2);
const GAMES = Number(args.find((a) => /^\d+$/.test(a)) ?? 30);
const outFlag = args.indexOf("--out");
const OUT = outFlag >= 0 ? args[outFlag + 1] : null;
// --bots a,b,c restricts the field. A full round robin is O(n^2) and the
// animal-first bots are slow (more cards owned means more legal actions means
// more clones per decision), so a curated field answers a specific question far
// faster than pitting all 23 against each other.
const botsFlag = args.indexOf("--bots");
const ONLY = botsFlag >= 0 ? args[botsFlag + 1].split(",") : null;

const mulberry = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) >>> 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

const chooserFor = (name: string, rng: () => number) =>
  name === "random" ? makeRandomBot(rng) : makeBot(ARCHETYPES[name], rng);

type Agg = {
  games: number;
  wins: number;
  draws: number;
  trail: number;
  turns: number;
  siphons: Record<string, number>;
  points: Record<string, number>;
};

const blank = (): Agg => ({
  games: 0, wins: 0, draws: 0, trail: 0, turns: 0, siphons: {}, points: {},
});

// CORE, not every archetype: a round robin is O(n^2), and the extra bots were
// interpolations between ones already here. Name them with --bots to include them.
const FIELD = ONLY ?? CORE;
for (const n of FIELD) {
  if (n !== "random" && !ARCHETYPES[n]) throw new Error(`unknown bot: ${n}`);
}

const agg: Record<string, Agg> = {};
const head: Record<string, Record<string, { w: number; g: number }>> = {};
for (const n of FIELD) {
  agg[n] = blank();
  head[n] = {};
  for (const m of FIELD) head[n][m] = { w: 0, g: 0 };
}

let stalled = 0;
let played = 0;
const started = Date.now();

for (let i = 0; i < FIELD.length; i++) {
  for (let j = i; j < FIELD.length; j++) {
    const a = FIELD[i];
    const b = FIELD[j];

    for (let g = 0; g < GAMES; g++) {
      // Swap seats on odd games so seat order cancels out.
      const swap = g % 2 === 1;
      const p1 = swap ? b : a;
      const p2 = swap ? a : b;
      const rng = mulberry(1000 * i + 100 * j + g + 7);

      const result = playGame([1, 2], {
        1: chooserFor(p1, rng),
        2: chooserFor(p2, rng),
      });
      played++;
      if (result.stalled) {
        stalled++;
        continue;
      }

      const meta = result.metaData as Any;
      const summary = meta.summary ?? [];
      if (summary.length < 2) continue;

      const byId: Record<number, Any> = {};
      for (const s of summary) byId[s.playerId] = s;
      const names: Record<number, string> = { 1: p1, 2: p2 };

      const t1 = byId[1]?.trail ?? 0;
      const t2 = byId[2]?.trail ?? 0;
      const winner = t1 === t2 ? null : t1 > t2 ? 1 : 2;

      for (const pid of [1, 2]) {
        const name = names[pid];
        const A = agg[name];
        A.games++;
        A.trail += byId[pid]?.trail ?? 0;
        A.turns += byId[pid]?.turns ?? 0;
        if (winner === null) A.draws++;
        else if (winner === pid) A.wins++;
      }

      head[p1][p2].g++;
      head[p2][p1].g++;
      if (winner === 1) head[p1][p2].w++;
      if (winner === 2) head[p2][p1].w++;

      // Card acquisitions and point sources, per archetype.
      for (const ev of meta.stats ?? []) {
        const name = names[ev.p];
        if (!name) continue;
        if ((ev.k === "siphon" || ev.k === "dieSiphon") && ev.c) {
          agg[name].siphons[ev.c] = (agg[name].siphons[ev.c] ?? 0) + 1;
        }
        if (ev.k === "advance" && ev.c) {
          agg[name].points[ev.c] = (agg[name].points[ev.c] ?? 0) + (ev.n ?? 0);
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

const pct = (n: number, d: number) => (d === 0 ? "  —  " : `${((100 * n) / d).toFixed(1)}%`);
const rows = FIELD.map((n) => {
  const A = agg[n];
  return {
    bot: n,
    games: A.games,
    winRate: A.games ? A.wins / A.games : 0,
    drawRate: A.games ? A.draws / A.games : 0,
    meanTrail: A.games ? A.trail / A.games : 0,
    meanTurns: A.games ? A.turns / A.games : 0,
  };
}).sort((x, y) => y.winRate - x.winRate);

console.log(`\n${played} games (${GAMES} per pairing), ${stalled} stalled, ${((Date.now() - started) / 1000).toFixed(1)}s\n`);
console.log("bot                win%    draw%   trail   turns");
console.log("-".repeat(52));
for (const r of rows) {
  console.log(
    r.bot.padEnd(18) +
      pct(r.winRate, 1).padStart(6) +
      pct(r.drawRate, 1).padStart(9) +
      r.meanTrail.toFixed(1).padStart(8) +
      r.meanTurns.toFixed(1).padStart(8),
  );
}

// Cards nobody ever buys are the clearest balance signal available.
const totalSiphons: Record<string, number> = {};
for (const n of FIELD) {
  for (const [k, v] of Object.entries(agg[n].siphons)) {
    totalSiphons[k] = (totalSiphons[k] ?? 0) + v;
  }
}
const never = ANIMAL_CARDS.filter((c) => !totalSiphons[c.key]);
const ranked = Object.entries(totalSiphons).sort((a, b) => b[1] - a[1]);

console.log(`\nMost-bought animals:`);
for (const [k, v] of ranked.slice(0, 8)) {
  console.log(`  ${(cardByKey(k)?.name ?? k).padEnd(28)} ${v}`);
}
console.log(`\nLeast-bought animals:`);
for (const [k, v] of ranked.slice(-8)) {
  console.log(`  ${(cardByKey(k)?.name ?? k).padEnd(28)} ${v}`);
}
console.log(`\nNever bought in any game: ${never.length} of ${ANIMAL_CARDS.length}`);
if (never.length) console.log("  " + never.map((c) => c.name).join(", "));

// Where the points came from, pooled across every bot.
const points: Record<string, number> = {};
for (const n of FIELD) {
  for (const [k, v] of Object.entries(agg[n].points)) points[k] = (points[k] ?? 0) + v;
}
const totalPoints = Object.values(points).reduce((a, b) => a + b, 0);
console.log(`\nPoint sources (all bots pooled):`);
for (const [k, v] of Object.entries(points).sort((a, b) => b[1] - a[1]).slice(0, 10)) {
  console.log(`  ${(cardByKey(k)?.name ?? k).padEnd(28)} ${v}  (${pct(v, totalPoints)})`);
}

if (OUT) {
  const fs = await import("node:fs");
  fs.writeFileSync(OUT, JSON.stringify({ games: GAMES, rows, head, agg }, null, 2));
  console.log(`\nWrote ${OUT}`);
}

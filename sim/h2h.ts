/* Turn-level search against one-ply, same weights, paired seeds. */
import { playGame } from "./harness.ts";
import { ARCHETYPES, makeBot, makeSearchBot } from "./bot.ts";
type Any = any;
const mulberry = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) >>> 0; let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
/*
 * The general hand-made vector, not a single-domain archetype. This comparison
 * holds weights constant and varies only the DRIVER, so the choice barely
 * mattered -- but reading "weights = base" invited the wrong conclusion
 * about which bot the result describes.
 */
const w = ARCHETYPES.base;
const SEEDS = Number(process.argv[2] ?? 20);
const diffs: number[] = [];
let sT = 0, sG = 0, n = 0;
for (let seed = 0; seed < SEEDS; seed++) {
  let pair = 0; let ok = true;
  for (const swap of [false, true]) {
    const rng = mulberry(seed * 2 + (swap ? 1 : 0));
    const search = makeSearchBot(w, rng, 3, 6);
    const greedy = makeBot(w, rng);
    const r = playGame([1, 2], swap ? { 1: greedy, 2: search } : { 1: search, 2: greedy }, seed);
    if (r.stalled) { ok = false; break; }
    const t: Any = {};
    for (const sm of ((r.metaData as Any).summary ?? []) as Any[]) t[sm.playerId] = sm.trail;
    const a = swap ? t[2] : t[1];
    const b = swap ? t[1] : t[2];
    pair += (a - b) / 2; sT += a / 2; sG += b / 2;
  }
  if (ok) { diffs.push(pair); n++; }
}
const m = diffs.reduce((a, b) => a + b, 0) / Math.max(1, n);
const v = diffs.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, n - 1);
const se = Math.sqrt(v / Math.max(1, n));
console.log(`turn-level search vs one-ply, ${n} paired seeds (${n * 2} games), weights = base\n`);
console.log(`  mean trail   ${(sT / Math.max(1, n)).toFixed(2)}  vs  ${(sG / Math.max(1, n)).toFixed(2)}`);
console.log(`  margin       ${m >= 0 ? "+" : ""}${m.toFixed(2)} trail  95% CI [${(m - 1.96 * se).toFixed(2)}, ${(m + 1.96 * se).toFixed(2)}]`);
console.log(`  ${m - 1.96 * se > 0 ? "search wins, interval excludes zero." : m + 1.96 * se < 0 ? "one-ply wins, interval excludes zero." : "no significant difference."}`);

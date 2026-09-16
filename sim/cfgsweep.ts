/*
 * The cheapest beam that keeps the win.
 *
 * Turn-level search beats one-ply by +16.3 trail at width 8 / depth 8, and
 * costs ~30s a game — fine for a live opponent, far too slow to generate
 * tuning corpora. Root diversity tripled the node count when it went in, so
 * there may be a much cheaper config that plays as well.
 *
 * Each row is a paired match against the one-ply bot at identical weights:
 * same shuffle, same market, same dice, once from each seat.
 */
import { playGame } from "./harness.ts";
import { ARCHETYPES, makeBot, makeSearchBot } from "./bot.ts";
type Any = any;
const mulberry = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) >>> 0; let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const w = ARCHETYPES.rotting;
const SEEDS = Number(process.argv[2] ?? 8);

console.log(`beam configs vs one-ply, ${SEEDS} paired seeds each, weights = rotting\n`);
console.log("width depth   margin      95% CI            s/game   stalled");
for (const [width, depth] of [[8, 8], [6, 8], [4, 8], [8, 6], [12, 8], [6, 10]] as const) {
  const diffs: number[] = [];
  let stalled = 0;
  const t0 = Date.now();
  for (let seed = 0; seed < SEEDS; seed++) {
    let pair = 0; let ok = true;
    for (const swap of [false, true]) {
      const rng = mulberry(seed * 2 + (swap ? 1 : 0));
      const s = makeSearchBot(w, rng, width, depth);
      const g = makeBot(w, rng);
      const r = playGame([1, 2], swap ? { 1: g, 2: s } : { 1: s, 2: g }, seed);
      if (r.stalled) { ok = false; break; }
      const t: Any = {};
      for (const sm of ((r.metaData as Any).summary ?? []) as Any[]) t[sm.playerId] = sm.trail;
      pair += ((swap ? t[2] : t[1]) - (swap ? t[1] : t[2])) / 2;
    }
    if (ok) diffs.push(pair); else stalled++;
  }
  const n = Math.max(1, diffs.length);
  const m = diffs.reduce((a, b) => a + b, 0) / n;
  const v = diffs.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, n - 1);
  const se = Math.sqrt(v / n);
  const secs = (Date.now() - t0) / 1000 / Math.max(1, SEEDS * 2);
  console.log(
    `${String(width).padStart(5)}${String(depth).padStart(6)}` +
    `${(m >= 0 ? "+" : "") + m.toFixed(2).padStart(8)}` +
    `   [${(m - 1.96 * se).toFixed(1)}, ${(m + 1.96 * se).toFixed(1)}]`.padEnd(20) +
    `${secs.toFixed(1).padStart(7)}${String(stalled).padStart(9)}`,
  );
}

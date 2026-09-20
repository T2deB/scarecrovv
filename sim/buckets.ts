/*
 * Does splitting "animals" by domain x type separate anything?
 *
 * Deliberately NOT a tuner run. The question is whether the buckets correlate
 * with the outcome at all, and that needs one row per game-player -- a deck
 * composition and a final margin -- not 250 clustered positions per game.
 * Running it this way costs games instead of a feature-set change, and answers
 * the actual question directly.
 */
import { playGame } from "./harness.ts";
import { makeSearchBot, makeTaperedBot } from "./bot.ts";
import { ZERO } from "../src/bot.ts";
import { cardByKey } from "../src/cards.ts";
import { appendFileSync, existsSync, readFileSync } from "node:fs";
type Any = any;
const argv = process.argv.slice(2);
const bare = argv.filter((a) => !a.startsWith("--"));
const WHICH = bare[0] ?? "hyb-animals.json";
const GAMES = Number(bare[1] ?? 60);
const OUT = (argv.find((a) => a.startsWith("--out=")) ?? "--out=wk/buckets.jsonl").slice(6);
const SEED0 = 820000;

const mul = (s: number) => () => { s = (s + 0x6d2b79f5) >>> 0; let t = s;
  t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const raw = JSON.parse(readFileSync(WHICH, "utf8")) as Any;
const tap = { early: { ...ZERO, ...raw.early }, late: { ...ZERO, ...raw.late } };

const done = new Set<number>();
if (existsSync(OUT)) for (const l of readFileSync(OUT, "utf8").split("\n"))
  if (l.trim()) try { done.add(JSON.parse(l).seed); } catch {}
if (done.size) console.log(`resuming: ${done.size} games banked`);

for (let i = 0; i < GAMES; i++) {
  const seed = SEED0 + i;
  if (done.has(seed)) continue;
  const rng = mul(seed);
  const r = playGame([1, 2],
    { 1: makeTaperedBot(tap, rng, makeSearchBot), 2: makeTaperedBot(tap, rng, makeSearchBot) }, seed);
  if (r.stalled) continue;
  const m = r.metaData as Any;
  const rows = (m.summary as Any[]).map((p) => {
    const b: Record<string, number> = {};
    for (const [key, n] of Object.entries(p.deck as Record<string, number>)) {
      const d = cardByKey(key) as Any;
      if (!d || d.category !== "Animal") continue;
      b[`${d.domain}:${d.type}`] = (b[`${d.domain}:${d.type}`] ?? 0) + (n as number);
    }
    return { id: p.playerId, trail: p.trail, b };
  });
  appendFileSync(OUT, `${JSON.stringify({ seed, rows })}\n`);
  if ((i + 1) % 10 === 0) console.log(`  ${i + 1}/${GAMES}`);
}
console.log(`written to ${OUT}`);

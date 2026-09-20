/*
 * Fit weights so the one-ply evaluation RANKS MOVES the way the search does.
 *
 * Softmax cross-entropy over the legal moves at each decision. For options
 * x_0..x_k, where x_0 is the one the search played:
 *
 *   s_i = w . x_i          L = -log( exp(s_0) / sum_i exp(s_i) )
 *
 * Tapered exactly as the outcome fit is: the feature row becomes
 * [x*(1-p), x*p], so early and late weights are learned together.
 *
 * SCALE. A ranking loss only pins w down up to a positive multiple -- doubling
 * every weight changes no ordering. That is harmless for a greedy bot and NOT
 * harmless inside the search, where the stopping rule compares a line's value
 * against standing still and UNSPENT_ACTION is an absolute 12. So the fitted
 * vector is rescaled at the end to put `trail` where the reference vector has
 * it, which leaves the ranking untouched and makes the absolute terms mean what
 * they meant before.
 *
 * usage: distilfit.ts <corpus.jsonl> [--out=f.json] [--ref=hyb-animals.json]
 *                     [--iters=400] [--l2=1e-4] [--lr=0.05]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { ZERO } from "../src/bot.ts";

type Any = any;
const argv = process.argv.slice(2);
const bare = argv.filter((a) => !a.startsWith("--"));
const val = (k: string, d: string) =>
  (argv.find((a) => a.startsWith(`--${k}=`)) ?? `--${k}=${d}`).slice(k.length + 3);

const CORPUS = bare[0] ?? "wk/distill.jsonl";
const OUT = val("out", "wk/distilled.json");
const REF = val("ref", "hyb-animals.json");
const ITERS = Number(val("iters", "400"));
const L2 = Number(val("l2", "1e-4"));
const LR = Number(val("lr", "0.05"));

const KEYS = Object.keys(ZERO);
const K = KEYS.length;
const D = K * 2; // early block, then late block

type Row = { p: number; x: number[][] };
const rows: Row[] = [];
let games = 0;
for (const line of readFileSync(CORPUS, "utf8").split("\n")) {
  if (!line.trim()) continue;
  try {
    const r = JSON.parse(line) as { g: number; d: Row[] };
    games++;
    for (const d of r.d) if (d.x.length >= 2) rows.push(d);
  } catch { /* torn line from an interrupted write */ }
}
console.log(`${rows.length} decisions from ${games} games, ` +
  `${(rows.reduce((a, r) => a + r.x.length, 0) / Math.max(1, rows.length)).toFixed(1)} options each`);

/* Standardise per column over every option vector, so one learning rate suits
 * features that run 0-3 and features that run 0-150. */
const mean = new Array(K).fill(0);
const sd = new Array(K).fill(0);
let nOpt = 0;
for (const r of rows) for (const x of r.x) { nOpt++; for (let j = 0; j < K; j++) mean[j] += x[j]; }
for (let j = 0; j < K; j++) mean[j] /= Math.max(1, nOpt);
for (const r of rows) for (const x of r.x) for (let j = 0; j < K; j++) sd[j] += (x[j] - mean[j]) ** 2;
for (let j = 0; j < K; j++) sd[j] = Math.sqrt(sd[j] / Math.max(1, nOpt)) || 1;

/* Centring is not allowed here. Softmax over options depends only on
 * DIFFERENCES between them, so subtracting a per-column constant cancels --
 * but only if it is the same constant for every option, which it is. Scaling
 * is what we actually need; the mean is kept only to compute sd. */
const z = (x: number[], p: number): number[] => {
  const out = new Array(D).fill(0);
  for (let j = 0; j < K; j++) {
    const v = x[j] / sd[j];
    out[j] = v * (1 - p);
    out[K + j] = v * p;
  }
  return out;
};

const data = rows.map((r) => ({ opts: r.x.map((x) => z(x, r.p)) }));

/** Share of decisions where w ranks the search's choice (index 0) first. */
const top1 = (w: number[]): number => {
  let hit = 0;
  for (const d of data) {
    let best = -Infinity, bi = -1;
    for (let i = 0; i < d.opts.length; i++) {
      let s = 0;
      for (let j = 0; j < D; j++) s += w[j] * d.opts[i][j];
      if (s > best) { best = s; bi = i; }
    }
    if (bi === 0) hit++;
  }
  return hit / Math.max(1, data.length);
};

// Adam.
let w = new Array(D).fill(0);
const m = new Array(D).fill(0), v = new Array(D).fill(0);
const B1 = 0.9, B2 = 0.999, EPS = 1e-8;
const chance = data.reduce((a, d) => a + 1 / d.opts.length, 0) / Math.max(1, data.length);
console.log(`  chance top-1 = ${(chance * 100).toFixed(1)}%\n`);

for (let it = 1; it <= ITERS; it++) {
  const g = new Array(D).fill(0);
  let loss = 0;
  for (const d of data) {
    const s = d.opts.map((o) => { let t = 0; for (let j = 0; j < D; j++) t += w[j] * o[j]; return t; });
    const mx = Math.max(...s);
    const ex = s.map((t) => Math.exp(t - mx));
    const Z = ex.reduce((a, b) => a + b, 0);
    loss += -(s[0] - mx - Math.log(Z));
    for (let i = 0; i < d.opts.length; i++) {
      const c = ex[i] / Z - (i === 0 ? 1 : 0);
      for (let j = 0; j < D; j++) g[j] += c * d.opts[i][j];
    }
  }
  const n = Math.max(1, data.length);
  for (let j = 0; j < D; j++) {
    const gj = g[j] / n + L2 * w[j];
    m[j] = B1 * m[j] + (1 - B1) * gj;
    v[j] = B2 * v[j] + (1 - B2) * gj * gj;
    const mh = m[j] / (1 - B1 ** it), vh = v[j] / (1 - B2 ** it);
    w[j] -= (LR * mh) / (Math.sqrt(vh) + EPS);
  }
  if (it % 50 === 0 || it === 1)
    console.log(`  iter ${String(it).padStart(4)}  loss ${(loss / n).toFixed(4)}  top-1 ${(top1(w) * 100).toFixed(1)}%`);
}

// Unstandardise, then rescale so `trail` matches the reference vector.
const early = { ...ZERO } as Any, late = { ...ZERO } as Any;
for (let j = 0; j < K; j++) {
  early[KEYS[j]] = w[j] / sd[j];
  late[KEYS[j]] = w[K + j] / sd[j];
}
let note = "not rescaled (no reference)";
try {
  const ref = JSON.parse(readFileSync(REF, "utf8")) as Any;
  const want = ref.early?.trail ?? ref.trail;
  if (want && Math.abs(early.trail) > 1e-9) {
    const k = want / early.trail;
    for (const key of KEYS) { early[key] *= k; late[key] *= k; }
    note = `rescaled x${k.toFixed(3)} so trail(early) = ${want} (from ${REF})`;
  }
} catch { /* reference missing: leave the raw scale */ }

console.log(`\nfinal top-1 ${(top1(w) * 100).toFixed(1)}%  (chance ${(chance * 100).toFixed(1)}%)`);
console.log(`scale: ${note}\n`);
const shown = KEYS.map((k) => [k, early[k] as number, late[k] as number] as const)
  .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
console.log("  feature              EARLY      LATE");
for (const [k, e, l] of shown.slice(0, 16))
  console.log(`  ${k.padEnd(18)}${e.toFixed(2).padStart(8)}${l.toFixed(2).padStart(10)}`);
writeFileSync(OUT, JSON.stringify({ early, late }, null, 1));
console.log(`\nwritten to ${OUT}`);

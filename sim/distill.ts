/*
 * POLICY DISTILLATION corpus.
 *
 * Every other fit in this project trains on OUTCOMES: this position, this final
 * margin, fit weights to predict it. That is a weak signal and a heavily
 * confounded one. One card's contribution to a win is tiny and tangled with
 * everything else in the game, so features whose value is real but competes for
 * a resource come back at zero -- and a vector fitted that way lost to the same
 * vector with two hand corrections by 31.6 trail.
 *
 * This trains on DECISIONS instead. For each position: the feature vector of
 * every move the search considered, and which move it picked. Fit weights so
 * the cheap one-ply evaluation ranks the search's choice first.
 *
 * Why that fixes what outcomes could not:
 *   - The search is measurably the stronger player (+24 trail over one-ply with
 *     identical weights), so its choices are better-informed than the weights
 *     that produced them. That is the condition distillation needs.
 *   - The ALTERNATIVES are explicit. The label is not "animals are good", it is
 *     "here, with these twelve options, buying the animal beat the other
 *     eleven". Opportunity cost becomes part of the question instead of a
 *     confounder.
 *   - A verb the outcome corpus is too thin to price still appears here as a
 *     correct choice, so the soul blind spot has a signal to learn from.
 *
 * It is also far cheaper in games: one game yields hundreds of labelled
 * decisions rather than one outcome.
 *
 * usage: distill.ts <weights.json|archetype> <games> [--from=N] [--out=f.jsonl]
 *                   [--every=4] [--cap=12]
 */
import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { legalActions, newGame, toEngineAction, simulateSettled, type Available } from "./harness.ts";
import { ARCHETYPES, makeSearchBot, makeTaperedBot, type RankedMove } from "./bot.ts";
import { applyActions, isGameOver } from "../src/game.ts";
import { features, ZERO, phaseOf } from "../src/bot.ts";

type Any = any;
const argv = process.argv.slice(2);
const bare = argv.filter((a) => !a.startsWith("--"));
const val = (k: string, d: string) =>
  (argv.find((a) => a.startsWith(`--${k}=`)) ?? `--${k}=${d}`).slice(k.length + 3);

const WHICH = bare[0] ?? "hyb-animals.json";
const GAMES = Number(bare[1] ?? 40);
const FROM = Number(val("from", "0"));
const OUT = val("out", "wk/distill.jsonl");
/** Record one decision in EVERY. Adjacent decisions in a turn are near-duplicates. */
const EVERY = Number(val("every", "4"));
/** Keep at most this many alternatives, always including the chosen one. */
const CAP = Number(val("cap", "12"));

const KEYS = Object.keys(ZERO);
const mul = (s: number) => () => { s = (s + 0x6d2b79f5) >>> 0; let t = s;
  t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };

/** Differenced, exactly as score() sees it — mine minus theirs. */
const vec = (st: Any, me: number): number[] => {
  const them = st.players().map((p: Any) => p.playerId).filter((id: number) => id !== me);
  const mine = features(st, me) as Any;
  const out = KEYS.map((k) => mine[k] as number);
  for (const id of them) {
    const th = features(st, id) as Any;
    for (let i = 0; i < KEYS.length; i++) out[i] -= th[KEYS[i]] as number;
  }
  return out;
};

const done = new Set<number>();
if (existsSync(OUT)) for (const l of readFileSync(OUT, "utf8").split("\n"))
  if (l.trim()) try { done.add(JSON.parse(l).g); } catch {}
if (done.size) console.log(`resuming from ${OUT}: ${done.size} games`);

const raw = WHICH.endsWith(".json") ? JSON.parse(readFileSync(WHICH, "utf8")) as Any : null;
const tap = raw ? { early: { ...ZERO, ...raw.early }, late: { ...ZERO, ...raw.late } } : null;

const started = Date.now();
let fresh = 0;
for (let g = FROM; g < FROM + GAMES; g++) {
  if (done.has(g)) continue;
  const rng = mul(g * 7919 + 11);
  const st: Any = newGame([1, 2], g);
  const rows: Any[] = [];
  let seen = 0;

  const sink = (state: Any, me: number, ranked: RankedMove[], chosen: Available): void => {
    if (seen++ % EVERY !== 0) return;
    // The chosen move first, then the best alternatives, capped.
    const order = [...ranked].sort((a, b) => b.value - a.value);
    const pick = order.filter((r) => r.option === chosen);
    const rest = order.filter((r) => r.option !== chosen).slice(0, CAP - 1);
    const keep = [...pick, ...rest];
    if (keep.length < 2) return;
    const xs: number[][] = [];
    for (const r of keep) {
      try {
        const after = simulateSettled(state, r.option, "decide");
        xs.push(vec(after, me).map((v) => Math.round(v * 1000) / 1000));
      } catch { return; }
    }
    if (xs.length < 2) return;
    // Index 0 is always the search's choice. The fit never sees the ordering of
    // the rest, so it cannot learn position rather than features.
    rows.push({ p: Math.round(phaseOf(state, me) * 1000) / 1000, x: xs });
  };

  const bot = tap
    ? makeTaperedBot(tap, rng, (w, r) => makeSearchBot(w, r, 8, 6, sink))
    : makeSearchBot(ARCHETYPES[WHICH] ?? ARCHETYPES.base, rng, 8, 6, sink);
  const bots: Any = { 1: bot, 2: bot };

  let steps = 0;
  while (!isGameOver(st, {} as Any) && steps++ < 4000) {
    const act = st.activePlayerIds[0];
    if (act === undefined) break;
    st.currentPlayerId = act;
    const opts = legalActions(st, act);
    if (!opts.length) break;
    applyActions(st, toEngineAction(st, bots[act](st, act, opts)) as Any);
  }
  if (steps >= 4000) { console.log(`  game ${g} stalled, skipped`); continue; }

  appendFileSync(OUT, `${JSON.stringify({ g, d: rows })}\n`);
  fresh++;
  if (fresh % 5 === 0) {
    const per = (Date.now() - started) / 1000 / fresh;
    console.log(`  ${fresh}/${GAMES} games (shard ${FROM}), ${per.toFixed(1)}s each, ` +
      `${rows.length} decisions in the last, ~${(((GAMES - (g - FROM) - 1) * per) / 60).toFixed(0)} min left`);
  }
}
console.log(`written to ${OUT}`);

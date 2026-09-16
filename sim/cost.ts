/* Where does the time actually go? */
import { newGame, toEngineAction, simulate, simulateSettled, type Available } from "./harness.ts";
import { ARCHETYPES, makeBot } from "./bot.ts";
import { applyActions, isGameOver } from "../src/game.ts";

/*
 * Enumeration goes through the disabled-button filter. getAvailableActions
 * lists buttons the real server refuses, and a driver that applies them is
 * simulating a game no human can play. See legalActions in harness.ts.
 */
import { legalActions as __legal } from "./harness.ts";
const getAvailableActions = (st: any) => __legal(st);

type Any = any;
const mul = (s: number) => () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const w = ARCHETYPES.rotting;

// Walk a real game, recording decisions and branching, and sample clone cost.
const st = newGame([1, 2], 3);
const bot = makeBot(w, mul(3));
let decisions = 0, branchSum = 0, pieces = 0;
const mid: Any[] = [];
while (!isGameOver(st as Any, {} as Any) && decisions < 4000) {
  const act = st.activePlayerIds[0]; if (act === undefined) break;
  st.currentPlayerId = act;
  const opts = getAvailableActions(st as Any) as unknown as Available[];
  if (!opts.length) break;
  decisions++; branchSum += opts.length;
  if (decisions === 60) { mid.push(st.dump()); pieces = (st.dump() as Any).pieces?.length ?? 0; }
  applyActions(st as Any, toEngineAction(st, bot(st, act, opts)) as Any);
}
const b = branchSum / decisions;

// Cost of the three operations a search node performs.
const probe = newGame([1, 2], 3);
const pbot = makeBot(w, mul(3));
for (let i = 0; i < 60; i++) {
  const a = probe.activePlayerIds[0]; probe.currentPlayerId = a;
  const o = getAvailableActions(probe as Any) as unknown as Available[];
  applyActions(probe as Any, toEngineAction(probe, pbot(probe, a, o)) as Any);
}
probe.currentPlayerId = probe.activePlayerIds[0];
const opts = getAvailableActions(probe as Any) as unknown as Available[];

const time = (label: string, n: number, f: () => void) => {
  const t0 = Date.now(); for (let i = 0; i < n; i++) f();
  const ms = (Date.now() - t0) / n;
  console.log(`  ${label.padEnd(34)} ${ms.toFixed(3)} ms`);
  return ms;
};
console.log(`game shape: ${decisions} decisions, mean branching ${b.toFixed(1)}, ${(probe.dump() as Any).pieces.length} pieces\n`);
console.log("per search node:");
const tDump = time("state.dump()", 300, () => { probe.dump(); });
const tClone = time("dump -> JSON round trip -> load", 300, () => {
  const d = probe.dump() as Any; d.metaData = { ...d.metaData, stats: [], log: [], undo: [] };
  (probe.constructor as Any).load(JSON.parse(JSON.stringify(d)));
});
const tSim = time("simulate() (clone + apply)", 300, () => { simulate(probe, opts[0]); });
const tSet = time("simulateSettled()", 300, () => { simulateSettled(probe, opts[0]); });
const tSig = time("signature() (JSON of metaData)", 300, () => {
  const { stats, log, undo, ...rest } = (probe.metaData ?? {}) as Any; JSON.stringify(rest);
});

const perDecision = (width: number, depth: number) => b + (depth - 1) * width * b;
console.log(`\nnodes per decision (branching ${b.toFixed(1)}):`);
for (const [wd, dp] of [[1, 1], [3, 6], [8, 8], [16, 10]] as const) {
  const nodes = wd === 1 ? b : perDecision(wd, dp);
  const perGame = (nodes * (tSet + tSig) * decisions) / 1000;
  console.log(`  ${wd === 1 ? "one-ply".padEnd(12) : `beam w${wd} d${dp}`.padEnd(12)} ${nodes.toFixed(0).padStart(6)} nodes   -> ${perGame.toFixed(1)} s/game predicted`);
}

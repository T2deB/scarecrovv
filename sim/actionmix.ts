/*
 * What does the bot SPEND ITS ACTIONS ON, in detail?
 *
 * The margin says which vector wins. This says whether the game is the one the
 * designer wanted: actions going into cards you acquired and played, rather
 * than into standard actions nobody earned. A design question, not a balance
 * one, and the thing an Effigy cap is meant to move.
 *
 * Attribution is exact rather than inferred. metaData.p[id].actions is the
 * per-turn counter and game.ts charges it in exactly one place, so an option
 * that increments it spent an action and one that does not was free. An earlier
 * version counted STAT EVENTS instead and reported 58 actions in a 30-action
 * game -- `tether` is a trigger firing, not an action.
 *
 * Every decision is written to --out as JSONL, one line per game, appended as
 * the game finishes. A crash costs the game in flight, not the run: rerun the
 * same command and it resumes, skipping seeds already banked.
 *
 * usage: actionmix.ts <weights.json|archetype> [games] [--seed=N] [--out=f.jsonl]
 */
import { existsSync, appendFileSync, readFileSync } from "node:fs";
import { legalActions, newGame, toEngineAction } from "./harness.ts";
import { ARCHETYPES, makeBot, makeSearchBot, makeTaperedBot } from "./bot.ts";
import { applyActions, isGameOver } from "../src/game.ts";
import { cardsIn, zone } from "../src/state.ts";
import { ZERO } from "../src/bot.ts";

type Any = any;
const argv = process.argv.slice(2);
const bare = argv.filter((a) => !a.startsWith("--"));
const val = (k: string, d: string) =>
  (argv.find((a) => a.startsWith(`--${k}=`)) ?? `--${k}=${d}`).slice(k.length + 3);

const WHICH = bare[0] ?? "base";
const GAMES = Number(bare[1] ?? 10);
const SEED0 = Number(val("seed", "700000"));
const OUT = val("out", "");
const ONEPLY = argv.includes("--one-ply");

const mul = (s: number) => () => {
  s = (s + 0x6d2b79f5) >>> 0; let t = s;
  t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

const driver = ONEPLY ? makeBot : makeSearchBot;
const make = (rng: () => number) => {
  if (WHICH.endsWith(".json")) {
    const raw = JSON.parse(readFileSync(WHICH, "utf8")) as Any;
    return makeTaperedBot(
      { early: { ...ZERO, ...raw.early }, late: { ...ZERO, ...raw.late } }, rng, driver,
    );
  }
  return driver(ARCHETYPES[WHICH] ?? ARCHETYPES.base, rng);
};

type Rec = {
  seed: number;
  stalled: boolean;
  players: Any[];
  /**
   * One per decision: round, phase, label, whether it cost an action, and the
   * label of the decision that action is CREDITED to (see INIT_PHASES).
   */
  d: {
    p: number; r: number; ph: string; a: 0 | 1; l: string; c?: string;
    /*
     * Zone sizes BEFORE the decision: deck, hand, discard. Cheap, and it is
     * what any "how thin was the deck when X happened" question needs -- the
     * empty-deck turn a player can draw their whole hand on, and (once the
     * mechanic exists) the deck size at the moment a lock-out is declared.
     */
    dk: number; hd: number; dc: number;
  }[];
};

/** Already-banked seeds, so a rerun continues instead of starting over. */
const banked = new Map<number, Rec>();
if (OUT && existsSync(OUT)) {
  let torn = 0;
  for (const line of readFileSync(OUT, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try { const r = JSON.parse(line) as Rec; banked.set(r.seed, r); }
    catch { torn++; }
  }
  console.log(`resuming from ${OUT}: ${banked.size} games${torn ? `, ${torn} torn` : ""}`);
}

const MAX_STEPS = 4000;

/*
 * Phases where a player STARTS something. game.ts charges an action as "owed"
 * rather than spent, so a card that pauses for a choice is billed on a later
 * decision in the `effect` phase -- measured, 92 of 192 charges land there, and
 * crediting them where they fall reports "Play a card" as free. Every charge is
 * credited to the most recent decision made in one of these phases instead.
 */
const INIT_PHASES = new Set(["play", "effigy", "remnants"]);

const playOne = (seed: number): Rec => {
  const rng = mul(seed);
  const st: Any = newGame([1, 2], seed);
  const bots: Any = { 1: make(rng), 2: make(rng) };
  const d: Rec["d"] = [];
  const lastInit: Record<number, string> = {};
  let steps = 0;
  while (!isGameOver(st, {} as Any) && steps < MAX_STEPS) {
    const act = st.activePlayerIds[0];
    if (act === undefined) break;
    st.currentPlayerId = act;
    const options = legalActions(st, act);
    if (options.length === 0) break;
    const chosen = bots[act](st, act, options);
    const md = st.metaData as Any;
    /*
     * actionsTotal, not actions. A locked-out player is never charged, so the
     * per-turn counter sits at 0 and every lock-out decision reads as free --
     * which is exactly the turn we most want measured. actionsTotal climbs
     * either way.
     */
    const before = md.p[String(act)]?.actionsTotal ?? 0;
    const round = md.p[String(act)]?.round ?? 0;
    const ph = md.phase?.kind ?? "?";
    const label = String(chosen.label ?? "").replace(/\s+/g, " ").trim();
    const dk = cardsIn(zone(st, act, "deck")).length;
    const hd = cardsIn(zone(st, act, "hand")).length;
    const dc = cardsIn(zone(st, act, "discard")).length;
    if (INIT_PHASES.has(ph)) lastInit[act] = label;
    applyActions(st, toEngineAction(st, chosen) as Any);
    const after = (st.metaData as Any).p[String(act)]?.actionsTotal ?? 0;
    const charged = after > before;
    d.push({
      p: act, r: round, ph, a: charged ? 1 : 0, l: label, dk, hd, dc,
      ...(charged ? { c: lastInit[act] ?? label } : {}),
    });
    steps++;
  }
  return {
    seed, stalled: steps >= MAX_STEPS,
    players: ((st.metaData as Any).summary ?? []).map((p: Any) => ({
      id: p.playerId, trail: p.trail, actions: p.actions, freePlays: p.freePlays,
      effigyActions: p.effigyActions, effigies: p.effigies, corrupted: p.corrupted,
      consumed: p.consumed, buried: p.buried, severed: p.severed, turns: p.turns,
    })),
    d,
  };
};

const started = Date.now();
let fresh = 0;
for (let i = 0; i < GAMES; i++) {
  const seed = SEED0 + i;
  if (banked.has(seed)) continue;
  const rec = playOne(seed);
  banked.set(seed, rec);
  if (OUT) appendFileSync(OUT, `${JSON.stringify(rec)}\n`);
  fresh++;
  if (fresh % 5 === 0) {
    const per = (Date.now() - started) / 1000 / fresh;
    console.log(`  ${fresh} played, ${per.toFixed(1)}s each, ~${(((GAMES - i - 1) * per) / 60).toFixed(0)} min left`);
  }
}

// --- report over everything banked, resumed games included -----------------
const all = [...banked.values()].filter((r) => r.seed >= SEED0 && r.seed < SEED0 + GAMES);
const live = all.filter((r) => !r.stalled);
const pgames = live.reduce((a, r) => a + r.players.length, 0) || 1;
const sum = (f: (p: Any) => number) => live.reduce((a, r) => a + r.players.reduce((b: number, p: Any) => b + (f(p) || 0), 0), 0);

console.log(`\n${WHICH} — ${live.length} games (${pgames} player-games), seeds ${SEED0}..${SEED0 + GAMES - 1}` +
  `${all.length - live.length ? `, ${all.length - live.length} stalled` : ""}\n`);
console.log(`  mean trail            ${(sum((p) => p.trail) / pgames).toFixed(1)}`);
console.log(`  effigies held         ${(sum((p) => p.effigies.length) / pgames).toFixed(2)}`);
console.log(`  corrupted left        ${(sum((p) => p.corrupted) / pgames).toFixed(2)}`);
console.log(`  actions spent         ${(sum((p) => p.actions) / pgames).toFixed(1)}  (30 available)`);
console.log(`  free plays            ${(sum((p) => p.freePlays) / pgames).toFixed(1)}`);

/* Collapse "Play Marrow-Fed Reindeer" to "Play <card>" so the shape of the turn
 * is visible; the raw labels are all in --out for anything finer. */
const norm = (l: string) =>
  l.replace(/^(Play|Keep|Select|Siphon|Untether|Take|Bury|Sever|Consume|Compost|Activate) .+/, "$1 …")
   .replace(/\d+/g, "N");

const paid = new Map<string, number>(); const freeM = new Map<string, number>();
const byRound = new Map<number, number>();
for (const r of live) for (const x of r.d) {
  if (x.a) {
    const k = norm(x.c ?? x.l);
    paid.set(k, (paid.get(k) ?? 0) + 1);
    byRound.set(x.r, (byRound.get(x.r) ?? 0) + 1);
  } else {
    freeM.set(norm(x.l), (freeM.get(norm(x.l)) ?? 0) + 1);
  }
}
const show = (m: Map<string, number>, title: string) => {
  const tot = [...m.values()].reduce((a, b) => a + b, 0) || 1;
  console.log(`\n  ${title} — ${(tot / pgames).toFixed(1)} per player-game`);
  for (const [k, v] of [...m].sort((a, b) => b[1] - a[1]).slice(0, 22))
    console.log(`    ${k.padEnd(34)} ${(v / pgames).toFixed(2).padStart(6)}  ${((v / tot) * 100).toFixed(0).padStart(3)}%`);
};
show(paid, "ACTIONS SPENT, by what they bought");
show(freeM, "FREE decisions (no action charged)");
console.log("\n  actions spent by round:");
for (const [r, v] of [...byRound].sort((a, b) => a[0] - b[0]))
  console.log(`    round ${r}   ${(v / pgames).toFixed(2).padStart(6)}`);

/*
 * The thin-deck moment: T2deB's favourite thing that happened at the table was
 * emptying deck AND discard and drawing the whole hand, then having only six
 * actions to use it. Count how often a bot reaches it and what it can do after.
 */
let reached = 0, playsAfter = 0, actionsAfter = 0;
for (const r of live) {
  for (const pid of [1, 2]) {
    const mine = r.d.filter((x) => x.p === pid);
    const i = mine.findIndex((x) => x.dk === 0 && x.dc === 0 && x.hd > 0);
    if (i < 0) continue;
    reached++;
    for (const x of mine.slice(i)) {
      if (x.l.startsWith("Play ")) playsAfter++;
      if (x.a) actionsAfter++;
    }
  }
}
/*
 * Lock-out: does it happen, does it terminate, and does it deliver the thing it
 * exists for -- a thinned deck you actually get to play?
 */
let lo = 0, loDeck = 0, loPlays = 0, loActions = 0, loDecisions = 0;
for (const r of live) {
  for (const pid of [1, 2]) {
    const mine = r.d.filter((x) => x.p === pid);
    const i = mine.findIndex((x) => x.l.startsWith("Walk alone"));
    if (i < 0) continue;
    lo++;
    loDeck += mine[i].dk + mine[i].hd;
    for (const x of mine.slice(i + 1)) {
      loDecisions++;
      if (x.l.startsWith("Play ")) loPlays++;
      if (x.a) loActions++;
    }
  }
}
console.log(`\n  LOCK-OUT declared in ${lo} of ${pgames} player-games` +
  (lo ? `\n    cards in deck+hand when declared  ${(loDeck / lo).toFixed(1)}` +
        `\n    cards played after                ${(loPlays / lo).toFixed(1)}` +
        `\n    actions taken after               ${(loActions / lo).toFixed(1)}` +
        `\n    decisions after                   ${(loDecisions / lo).toFixed(1)}  <- unbounded if this runs away`
      : ""));

console.log(`\n  EMPTY DECK AND DISCARD reached in ${reached} of ${pgames} player-games` +
  `${reached ? ` — after it: ${(playsAfter / reached).toFixed(1)} plays, ${(actionsAfter / reached).toFixed(1)} actions` : ""}`);
if (OUT) console.log(`\n  every decision written to ${OUT} (${banked.size} games)`);

/*
 * Does the simulator actually exercise the whole game?
 *
 * Two questions, and they fail for different reasons:
 *
 *   STATIC    every card carries an effect, the pool is the shape the design
 *             doc says it is, and no card's text outruns its code.
 *   REACHABLE every card key actually turns up in real games. A card that no
 *             bot ever touches has no statistics, and a balance report that
 *             quietly omits it is worse than one that says so.
 *
 * Run it before trusting any number out of run.ts:
 *
 *   node --import ./register.mjs conformance.ts [games]
 */

import { ANIMAL_DEFS, ANIMAL_CARDS, EFFIGY_CARDS, cardByKey, slug } from "../src/cards.ts";
import { EFFECTS } from "../src/effects.ts";
import type { Step } from "../src/steps.ts";
import { playGame } from "./harness.ts";
import { ARCHETYPES, CORE, GUARDS, makeBot, makeRandomBot } from "./bot.ts";

type Any = any;

const GAMES = Number(process.argv[2] ?? 12);

/*
 * Animals whose whole card is a while-tethered trigger, so an empty `effect` is
 * correct rather than missing. Keep this list short and justified — it is the
 * one place a genuinely unimplemented card could hide.
 */
const TRIGGER_ONLY = new Set(["lynx-chimera"]);

const problems: string[] = [];
const notes: string[] = [];
const fail = (s: string) => problems.push(s);

// ---------------------------------------------------------------------------
// Static
// ---------------------------------------------------------------------------

console.log("STATIC\n");

const byDomain: Record<string, number> = {};
const byDomainType: Record<string, number> = {};
const keys = new Set<string>();

for (const { def, domain } of ANIMAL_DEFS) {
  const key = slug(def.name);
  if (keys.has(key)) fail(`duplicate card key: ${key}`);
  keys.add(key);

  byDomain[domain] = (byDomain[domain] ?? 0) + 1;
  byDomainType[`${domain}/${def.type}`] = (byDomainType[`${domain}/${def.type}`] ?? 0) + 1;

  const hasEffect = def.effect.length > 0;
  if (!hasEffect && !TRIGGER_ONLY.has(key)) {
    fail(`${def.name} (${key}) has no effect and is not declared trigger-only`);
  }
  if (hasEffect && TRIGGER_ONLY.has(key)) {
    notes.push(`${def.name} is declared trigger-only but now HAS an effect — update TRIGGER_ONLY`);
  }
  if (hasEffect && !EFFECTS[key]) fail(`${def.name} (${key}) is missing from the EFFECTS table`);
  if (!def.text?.trim()) fail(`${def.name} (${key}) has no text`);
  if (def.cost < 0 || def.cost > 6) fail(`${def.name} has an implausible cost: ${def.cost}`);
}

/** Card text that the code only approximates announces itself with a note step. */
const noteSteps = (steps: Step[]): string[] => {
  const out: string[] = [];
  const walk = (list: Step[]) => {
    for (const s of list) {
      if (s.s === "note") out.push(s.text);
      if (s.s === "cond") {
        walk(s.then);
        walk(s.other);
      }
      if (s.s === "pick") for (const o of s.options) walk(o.steps);
    }
  };
  walk(steps);
  return out;
};

for (const { def } of ANIMAL_DEFS) {
  for (const text of noteSteps(def.effect)) {
    notes.push(`${def.name} is only approximated: ${text}`);
  }
}

console.log(`  animals: ${ANIMAL_CARDS.length}`);
for (const [d, n] of Object.entries(byDomain).sort()) {
  const types = ["Critter", "Tamekin", "Wildkin"].map((t) => byDomainType[`${d}/${t}`] ?? 0);
  console.log(`    ${d.padEnd(12)} ${String(n).padStart(3)}   C/T/W ${types.join("/")}`);
}
console.log(`  effigies: ${EFFIGY_CARDS.length}`);
console.log(`  trigger-only (no effect, by design): ${[...TRIGGER_ONLY].join(", ") || "none"}`);
console.log(`  approximated by a note step: ${notes.length === 0 ? "none" : notes.length}`);

for (const e of EFFIGY_CARDS) {
  if (!cardByKey(e.key)) fail(`effigy ${e.name} is not reachable through cardByKey`);
  if (typeof (e.score as Any).max !== "number") fail(`effigy ${e.name} has no scoring cap`);
}

// ---------------------------------------------------------------------------
// Honest
// ---------------------------------------------------------------------------

/*
 * Does anything still drive a game past the disabled-button filter?
 *
 * getAvailableActions enumerates buttons the real server refuses. A script that
 * calls it directly and applies what it gets is simulating a game no human can
 * play — which went unnoticed for the entire life of this simulator, across
 * something like a hundred thousand games, because no aggregate can see it.
 * This is cheap insurance against a second time.
 */
{
  const fs = await import("node:fs");
  const drivers = fs
    .readdirSync(".")
    .filter((f) => f.endsWith(".ts") && f !== "conformance.ts");
  for (const file of drivers) {
    const text = fs.readFileSync(file, "utf8");
    if (!text.includes("applyActions(")) continue; // not a driver
    // Any reference counts — some drivers alias it to shadow the raw enumeration.
    if (text.includes("getAvailableActions(") && !text.includes("legalActions")) {
      fail(`${file} drives a game with getAvailableActions and never calls legalActions`);
    }
  }
}

// ---------------------------------------------------------------------------
// Reachable
// ---------------------------------------------------------------------------

console.log(`\nREACHABLE  (${GAMES} games)\n`);

const mulberry = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) >>> 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

/*
 * A spread of behaviour, not a search for the best weights. Coverage is the
 * goal here: a card only one strategy ever wants still has to turn up.
 */
const FIELD = CORE;

const seen = new Set<string>();
const played = new Set<string>();
let stalled = 0;

for (let g = 0; g < GAMES; g++) {
  const rng = mulberry(g * 7919 + 13);
  const a = FIELD[g % FIELD.length];
  const b = FIELD[(g + 1 + Math.floor(g / FIELD.length)) % FIELD.length];
  const bot = (name: string) =>
    name === "random" ? makeRandomBot(rng) : makeBot(ARCHETYPES[name], rng);

  const r = playGame([1, 2], { 1: bot(a), 2: bot(b) }, g);
  if (r.stalled) stalled++;
  for (const ev of ((r.metaData as Any).stats ?? []) as Any[]) {
    if (!ev.c) continue;
    if (ev.k === "siphon" || ev.k === "dieSiphon") seen.add(ev.c);
    if (ev.k === "play" || ev.k === "copy" || ev.k === "activateSevered") played.add(ev.c);
  }
}

const animalKeys = ANIMAL_CARDS.map((c) => c.key);
const neverSeen = animalKeys.filter((k) => !seen.has(k) && !played.has(k));
const neverPlayed = animalKeys.filter((k) => seen.has(k) && !played.has(k));

console.log(`  acquired at least once: ${animalKeys.length - neverSeen.length}/${animalKeys.length}`);
console.log(`  bought but never played: ${neverPlayed.length}`);
if (stalled > 0) console.log(`  stalled games: ${stalled}`);

/*
 * The bot's loop guards narrow its options in a way the rules never narrow a
 * human's. If they fire, the bot is playing a slightly different game from the
 * one people play, and the balance numbers are measuring that difference.
 */
console.log(`  loop guard fired: ${GUARDS.streak}  (round-clock guard: ${GUARDS.roundClock})`);
if (GUARDS.streak > 0) {
  fail(
    `the bot had to be forced to commit ${GUARDS.streak} time(s), in: ` +
      Object.entries(GUARDS.where).map(([k, v]) => `${k}x${v}`).join(", "),
  );
  console.log("\n  picks leading up to the first firing:");
  for (const line of GUARDS.sample) console.log(`    ${line}`);
}

if (neverSeen.length > 0) {
  console.log(`\n  never acquired in ${GAMES} games (no statistics exist for these):`);
  for (const k of neverSeen) console.log(`    ${cardByKey(k)?.name ?? k}`);
}

// ---------------------------------------------------------------------------

console.log("");
for (const n of notes) console.log(`NOTE  ${n}`);
for (const p of problems) console.log(`FAIL  ${p}`);
console.log(problems.length === 0 ? "\nconformance OK" : `\n${problems.length} problem(s)`);
process.exit(problems.length === 0 ? 0 : 1);

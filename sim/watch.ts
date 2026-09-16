/*
 * Watch ONE game, turn by turn, in plain language.
 *
 * Every bug found in this simulator so far was found by reading a single
 * game's decisions, not by reading an average over hundreds. The averages said
 * something was wrong; they never once said what. Worse, they actively hid
 * things — "0.0 trail" turned out to mean every game had stalled and been
 * discarded, which looks identical to a bot that scores nothing.
 *
 * So this is the first thing to reach for, and the sweeps are for afterwards.
 *
 *   node --import ./register.mjs watch.ts [bot] [seed] [--quiet]
 *
 * bot: an archetype name, or "search" / "search-lowrih" for the beam.
 */

import { legalActions, newGame, toEngineAction, type Available } from "./harness.ts";
import { ARCHETYPES, makeBot, makeRandomBot, makeSearchBot } from "./bot.ts";
import { getAvailableActions, applyActions, isGameOver } from "../src/game.ts";
import { cardByKey } from "../src/cards.ts";
import { features } from "../src/bot.ts";

type Any = any;
const args = process.argv.slice(2);
const WHICH = args.find((a) => !a.startsWith("--")) ?? "rotting";
const SEED = Number(args.filter((a) => !a.startsWith("--"))[1] ?? 3);
const QUIET = args.includes("--quiet");

const mulberry = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) >>> 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

const make = (name: string, rng: () => number) => {
  if (name === "random") return makeRandomBot(rng);
  if (name === "search") return makeSearchBot(ARCHETYPES.rotting, rng, 8, 8);
  if (name === "search-lowrih")
    return makeSearchBot({ ...ARCHETYPES.rotting, remnantsInHand: 0 }, rng, 8, 8);
  /*
   * A Corrupted Soul left in your deck at the end is a 3-point retreat, which
   * at trail:10 is -30 units. The archetypes carry -1.5 — off by a factor of
   * twenty — so a bot that farms them is correctly reading a lying scorecard.
   */
  if (name === "search-cs")
    return makeSearchBot({ ...ARCHETYPES.rotting, corruptedLive: -30 }, rng, 8, 8);
  if (name === "greedy-cs")
    return makeBot({ ...ARCHETYPES.rotting, corruptedLive: -30 }, rng);
  return makeBot(ARCHETYPES[name] ?? ARCHETYPES.rotting, rng);
};

const rng = mulberry(SEED);
const st = newGame([1, 2], SEED);
const bots: Any = { 1: make(WHICH, rng), 2: make("rotting", rng) };
console.log(`P1 = ${WHICH}   P2 = rotting   seed ${SEED}\n`);

let steps = 0;
let lastKey = "";
const t0 = Date.now();

while (!isGameOver(st as Any, {} as Any) && steps < 4000) {
  const act = st.activePlayerIds[0];
  if (act === undefined) break;
  st.currentPlayerId = act;
  const opts = legalActions(st, act);
  if (!opts.length) break;

  const md = st.metaData as Any;
  const me = md.p[String(act)];
  const before = { trail: me.trail, souls: me.souls + me.tempSouls, actions: me.actions };
  const pick = bots[act](st, act, opts);

  /*
   * An unspent action is worth exactly zero, so a turn ended with actions still
   * in hand is either the bot correctly having nothing to do, or it declining
   * to play. Those need different fixes, and only this tells them apart.
   */
  const label0 = pick.label ?? (pick.action as Any).button?.id ?? "";
  if (label0 === "End turn" && before.actions < 2 + (me.extraActions ?? 0)) {
    const hand = (st.player(act)?.spaces().find((sp: Any) => sp.kind === "hand")?.pieces("card") ?? []) as Any[];
    const others = opts
      .filter((o) => o !== pick && o.intent !== "undo")
      .map((o) => o.label ?? (o.action as Any).button?.id ?? "?");
    console.log(
      `   >> PASSED with a${before.actions}/${2 + (me.extraActions ?? 0)}, ` +
        `${before.souls} souls, ${hand.length} cards in hand. ` +
        `${others.length} other option(s): ${others.slice(0, 4).join(" | ")}${others.length > 4 ? " ..." : ""}`,
    );
  }

  // A header whenever the turn changes hands or the round moves on.
  const key = `${act}-${me.round}-${md.turns?.[String(act)] ?? 0}`;
  if (key !== lastKey) {
    lastKey = key;
    if (!QUIET || act === 1) {
      const f = features(st as Any, act) as Any;
      console.log(
        `\n-- P${act}  round ${me.round} (${["", "Echo", "Building", "Burning", "Guttering", "Hollow Echo"][me.round] ?? "?"})` +
          `  trail ${me.trail}  souls ${before.souls}  deck ${f.deckSize}`,
      );
      // The numbers the bot is actually deciding on.
      console.log(
        `   corruptedLive ${f.corruptedLive}  soulSinks ${f.soulSinks.toFixed(1)}` +
          `  stuckSouls ${f.stuckSouls.toFixed(1)}  animals ${f.animals}  tethered ${f.tethered}`,
      );
    }
  }

  applyActions(st as Any, toEngineAction(st, pick) as Any);
  steps++;

  const after = (st.metaData as Any).p[String(act)];
  const gained = after.trail - before.trail;
  const spent = before.souls - (after.souls + after.tempSouls);
  if (!QUIET || act === 1) {
    const label = pick.label ?? (pick.action as Any).button?.id ?? "?";
    const notes = [
      gained ? `+${gained} trail` : "",
      spent > 0 ? `-${spent} souls` : spent < 0 ? `+${-spent} souls` : "",
    ].filter(Boolean).join(", ");
    console.log(`     a${after.actions}/${2 + (after.extraActions ?? 0)} ${(st.metaData as Any).phase.kind.padEnd(9)} ${label}${notes ? `   [${notes}]` : ""}`);
  }
}

const md = st.metaData as Any;
console.log(`\n=== over after ${steps} decisions, ${((Date.now() - t0) / 1000).toFixed(1)}s ===`);
for (const sm of md.summary ?? []) {
  const deck = Object.entries(sm.deck ?? {}) as [string, number][];
  const animals = deck.filter(([k]) => cardByKey(k)?.category === "Animal");
  console.log(
    `P${sm.playerId}  trail ${String(sm.trail).padStart(4)}   ` +
      `corrupted left ${sm.corrupted ?? 0}   effigies ${(sm.effigies ?? []).length}   ` +
      `animals owned ${animals.reduce((a, [, n]) => a + n, 0)}`,
  );
  if (animals.length) {
    console.log(`     ${animals.map(([k, n]) => `${cardByKey(k)?.name}${n > 1 ? ` x${n}` : ""}`).join(", ")}`);
  }
}

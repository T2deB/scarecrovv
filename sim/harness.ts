/*
 * Drives the real rules files headlessly.
 *
 * This is the counterpart to what BoardWeaver's server does: build the state
 * from gameConfig, run preGameInitialization, then loop
 * getAvailableActions -> pick one -> applyActions until isGameOver.
 *
 * No rule lives here. Every decision goes through the same functions the live
 * game runs, which is the whole point: the simulator cannot drift from what
 * players actually play.
 */

import { MockGameState } from "./gamestate.ts";
import { capturePieces, restoreSnapshot, serializeMeta } from "../src/state.ts";
import { positionKey } from "../src/bot.ts";
import {
  applyActions,
  gameConfig,
  getAvailableActions,
  isGameOver,
  preGameInitialization,
} from "../src/game.ts";

type Any = any;

/** One legal action, as returned by the engine. */
export type Available = {
  action: { type: string; pieceId?: string; spaceId?: string; button?: Any };
  intent: "choice" | "confirm" | "cancel" | "undo";
  label?: string;
};

export type Chooser = (
  state: MockGameState,
  playerId: number,
  options: Available[],
) => Available;

/**
 * Every action a player may legally take right now.
 *
 * THE DISABLED FILTER IS NOT COSMETIC. getButtons marks a button
 * `disabled: !canAct`, and BoardWeaver's server refuses a disabled button even
 * though getAvailableActions enumerates it — the docs say so explicitly. This
 * harness did not, so for the whole of its existence a bot here could press
 * buttons no human can.
 *
 * It was found by printing the action counter next to each click: a bot sat at
 * a3/2, a5/2, a7/2 and kept going, taking eighty actions in a two-action turn
 * and draining the entire Corrupted Soul supply in one. Nothing in a hundred
 * thousand simulated games had surfaced it, because nothing had ever looked at
 * a single turn.
 */
export const legalActions = (st: MockGameState, playerId?: number): Available[] => {
  if (playerId !== undefined) st.currentPlayerId = playerId;
  return ((getAvailableActions(st as Any) as unknown as Available[]) ?? []).filter(
    (o) => !(o.action.type === "ButtonClick" && (o.action as Any).button?.disabled),
  );
};

/** A safety net: a bot that loops on `choice` actions would otherwise hang. */
const MAX_STEPS = 4000;

export type GameResult = {
  seed: number;
  steps: number;
  stalled: boolean;
  metaData: Any;
};

/**
 * `seed` makes a game reproducible.
 *
 * preGameInitialization draws its PRNG seed from Math.random(), so without this
 * two runs shuffle differently and can only be compared as independent samples.
 * Stubbing Math.random for the duration pins the deck order, market order and
 * opening dice, which lets two rule variants be compared as a PAIRED
 * experiment — far tighter than comparing two independent groups.
 */
export const newGame = (playerIds: number[], seed?: number): MockGameState => {
  const state = new MockGameState(playerIds);
  const config = gameConfig({ numPlayers: playerIds.length } as Any) as Any;

  for (const [id, def] of Object.entries(config.spaces ?? {})) {
    state.addSpace(id, def);
  }
  state.scoreLabels = config.scoreLabels;

  // The platform picks a starting player; mirror that.
  state.activePlayerIds = [playerIds[0]];
  state.currentPlayerId = playerIds[0];

  if (seed === undefined) {
    preGameInitialization(state as Any);
  } else {
    const realRandom = Math.random;
    // preGameInitialization calls Math.random once, for the game seed.
    Math.random = () => ((seed >>> 0) % 0xffffffff) / 0xffffffff;
    try {
      preGameInitialization(state as Any);
    } finally {
      Math.random = realRandom;
    }
  }
  return state;
};

/**
 * Play one game to completion.
 *
 * `choosers` is keyed by playerId. The engine is asked for legal actions from
 * the perspective of whoever is currently active — including a player who has
 * been handed the turn mid-move by a while-tethered interrupt.
 */
export const playGame = (
  playerIds: number[],
  choosers: Record<number, Chooser>,
  seed?: number,
): GameResult => {
  const state = newGame(playerIds, seed);
  let steps = 0;

  while (!isGameOver(state as Any, {} as Any) && steps < MAX_STEPS) {
    const active = state.activePlayerIds[0];
    if (active === undefined) break;

    // currentPlayer is "the player this call is being made on behalf of".
    state.currentPlayerId = active;
    const options = legalActions(state, active);
    if (options.length === 0) break;

    const chosen = choosers[active](state, active, options);
    applyActions(state as Any, toEngineAction(state, chosen) as Any);
    steps++;
  }

  return {
    seed: (state.metaData as Any).seed,
    steps,
    stalled: steps >= MAX_STEPS,
    metaData: state.metaData,
  };
};

/**
 * getAvailableActions returns wire-shaped actions (ids); applyActions wants the
 * live instances. The server does this resolution too.
 */
export const toEngineAction = (state: MockGameState, entry: Available): Any => {
  const a = entry.action;
  if (a.type === "ButtonClick") return { type: "ButtonClick", button: a.button };
  if (a.type === "PieceClick") return { type: "PieceClick", piece: state.piece(a.pieceId!) };
  return { type: "SpaceClick", space: state.space(a.spaceId!) };
};

/**
 * Apply one action to a COPY of the state, for lookahead.
 *
 * The telemetry, log and undo stack are dropped from the copy. Nothing scores
 * them, and they dominate the cost of a clone: the undo stack holds serialized
 * snapshots of metaData, so copying it is close to quadratic in game length.
 * A bot evaluates every legal action every decision, so this is the hot path.
 */
export const simulate = (state: MockGameState, entry: Available): MockGameState => {
  const dump = state.dump();
  const meta = dump.metaData as Any;
  dump.metaData = { ...meta, stats: [], log: [], undo: [] };
  const next = MockGameState.load(JSON.parse(JSON.stringify(dump)) as Any);
  next.currentPlayerId = state.currentPlayerId;
  applyActions(next as Any, toEngineAction(next, entry) as Any);
  return next;
};

/**
 * Like `simulate`, but if the action leaves cards merely STAGED in a prompt,
 * commit them before returning.
 *
 * Staging changes nothing a bot scores — the cards have not moved yet — so
 * without this every selection looks identical and a bot toggles forever. This
 * makes "select this card" mean "select it and commit", which is the decision
 * the player is actually weighing.
 */
export const simulateSettled = (
  state: MockGameState,
  entry: Available,
  /*
   * How the Remnant quantity sub-phase is handled.
   *
   * "max" is for a ONE-PLY bot, which cannot see past the click that opens the
   * sub-phase: unsettled, taking Remnants scores as worthless and the bot stops
   * doing it, which deletes the main scoring route in the game.
   *
   * "decide" is for a SEARCH, and is the honest treatment. How many Remnants to
   * take is a real decision — each costs a soul, and composting them costs two
   * more — so forcing the maximum is a bot-only distortion, and a bad one. A
   * search bot on "max" drained every soul into Remnants on one click, took 43
   * of them in a game, composted 9 and scored 0 trail, because it could never
   * examine a line that took fewer.
   */
  remnants: "max" | "decide" = "max",
): MockGameState => {
  const next = simulate(state, entry);
  const meta = next.metaData as Any;
  const prompt = meta?.pending?.prompt;
  if (prompt?.k === "cards" && (meta.pending.staged?.length ?? 0) > 0) {
    const done = { action: { type: "ButtonClick", button: { id: "fx-done", label: "", location: "PlayerHeader", disabled: false } }, intent: "confirm" as const };
    try {
      applyActions(next as Any, toEngineAction(next, done) as Any);
    } catch {
      /* leave it staged */
    }
  }

  /*
   * Taking or composting Remnants now opens a quantity sub-phase. Opening it
   * changes nothing a bot can score, so unsettled the action looks worthless
   * and no bot ever takes a Remnant again - which deletes the main scoring
   * route in the game. Settle it by committing the largest quantity offered,
   * which is bounded by what the player can actually afford.
   */
  if (remnants === "decide") {
    // Leave the quantity on the table; the caller will enumerate it as moves.
  } else if ((meta?.phase?.kind ?? "") === "remnants") {
    const opts = legalActions(next, next.activePlayerIds[0] ?? next.currentPlayerId);
    const qty = opts
      .filter((o) => o.action.type === "ButtonClick" && (o.action as Any).button?.id?.startsWith("remnant-qty-"))
      .sort(
        (a, b) =>
          Number((b.action as Any).button.id.slice("remnant-qty-".length)) -
          Number((a.action as Any).button.id.slice("remnant-qty-".length)),
      )[0];
    if (qty) {
      try {
        applyActions(next as Any, toEngineAction(next, qty) as Any);
      } catch {
        /* leave the sub-phase open */
      }
    }
  }

  /*
   * Spending a die costs NO ACTION and becomes a soul or a free Siphon, so
   * passing on one is pure waste — but the click only opens a sub-phase, which
   * changes nothing a bot can score. Unsettled it reads as 0.00, loses to
   * anything at all, and the bot ends the round with the die unspent.
   *
   * This is the SECOND settling implementation in the codebase; the other is
   * `settle` in /src/bot.ts, which already covers this. Fixing it there did
   * nothing for the one-ply bot because the one-ply bot comes through here.
   * They should be one function.
   */
  if ((next.metaData as Any)?.phase?.kind === "dieSpend") {
    const opts = legalActions(next, next.activePlayerIds[0] ?? next.currentPlayerId);
    const pick = opts.find((o) => o.intent === "confirm");
    if (pick) {
      try {
        applyActions(next as Any, toEngineAction(next, pick) as Any);
      } catch {
        /* leave the die uncommitted */
      }
    }
  }

  /*
   * Look for Scarecrovv only puts two Effigies on offer; the Effigy is claimed
   * by the NEXT click. Unsettled, a bot sees Looking as pure cost — it discards
   * three Tethered cards and gains nothing yet — and never does it, even when
   * the trio is sitting right there. Claim one so the payoff is visible.
   */
  if ((meta?.phase?.kind ?? "") === "effigy") {
    const offered = next.spaceOfKind("effigy-offer")?.pieces("card") ?? [];
    if (offered.length > 0) {
      const claim = { action: { type: "PieceClick", pieceId: offered[0].pieceId }, intent: "confirm" as const };
      try {
        applyActions(next as Any, toEngineAction(next, claim) as Any);
      } catch {
        /* leave the offer open */
      }
    }
  }
  return next;
};

// ---------------------------------------------------------------------------
// Make a move, look, take it back
// ---------------------------------------------------------------------------

/*
 * `simulate` answers "what would the table look like if I clicked that?" by
 * photocopying the entire table — 251 pieces, every space, all of metaData —
 * clicking on the copy and throwing it away. Measured: 0.360 ms of the 0.440 ms
 * a node costs is that one JSON round trip. Everything else is nearly free.
 *
 * That is affordable when a bot imagines six moves per decision and ruinous
 * when a search imagines 359: one game becomes 37,000 complete copies of the
 * table.
 *
 * The alternative is what a person does at a real table — make the move, look,
 * take it back — and the live game already has the machinery, because that is
 * what Undo is. These are the SAME functions /src/bot.ts uses, not a second
 * implementation of them, so the simulator's search cannot drift from the real
 * one. capturePieces records only cards, which is all that moves; nothing in a
 * turn mints a new piece, so a restore is exact.
 */
export type Snap = ReturnType<typeof capture>;

export const capture = (st: MockGameState) => ({
  meta: serializeMeta(st as Any),
  pieces: capturePieces(st as Any),
  statsLen: ((st.metaData as Any).stats ?? []).length,
  logLen: ((st.metaData as Any).log ?? []).length,
  active: [...st.activePlayerIds],
  label: "search",
  // Never offered to anyone: rolled back here, not pushed onto the undo stack.
  by: -1,
});

export const restore = (st: MockGameState, snap: Snap): void => {
  restoreSnapshot(st as Any, snap as Any);
};

/**
 * A state signature for spotting transpositions.
 *
 * metaData alone is not enough — two different arrangements of the same cards
 * share it — so the digest carries where every card sits and which face is up.
 */
export const signatureOf = (snap: Snap): string =>
  /*
   * Which card is in which zone, and nothing else.
   *
   * snap.meta is serializeMeta, which carries `seq` and is therefore unique
   * every time; positionKey strips that. The piece digest had exactly the same
   * disease one level down — it included each card's ORDER within its zone, and
   * a card played and then taken back returns to hand at a different order. So
   * the position read as new, the repetition check never fired, and the search
   * bot cycled play/take-back until the step cap. Every game stalled.
   *
   * Sorted by pieceId so the digest does not depend on the traversal order
   * either, which changes for the same reason.
   */
  `${positionKey({ metaData: JSON.parse(snap.meta) } as Any)}|${snap.pieces
    .map((p: Any) => `${p[0]}@${p[1]}:${p[3] ?? ""}`)
    .sort()
    .join(",")}`;

/** The three things `settle` needs, bound to a live MockGameState. */
export const searchAdapter = (st: MockGameState, playerId: number) => ({
  available: () => legalActions(st, playerId).filter((o) => o.intent !== "undo") as Any,
  apply: (s: Any, _p: number, action: Any) => {
    applyActions(s, toEngineAction(s, { action, intent: "choice" } as Available) as Any);
  },
  buttonId: (action: Any): string | null =>
    action?.type === "ButtonClick" ? (action.button?.id ?? null) : null,
});

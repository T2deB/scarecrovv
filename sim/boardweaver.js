/*
 * Minimal stand-in for the `boardweaver` module, so the real rules files in
 * the repo's /src tree can run under plain Node.
 *
 * Two things live here:
 *
 * 1. PieceDef / SpaceDef — the only real VALUES the engine imports. The engine
 *    subclasses them and reads `kind`, `orientations`, and the defaults hooks.
 *
 * 2. Placeholder exports for names that are types in the real package. Node's
 *    type-stripping does no type analysis, so `import { Button } from
 *    "boardweaver"` survives into runtime as a real named import even though
 *    Button is type-only. Without a matching export, Node throws
 *    "does not provide an export named". Exporting undefined satisfies the
 *    binding and is never read.
 *
 * This file is .js on purpose: Node does not strip types inside node_modules.
 */

export class PieceDef {
  constructor(init = {}) {
    const { privateState, ...pub } = init;
    this._initPublic = pub;
    this._initPrivate = privateState ?? {};
  }
  publicDefaults() {
    return {};
  }
  privateDefaults() {
    return {};
  }
}

export class SpaceDef {
  constructor() {}
}

// Type-only in the real package; present here purely to satisfy imports.
export const GameState = undefined;
export const Piece = undefined;
export const Space = undefined;
export const Player = undefined;
export const Button = undefined;
export const AvailableAction = undefined;
export const GameStateConfigObject = undefined;
export const GameStateConfigFn = undefined;
export const ApplyActionsFn = undefined;
export const GetAvailableActionsFn = undefined;
export const GetButtonsFn = undefined;
export const GetPlayerScoresFn = undefined;
export const GetSelectableItemsFn = undefined;
export const IsGameOverFn = undefined;
export const PreGameInitializationFn = undefined;
export const Action = undefined;
export const RemoteAction = undefined;
export const Scores = undefined;
export const PieceKindRegistry = undefined;
export const SpaceKindRegistry = undefined;

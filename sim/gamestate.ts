/*
 * A local stand-in for BoardWeaver's GameState, good enough to run the real
 * rules files in sim/engine headlessly.
 *
 * This implements only the surface the engine actually touches. It is NOT a
 * reimplementation of the game: no rule lives here. If a method is missing, the
 * engine will throw and tell you, which is the failure mode you want.
 *
 * Cloning goes through dump()/load() rather than structuredClone, because
 * pieces and spaces hold back-references to the state and would otherwise be
 * cloned into a tangle. The dump is plain JSON, so a clone is a round-trip.
 */

type Dict = Record<string, unknown>;

export type PieceDump = {
  id: string;
  kind: string;
  order: number;
  spaceId: string;
  pub: Dict;
  priv: Dict;
};

export type SpaceDump = { id: string; kind: string; playerId: number | null };

export type StateDump = {
  metaData: unknown;
  activePlayerIds: number[];
  currentPlayerId: number;
  players: number[];
  spaces: SpaceDump[];
  pieces: PieceDump[];
};

type Selector<T> = string | ((item: T) => unknown) | undefined;

const match = <T extends { kind: string }>(
  items: T[],
  selector: Selector<T>,
  extra?: Selector<T>,
): T[] => {
  let out = items;
  if (typeof selector === "string") out = out.filter((i) => i.kind === selector);
  else if (typeof selector === "function") out = out.filter((i) => selector(i));
  if (typeof extra === "function") out = out.filter((i) => extra(i));
  return out;
};

export class MockPiece {
  // Written out longhand: Node's type-stripping rejects parameter properties.
  state: MockGameState;
  pieceId: string;
  kind: string;
  order: number;
  publicState: Dict;
  privateState: Dict;
  spaceId: string;

  constructor(
    state: MockGameState,
    pieceId: string,
    kind: string,
    order: number,
    publicState: Dict,
    privateState: Dict,
    spaceId = "",
  ) {
    this.state = state;
    this.pieceId = pieceId;
    this.kind = kind;
    this.order = order;
    this.publicState = publicState;
    this.privateState = privateState;
    this.spaceId = spaceId;
  }

  get space(): MockSpace | undefined {
    return this.state.space(this.spaceId);
  }

  get player(): MockPlayer | undefined {
    const s = this.space;
    return s?.playerId === null || s?.playerId === undefined
      ? undefined
      : this.state.player(s.playerId);
  }
}

export class MockSpace {
  state: MockGameState;
  spaceId: string;
  kind: string;
  playerId: number | null;

  constructor(
    state: MockGameState,
    spaceId: string,
    kind: string,
    playerId: number | null = null,
  ) {
    this.state = state;
    this.spaceId = spaceId;
    this.kind = kind;
    this.playerId = playerId;
  }

  pieces(selector?: Selector<MockPiece>, extra?: Selector<MockPiece>): MockPiece[] {
    const mine = this.state.allPieces().filter((p) => p.spaceId === this.spaceId);
    return match(mine, selector, extra).sort((a, b) => a.order - b.order);
  }

  addPiece(id: string, def: unknown): MockPiece {
    const piece = this.state.addPiece(id, def);
    piece.spaceId = this.spaceId;
    return piece;
  }

  ensurePiece(id: string): MockPiece {
    const p = this.pieces().find((x) => x.pieceId === id);
    if (!p) throw new Error(`ensurePiece: no piece ${id} in ${this.spaceId}`);
    return p;
  }

  get player(): MockPlayer | undefined {
    return this.playerId === null ? undefined : this.state.player(this.playerId);
  }
}

export class MockPlayer {
  state: MockGameState;
  playerId: number;

  constructor(state: MockGameState, playerId: number) {
    this.state = state;
    this.playerId = playerId;
  }

  spaces(selector?: Selector<MockSpace>, extra?: Selector<MockSpace>): MockSpace[] {
    const mine = this.state.allSpaces().filter((s) => s.playerId === this.playerId);
    return match(mine, selector, extra);
  }

  space(id: string): MockSpace | undefined {
    return this.spaces().find((s) => s.spaceId === id);
  }

  spaceOfKind(kind: string): MockSpace | undefined {
    return this.spaces().find((s) => s.kind === kind);
  }

  ensureSpaceOfKind(kind: string): MockSpace {
    const s = this.spaceOfKind(kind);
    if (!s) throw new Error(`ensureSpaceOfKind: player ${this.playerId} has no "${kind}"`);
    return s;
  }

  addSpace(id: string, def: unknown): MockSpace {
    const space = this.state.addSpace(id, def);
    space.playerId = this.playerId;
    return space;
  }

  pieces(selector?: Selector<MockPiece>, extra?: Selector<MockPiece>): MockPiece[] {
    const ids = new Set(this.spaces().map((s) => s.spaceId));
    const mine = this.state.allPieces().filter((p) => ids.has(p.spaceId));
    return match(mine, selector, extra).sort((a, b) => a.order - b.order);
  }
}

export class MockGameState {
  metaData: unknown = {};
  activePlayerIds: number[] = [];
  scoreLabels: string[] | undefined;
  currentPlayerId = 0;

  private readonly pieceMap = new Map<string, MockPiece>();
  private readonly spaceMap = new Map<string, MockSpace>();
  private readonly playerList: MockPlayer[] = [];

  constructor(playerIds: number[] = []) {
    for (const id of playerIds) this.playerList.push(new MockPlayer(this, id));
    this.currentPlayerId = playerIds[0] ?? 0;
  }

  // --- viewer ---------------------------------------------------------------

  get currentPlayer(): MockPlayer {
    return this.ensurePlayer(this.currentPlayerId);
  }

  // --- players --------------------------------------------------------------

  players(): MockPlayer[] {
    return [...this.playerList];
  }

  player(id: number): MockPlayer | undefined {
    return this.playerList.find((p) => p.playerId === id);
  }

  ensurePlayer(selector: number | ((p: MockPlayer) => unknown)): MockPlayer {
    const found =
      typeof selector === "function"
        ? this.playerList.find((p) => selector(p))
        : this.player(selector);
    if (!found) throw new Error(`ensurePlayer: no player ${String(selector)}`);
    return found;
  }

  // --- pieces ---------------------------------------------------------------

  allPieces(): MockPiece[] {
    return [...this.pieceMap.values()];
  }

  pieces(selector?: Selector<MockPiece>, extra?: Selector<MockPiece>): MockPiece[] {
    return match(this.allPieces(), selector, extra).sort((a, b) => a.order - b.order);
  }

  piece(id: string): MockPiece | undefined {
    return this.pieceMap.get(id);
  }

  ensurePiece(id: string): MockPiece {
    const p = this.piece(id);
    if (!p) throw new Error(`ensurePiece: no piece ${id}`);
    return p;
  }

  addPiece(id: string, def: unknown): MockPiece {
    if (this.pieceMap.has(id)) throw new Error(`addPiece: duplicate id ${id}`);
    const d = def as {
      kind: string;
      publicDefaults(): Dict;
      privateDefaults(): Dict;
      _initPublic?: Dict;
      _initPrivate?: Dict;
    };
    const pub: Dict = { ...d.publicDefaults(), ...(d._initPublic ?? {}) };
    const order = typeof pub.order === "number" ? pub.order : 0;
    delete pub.order;
    const priv: Dict = { ...d.privateDefaults(), ...(d._initPrivate ?? {}) };
    const piece = new MockPiece(this, id, d.kind, order, pub, priv);
    this.pieceMap.set(id, piece);
    return piece;
  }

  removePiece(id: string): boolean {
    return this.pieceMap.delete(id);
  }

  // --- spaces ---------------------------------------------------------------

  allSpaces(): MockSpace[] {
    return [...this.spaceMap.values()];
  }

  spaces(selector?: Selector<MockSpace>, extra?: Selector<MockSpace>): MockSpace[] {
    return match(this.allSpaces(), selector, extra);
  }

  space(id: string): MockSpace | undefined {
    return this.spaceMap.get(id);
  }

  ensureSpace(id: string): MockSpace {
    const s = this.space(id);
    if (!s) throw new Error(`ensureSpace: no space ${id}`);
    return s;
  }

  spaceOfKind(kind: string): MockSpace | undefined {
    return this.allSpaces().find((s) => s.kind === kind);
  }

  ensureSpaceOfKind(kind: string): MockSpace {
    const s = this.spaceOfKind(kind);
    if (!s) throw new Error(`ensureSpaceOfKind: no space of kind "${kind}"`);
    return s;
  }

  addSpace(id: string, def: unknown): MockSpace {
    if (this.spaceMap.has(id)) throw new Error(`addSpace: duplicate id ${id}`);
    const d = def as { kind: string };
    if (!d?.kind) throw new Error(`addSpace: def for ${id} has no kind`);
    const space = new MockSpace(this, id, d.kind);
    this.spaceMap.set(id, space);
    return space;
  }

  // --- cloning --------------------------------------------------------------

  dump(): StateDump {
    return {
      metaData: this.metaData,
      activePlayerIds: [...this.activePlayerIds],
      currentPlayerId: this.currentPlayerId,
      players: this.playerList.map((p) => p.playerId),
      spaces: this.allSpaces().map((s) => ({
        id: s.spaceId,
        kind: s.kind,
        playerId: s.playerId,
      })),
      pieces: this.allPieces().map((p) => ({
        id: p.pieceId,
        kind: p.kind,
        order: p.order,
        spaceId: p.spaceId,
        pub: p.publicState,
        priv: p.privateState,
      })),
    };
  }

  static load(dump: StateDump): MockGameState {
    const state = new MockGameState(dump.players);
    state.metaData = dump.metaData;
    state.activePlayerIds = [...dump.activePlayerIds];
    state.currentPlayerId = dump.currentPlayerId;
    for (const s of dump.spaces) {
      const space = new MockSpace(state, s.id, s.kind, s.playerId);
      state.spaceMap.set(s.id, space);
    }
    for (const p of dump.pieces) {
      const piece = new MockPiece(state, p.id, p.kind, p.order, p.pub, p.priv, p.spaceId);
      state.pieceMap.set(p.id, piece);
    }
    return state;
  }

  /** Deep, independent copy. Used for the bot's one-ply lookahead. */
  clone(): MockGameState {
    return MockGameState.load(JSON.parse(JSON.stringify(this.dump())) as StateDump);
  }
}

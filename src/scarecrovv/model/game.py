# src/scarecrovv/model/game.py
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from scarecrovv.config import Config
from scarecrovv.utils.logging import EventLog

@dataclass
class GameState:
    cfg: Config
    rng: Any

    # Library & market
    cards: Dict[str, Any] = field(default_factory=dict)   # card_id -> Card
    supply: List[str] = field(default_factory=list)       # face-down stack
    pool: List[str] = field(default_factory=list)         # face-up market
    pool_discard: List[str] = field(default_factory=list)

    # Players & turn
    players: List[Any] = field(default_factory=list)
    turn: int = 0
    current_player: int = 0

    # Fields (counts, not None/pid)
    field_capacity: Dict[str, int] = field(default_factory=dict)   # set in setup()
    field_occupancy: Dict[str, int] = field(default_factory=dict)  # 0..cap per field

    # Round-scoped flags / modifiers
    forage_yield_bonus_this_round: int = 0
    hand_size_delta_next_round: Dict[int, int] = field(default_factory=dict)
    blight_compost_at_end: bool = False

    # Achievements
    first_to_three_domains_claimed: bool = False

    # Turn order & initiative
    start_player: int = 0
    turn_order: List[int] = field(default_factory=list)
    initiative_pid: Optional[int] = None  # who starts NEXT round if claimed

    # Logging
    log: EventLog = field(default_factory=EventLog)

    # Convenience logger
    def emit(self, rec: Dict[str, Any]) -> None:
        self.log.emit(rec)

    # Back-compat alias for older code that expects g.carddb
    @property
    def carddb(self):
        return self.cards

    # ----------------------------
    # Turn-order / initiative API
    # ----------------------------

    def set_turn_order_for_round(self) -> None:
        """
        Compute the player order for the current round from start_player.
        """
        n = len(self.players)
        if n <= 0:
            self.turn_order = []
            return
        s = self.start_player % n
        self.turn_order = list(range(s, n)) + list(range(0, s))
        # Align current_player to the first in order
        self.current_player = self.turn_order[0]
        self.emit({
            "a": "turn_order_set",
            "t": self.turn,
            "start_player": self.start_player,
            "order": self.turn_order[:],
        })

    def next_round_start_from_initiative(self) -> None:
        """
        Apply initiative claim (if any) to pick next round's start_player.
        """
        prev = self.start_player
        if self.initiative_pid is not None:
            self.start_player = self.initiative_pid
            self.initiative_pid = None
        self.emit({
            "a": "initiative_applied",
            "t": self.turn,
            "prev_start": prev,
            "next_start": self.start_player,
        })

    def clear_round_occupancy(self) -> None:
        """
        Reset all field occupancies to integer counts (0 = free).
        """
        if self.field_capacity:
            self.field_occupancy = {k: 0 for k in self.field_capacity.keys()}
        self.emit({"a": "field_occupancy_cleared", "t": self.turn})

    def claim_initiative(self, pid: int) -> bool:
        """
        Record who will start next round. Does NOT touch field_occupancy
        (capacity is enforced by worker placement).
        """
        if self.initiative_pid is not None:
            return False  # already claimed this round
        self.initiative_pid = pid
        self.emit({"a": "initiative_claimed", "t": self.turn, "pid": pid})
        return True

    def ensure_initiative_slot(self) -> None:
        """No-op under count-based occupancy (kept for back-compat)."""
        return

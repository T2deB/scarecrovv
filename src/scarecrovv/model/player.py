from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Duplicating resource keys here avoids model↔constants dependency loops
_RES_KEYS = ("plasma", "ash", "shards", "nut", "berry", "mushroom")

@dataclass
class PlayerState:
    id: int

    # Zones
    deck: List[str] = field(default_factory=list)
    hand: List[str] = field(default_factory=list)
    discard: List[str] = field(default_factory=list)

    # Mat: slot -> card_id (1..6)
    mat: Dict[int, str] = field(default_factory=dict)

    # Workers & scoring
    workers: int = 2
    vp: int = 0

    # Resources
    resources: Dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in _RES_KEYS}
    )

    # Telemetry (for summaries/analytics)
    first_play_turn: Dict[str, int] = field(default_factory=dict)
    slot2_type: Optional[str] = None  # chosen type for slot2 discount

    # Per-round counters (optional; helpful if you track field visits here)
    visits: Dict[str, int] = field(default_factory=dict)

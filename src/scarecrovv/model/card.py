# src/scarecrovv/model/card.py
from dataclasses import dataclass, field
from typing import Dict, List, Any

# Keep model layer decoupled from constants to avoid circular imports.
_RES_KEYS = ("plasma", "ash", "shards", "nut", "berry", "mushroom")

@dataclass
class Card:
    id: str
    name: str
    buy_cost_plasma: int = 2
    play_cost: Dict[str, int] = field(default_factory=dict)
    type_: str = "None"
    domain: str = "None"
    mat_points: int = 0
    can_play_on_mat: bool = True
    effect: str = ""  # e.g., "draw:1;if_composted_gain:ash:1"

    # -------- Compatibility layer expected by engine/eval.py --------

    @property
    def tags(self) -> List[str]:
        """
        Generic, type-like labels (used by eval for synergies/discounts).
        Derived from type_ if no explicit tag list exists.
        """
        # If you later add a real list e.g. self._tags = ["farm","radioactive"], prefer that
        t = (self.type_ or "").strip().lower()
        return [t] if t and t != "none" else []

    @property
    def domains(self) -> List[str]:
        """
        Domain labels (e.g., radioactive/slime/magic).
        """
        d = (self.domain or "").strip().lower()
        return [d] if d and d != "none" else []

    @property
    def text(self) -> Dict[str, Any]:
        """
        Minimal text metadata. We expose 'persistent' so eval can give a future value hint.
        Heuristic: persistent if the card grants mat points OR effect mentions ongoing value.
        """
        eff = (self.effect or "").lower()
        persistent = bool(self.mat_points) or ("persistent" in eff) or ("each round" in eff)
        return {"persistent": persistent}

    @property
    def vp_on_play(self) -> int:
        """
        Immediate VP when played from hand (library cards usually 0).
        VP token cards ('VP:') are handled outside Card, so default 0 here.
        """
        return 0

    @property
    def vp_on_mat(self) -> int:
        """
        VP gained when this card is placed on the mat (your CSV's mat_points).
        """
        return int(self.mat_points or 0)

    @property
    def cost_play_active(self) -> Dict[str, int]:
        """Cost to play from hand (active)."""
        return dict(self.play_cost)

    @property
    def cost_play_mat(self) -> Dict[str, int]:
        """Cost to play onto the mat (use same cost unless you model extra mat costs)."""
        return dict(self.play_cost)

    @property
    def gain_play_active(self) -> Dict[str, int]:
        """
        Immediate resource gains from playing active.
        Stubbed empty; make smarter later by parsing self.effect if you wish.
        """
        return {}

    @property
    def gain_play_mat(self) -> Dict[str, int]:
        """
        Immediate resource gains from playing to mat.
        Stubbed empty; persistent value is handled via text['persistent'] & vp_on_mat.
        """
        return {}

    # ----------------- CSV loader (your existing code) -----------------

    @staticmethod
    def from_row(row: Dict[str, str]) -> "Card":
        def as_int(x, d=0):
            try:
                return int(x)
            except Exception:
                return d

        def as_bool(x):
            s = str(x).strip().lower()
            return s in ("1", "true", "yes", "y")

        pc: Dict[str, int] = {}
        for k in _RES_KEYS:
            v = as_int(row.get(f"play_cost_{k}", 0))
            if v:
                pc[k] = v

        return Card(
            id=(row["id"] or "").strip(),
            name=(row["name"] or "").strip(),
            buy_cost_plasma=as_int(row.get("buy_cost_plasma", 2)),
            play_cost=pc,
            type_=(row.get("type", "None") or "None").strip(),
            domain=(row.get("domain", "None") or "None").strip(),
            mat_points=as_int(row.get("mat_points", 0)),
            can_play_on_mat=as_bool(row.get("can_play_on_mat", "true")),
            effect=(row.get("effect", "") or "").strip(),
        )

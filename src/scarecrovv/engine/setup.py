# src/scarecrovv/engine/setup.py
from __future__ import annotations
import random
from typing import Dict, List, Optional

from scarecrovv.config import Config
from scarecrovv.constants import RES
from scarecrovv.model.card import Card
from scarecrovv.model.player import PlayerState as Player
from scarecrovv.model.game import GameState as Game
from scarecrovv.io.load_cards import load_cards, load_globals


# ---------------- Helpers (deck / draw) ----------------
def reshuffle_if_needed(p: Player, rng: random.Random) -> None:
    if not p.deck and p.discard:
        rng.shuffle(p.discard)
        p.deck = p.discard[:]
        p.discard.clear()


def draw(g: Game, p: Player, n: int) -> None:
    for _ in range(n):
        if not p.deck:
            if p.discard:  # log before reshuffle
                g.emit({"a": "reshuffle", "p": p.id, "n": len(p.discard)})
            reshuffle_if_needed(p, g.rng)
        if not p.deck:
            return
        p.hand.append(p.deck.pop())



def draw_to_hand_size(g: Game, p: Player, target: int) -> None:
    need = max(0, target - len(p.hand))
    if need > 0:
        draw(g, p, need)


# ---------------- Mat / discount helpers ----------------
def slot2_type(g: Game, p: Player) -> Optional[str]:
    """Return the 'chosen type' for slot 2 (we default to the type of the card in slot 2)."""
    if 2 in p.mat:
        cid = p.mat[2]
        c = g.cards.get(cid)
        if c:
            return c.type_
    return None


def total_discount_for_card(g: Game, p: Player, c: Card) -> int:
    """
    Compute total 1-resource discount from:
    - Slot 2: chosen type
    - Slot 4: Critter
    - Slot 5: Farm
    - Slot 6: Wild
    Rule: discounts do not stack >1 (min(disc,1))
    """
    disc = 0
    s2 = slot2_type(g, p)
    if s2 and c.type_ == s2:
        disc += 1
    if 4 in p.mat and c.type_ == "Critter":
        disc += 1
    if 5 in p.mat and c.type_ == "Farm":
        disc += 1
    if 6 in p.mat and c.type_ == "Wild":
        disc += 1
    return min(disc, 1)


def discounted_cost(c: Card, disc: int) -> Dict[str, int]:
    """
    Apply a single 1-resource discount to the first nonzero entry in c.play_cost.
    (Matches our previous simple rule.)
    """
    if disc <= 0:
        return dict(c.play_cost)
    cost = dict(c.play_cost)
    for k in RES:
        if cost.get(k, 0) > 0:
            cost[k] -= 1
            if cost[k] <= 0:
                del cost[k]
            break
    return cost


def can_pay_res(p: Player, cost: Dict[str, int]) -> bool:
    return all(p.resources.get(k, 0) >= v for k, v in cost.items())


def pay_res(p: Player, cost: Dict[str, int]) -> None:
    for k, v in cost.items():
        p.resources[k] = p.resources.get(k, 0) - v


# ----- Effect tag parsing & compost trigger extraction -----
def effect_tags(effect_str: str) -> List[str]:
    if not effect_str:
        return []
    return [t.strip() for t in effect_str.split(";") if t.strip()]


def compost_gains_for(card: Card) -> Dict[str, int]:
    """
    Recognizes both syntaxes:
      - if_composted_gain:ash:1
      - on_compost:ash:1
    Returns a resource->amount dict (only for valid RES keys, positive amounts).
    """
    gains: Dict[str, int] = {}
    for tag in effect_tags(card.effect):
        if tag.startswith("if_composted_gain:") or tag.startswith("on_compost:"):
            parts = tag.split(":")
            # prefix, resource, amount
            if len(parts) >= 3:
                res = parts[1].strip().lower()
                try:
                    amt = int(parts[2].strip())
                except Exception:
                    amt = 0
                if res in RES and amt > 0:
                    gains[res] = gains.get(res, 0) + amt
    return gains


def grant_resources(p: Player, grants: Dict[str, int]) -> None:
    for k, v in grants.items():
        p.resources[k] = p.resources.get(k, 0) + v


def compost_from_hand(g: Game, pid: int, index: int, reason: str):
    """
    Remove a specific hand index & trigger on_compost gain if the token is a library card.
    Logs both the compost and the on_compost gain (if any).
    """
    p = g.players[pid]
    if not (0 <= index < len(p.hand)):
        return None
    tok = p.hand.pop(index)

    # If it's a library id, check compost gains
    if tok in g.cards:
        c = g.cards[tok]
        gains = compost_gains_for(c)
        if gains:
            grant_resources(p, gains)
            g.emit({"t": g.turn, "a": "on_compost_gain", "p": pid, "cid": c.id, "grants": gains, "reason": reason})

    # Log the compost for traceability
    g.emit({"t": g.turn, "a": "compost", "p": pid, "card": tok, "reason": reason})
    return tok


# ---------------- Setup ----------------
def setup(cfg: Config) -> Game:
    rng = random.Random(cfg.seed)

    # Load library: animals + globals
    lib: Dict[str, Card] = {}
    lib.update(load_cards(cfg.cards_csv))
    lib.update(load_globals(cfg.globals_csv))  # loader should set type_="Global", can_play_on_mat=False

    # Build supply (expand each card id into N copies; globals usually excluded from supply)
    supply: List[str] = [cid for cid, c in lib.items()
                         if c.type_ != "Global"
                         for _ in range(cfg.copies_per_unique)]
    rng.shuffle(supply)

    # Pool (face-up market)
    pool: List[str] = [supply.pop() for _ in range(min(10, len(supply)))]

    # Players
    players: List[Player] = []
    for pid in range(cfg.players):
        deck = ["RES:plasma"] * 6 + ["VP:1"] * 4
        rng.shuffle(deck)
        players.append(Player(id=pid, deck=deck, hand=[], discard=[]))

    # Game state
    g = Game(cfg=cfg, rng=rng, cards=lib, supply=supply, pool=pool, players=players)

    g.field_occupancy = {k: int(g.field_occupancy.get(k) or 0) for k in g.field_capacity.keys()}


    # Optional per-round achievement tracking (used by some global effects)
    # We attach dynamically so older GameState definitions don't break.
    g.domains_played_this_round = [set() for _ in range(cfg.players)]  # type: ignore[attr-defined]

    # Draw opening hands
    for p in g.players:
        draw_to_hand_size(g, p, cfg.hand_size)

    # Opening income (your snippet gives +1 plasma at setup)
    for p in g.players:
        p.resources["plasma"] = p.resources.get("plasma", 0) + 1

    # Field capacities & occupancy (defaulted here; can be overridden elsewhere)
    if not g.field_capacity:
        g.field_capacity = {
            "plasma": 1,
            "ash": 1,
            "shards": 1,
            "forage": 1,
            "rookery": 1,
            "compost": 1,
            "initiative": 1,
        }
    if not g.field_occupancy:
        g.field_occupancy = {k: 0 for k in g.field_capacity}

    # Round flags
    if not g.hand_size_delta_next_round:
        g.hand_size_delta_next_round = {p.id: 0 for p in g.players}
    g.forage_yield_bonus_this_round = 0
    g.blight_compost_at_end = False
    g.first_to_three_domains_claimed = False

    # First player
    # First player (rotate if start_offset present; else default 0)
    start_offset = int(getattr(cfg, "start_offset", 0)) % len(players)
    g.start_player = start_offset
    g.current_player = g.start_player


    return g



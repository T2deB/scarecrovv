# src/scarecrovv/io/load_cards.py
from __future__ import annotations
import csv
from typing import Dict, Iterable
from scarecrovv.model.card import Card
from scarecrovv.constants import RES

def _as_int(x, default=0):
    try:
        return int(str(x).strip())
    except Exception:
        return default

def _as_bool(x, default=True):
    s = str(x).strip().lower()
    if s in ("1","true","yes","y"): return True
    if s in ("0","false","no","n"): return False
    return default

def _row_to_play_cost(row: Dict[str,str]) -> Dict[str,int]:
    pc = {}
    for r in RES:
        v = _as_int(row.get(f"play_cost_{r}", 0), 0)
        if v: pc[r] = v
    return pc

def _read_csv(path: str) -> Iterable[Dict[str,str]]:
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # normalize keys
            yield { (k.strip() if k else k): (v.strip() if isinstance(v,str) else v)
                    for k,v in row.items() }

def load_cards(path: str) -> Dict[str, Card]:
    """
    Load normal (non-global) cards from CSV.
    Expected columns (robust to missing optional ones):
      id,name,buy_cost_plasma,play_cost_<res>,type,domain,mat_points,can_play_on_mat,effect
    """
    lib: Dict[str, Card] = {}
    for row in _read_csv(path):
        cid = row["id"].strip()
        c = Card(
            id=cid,
            name=row.get("name","").strip() or cid,
            buy_cost_plasma=_as_int(row.get("buy_cost_plasma", 2)),
            play_cost=_row_to_play_cost(row),
            type_=row.get("type","None").strip() or "None",
            domain=row.get("domain","None").strip() or "None",
            mat_points=_as_int(row.get("mat_points", 0)),
            can_play_on_mat=_as_bool(row.get("can_play_on_mat", "true")),
            effect=row.get("effect","").strip(),
        )
        lib[cid] = c
    return lib

def load_globals(path: str) -> Dict[str, Card]:
    """
    Load global cards from CSV and force:
      - type_ = "Global"
      - domain = "None"
      - can_play_on_mat = False
    We still read buy_cost_plasma and play costs so globals can have costs.
    Expected columns (flexible):
      id,name,effect,buy_cost_plasma,play_cost_<res>,(optional extras ignored)
    """
    lib: Dict[str, Card] = {}
    for row in _read_csv(path):
        cid = row["id"].strip()
        c = Card(
            id=cid,
            name=row.get("name","").strip() or cid,
            buy_cost_plasma=_as_int(row.get("buy_cost_plasma", 2)),
            play_cost=_row_to_play_cost(row),
            type_="Global",
            domain="None",
            mat_points=0,
            can_play_on_mat=False,
            effect=row.get("effect","").strip(),
        )
        lib[cid] = c
    return lib

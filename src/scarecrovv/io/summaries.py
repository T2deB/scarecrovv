# src/scarecrovv/io/summaries.py
from __future__ import annotations
import os
from typing import Any, Dict, List, Tuple
from collections import defaultdict, Counter
import pandas as pd

FIELDS = ("plasma","ash","shards","forage","rookery","compost","initiative")

def _key(a: Dict[str,Any]) -> str:
    """Support both {'a':...} and {'action':...} logs."""
    return a.get("a", a.get("action", ""))

def _payload(a: Dict[str,Any]) -> Dict[str,Any]:
    return a.get("payload", a)

def build_card_and_field_rows(outs: List[Dict[str,Any]]) -> Tuple[List[Dict[str,Any]], List[Dict[str,Any]]]:
    # ---- per-card aggregation ----
    C = defaultdict(lambda: {
        "bought": 0,
        "played": 0,
        "to_mat_plays": 0,
        "slot_usage": Counter(),
        "games_owned": 0,
        "wins_when_owned": 0,
        "first_play": [],
    })

    # ---- per-field/per-player aggregation (across all games) ----
    per_player = defaultdict(lambda: {
        # field visits
        **{f"visits_{f}": 0 for f in FIELDS},
        "initiative_claims": 0,
        # VP economy
        "buy_vp_1": 0, "buy_vp_2": 0, "buy_vp_3": 0,
        "play_vp_1": 0, "play_vp_2": 0, "play_vp_3": 0,
        "plays_vp": 0,
        "vp_from_tokens": 0,       # sum of vp + bonus from play_vp
        "vp_bonus_from_slot1": 0,  # sum of 'bonus'
        "vp_end_total": 0,         # end-of-game VP total (summed across games)
        "games": 0,                # how many games this seat appeared in
    })

    for g in outs:
        winner = g.get("winner", None)
        owned_by_player = defaultdict(set)

        # capture end-of-game VP once per game
        game_end_vps = None

        for e in g.get("events", []):
            a = _key(e); p = _payload(e)
            pid = p.get("p", p.get("player"))

            if a == "buy":
                cid = p.get("cid") or p.get("card") or p.get("id")
                if cid:
                    C[cid]["bought"] += 1
                    if pid is not None:
                        owned_by_player[pid].add(cid)

            elif a == "play_card":
                cid = p.get("cid")
                if cid:
                    C[cid]["played"] += 1
                    if p.get("to_mat"):
                        C[cid]["to_mat_plays"] += 1
                        slot = p.get("slot")
                        if slot is not None:
                            C[cid]["slot_usage"][int(slot)] += 1
                t = p.get("t") or e.get("turn")
                if t is not None and cid:
                    C[cid]["first_play"].append(int(t))

            elif a == "play_global":
                cid = p.get("cid")
                if cid:
                    C[cid]["played"] += 1

            elif a == "worker":
                if pid is None:
                    continue
                field = p.get("field")
                if field in FIELDS:
                    per_player[pid][f"visits_{field}"] += 1
                    if field == "initiative":
                        per_player[pid]["initiative_claims"] += 1

# 🔹 VP events
            elif a == "buy_vp":
                v = int(p.get("vp", 0) or p.get("v", 0) or 0)
                if pid is not None and v in (1,2,3):
                    per_player[pid][f"buy_vp_{v}"] = per_player[pid].get(f"buy_vp_{v}", 0) + 1

            elif a == "play_vp":
                v = int(p.get("vp", 0) or 0)
                if pid is not None and v in (1,2,3):
                    per_player[pid][f"play_vp_{v}"] = per_player[pid].get(f"play_vp_{v}", 0) + 1
                    per_player[pid]["plays_vp"] = per_player[pid].get("plays_vp", 0) + 1
                    per_player[pid]["vp_from_tokens"] = per_player[pid].get("vp_from_tokens", 0) + v
                    # If you emit slot-1 bonus in the same log, add it here:
                    b = int(p.get("bonus", 0) or 0)
                    per_player[pid]["vp_bonus_from_slot1"] = per_player[pid].get("vp_bonus_from_slot1", 0) + b


            elif a == "game_end_vp":
                # payload: {"vps":[...]}
                game_end_vps = p.get("vps")

        # First-play dict & ownership from players payload (if present)
        for pid, pp in enumerate(g.get("players", [])):
            first = pp.get("first_play", {}) or {}
            for cid, t in first.items():
                C[cid]["first_play"].append(int(t))
            for cid in pp.get("owned", []) or []:
                owned_by_player[pid].add(cid)

        for pid, owned_set in owned_by_player.items():
            for cid in owned_set:
                C[cid]["games_owned"] += 1
                if winner == pid:
                    C[cid]["wins_when_owned"] += 1

        # finalize end-of-game VP accumulation for this game
        if game_end_vps is not None:
            for pid, vp in enumerate(game_end_vps):
                per_player[pid]["vp_end_total"] += int(vp)
                per_player[pid]["games"] += 1

    # finalize per-card rows
    card_rows: List[Dict[str,Any]] = []
    for cid, m in C.items():
        played = m["played"]
        to_mat_plays = m["to_mat_plays"]
        games_owned = m["games_owned"]
        wins_when_owned = m["wins_when_owned"]
        first_list = m["first_play"]
        card_rows.append({
            "card_id": cid,
            "bought": m["bought"],
            "played": played,
            "to_mat_rate": (to_mat_plays / played) if played else None,
            "games_owned": games_owned,
            "winrate_when_owned": (wins_when_owned / games_owned) if games_owned else None,
            "slot_pref": dict(m["slot_usage"]) if m["slot_usage"] else {},
            "time_to_first_play": (min(first_list) if first_list else None),
        })

    # finalize per-field rows (one row per player)
    field_rows = []
    if per_player:
        for pid, data in sorted(per_player.items()):
            row = {"player_id": pid}
            row.update(data)
            field_rows.append(row)
    else:
        # fallback: emit zeros for 3 players so CSV isn't empty
        for pid in range(3):
            row = {"player_id": pid}
            for f in FIELDS: row[f"visits_{f}"] = 0
            row["initiative_claims"] = 0
            # VP defaults
            for v in (1,2,3):
                row[f"buy_vp_{v}"] = 0
                row[f"play_vp_{v}"] = 0
            row["plays_vp"] = 0
            row["vp_from_tokens"] = 0
            row["vp_bonus_from_slot1"] = 0
            row["vp_end_total"] = 0
            row["games"] = 0
            field_rows.append(row)

    return card_rows, field_rows

CARD_COLS = [
    "card_id","bought","played","to_mat_rate",
    "games_owned","winrate_when_owned","slot_pref","time_to_first_play"
]
FIELD_COLS = (
    ["player_id"]
    + [f"visits_{f}" for f in FIELDS]
    + ["initiative_claims"]
    # 🔹 VP columns
    + [f"buy_vp_{v}" for v in (1,2,3)]
    + [f"play_vp_{v}" for v in (1,2,3)]
    + ["plays_vp","vp_from_tokens","vp_bonus_from_slot1","vp_end_total","games"]
)

def write_summaries(cfg, games:int, outs: List[Dict[str,Any]]) -> Tuple[str,str]:
    cards, fields = build_card_and_field_rows(outs)

    os.makedirs("summaries", exist_ok=True)
    cards_path  = f"summaries/summary_cards_{cfg.seed}_{games}games.csv"
    fields_path = f"summaries/summary_fields_{cfg.seed}_{games}games.csv"

    # cards
    if cards:
        dfc = pd.DataFrame(cards)
        for col in CARD_COLS:
            if col not in dfc.columns: dfc[col] = None
        dfc = dfc[CARD_COLS]
    else:
        dfc = pd.DataFrame(columns=CARD_COLS)

    # fields (+ VP metrics)
    if fields:
        dff = pd.DataFrame(fields)
        for col in FIELD_COLS:
            if col not in dff.columns:
                dff[col] = 0 if (col.startswith("visits_") or col.startswith("buy_vp_") or col.startswith("play_vp_")
                                 or col in ("initiative_claims","plays_vp","vp_from_tokens","vp_bonus_from_slot1","vp_end_total","games")) else None
        dff = dff[FIELD_COLS]
    else:
        dff = pd.DataFrame(columns=FIELD_COLS)

    dfc.to_csv(cards_path, index=False)
    dff.to_csv(fields_path, index=False)
    return cards_path, fields_path

#!/usr/bin/env python3
import argparse, os, sys, csv, glob, re, pathlib, time
import pandas as pd

# Optional dependency for DataFrame.to_markdown()
try:
    import tabulate  # noqa: F401
    _HAS_TABULATE = True
except Exception:
    _HAS_TABULATE = False

def df_to_md(df: pd.DataFrame) -> str:
    """Markdown table if 'tabulate' is installed, otherwise plain text fallback."""
    try:
        if _HAS_TABULATE:
            return df.to_markdown(index=False)
    except Exception:
        pass
    return "```\n" + df.to_string(index=False) + "\n```"

RUN_RE = re.compile(r"summary_(cards|fields)_(?P<seed>\d+)_(?P<games>\d+)games\.csv$")

def load_card_names(cards_csv: str) -> dict:
    names={}
    if not os.path.exists(cards_csv):
        return names
    with open(cards_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = (row.get("id") or "").strip()
            nm  = (row.get("name") or cid).strip()
            if cid: names[cid]=nm
    return names

def _filter_runs(files, run_hint: str|None, seed_filter: str|None, games_filter: str|None):
    out = []
    for p in files:
        base = os.path.basename(p)
        if run_hint and run_hint not in base:
            continue
        m = RUN_RE.search(base)
        if not m:
            continue
        seed = m.group("seed")
        games = m.group("games")
        if seed_filter and seed != str(seed_filter):
            continue
        if games_filter and games != str(games_filter):
            continue
        out.append((p, int(seed), int(games)))
    return out

def _pick_one(matches, prefer_latest: bool):
    if not matches:
        return None
    if prefer_latest:
        # pick by mtime
        matches.sort(key=lambda x: os.path.getmtime(x[0]))
        return matches[-1][0]
    # pick last by path sort (backwards compatible)
    matches.sort(key=lambda x: x[0])
    return matches[-1][0]

def pick_run_files(summaries_dir: str, run_hint: str|None, seed_filter: str|None, games_filter: str|None, prefer_latest: bool):
    cards = sorted(glob.glob(os.path.join(summaries_dir, "summary_cards_*_*games.csv")))
    fields = sorted(glob.glob(os.path.join(summaries_dir, "summary_fields_*_*games.csv")))
    if not cards and not fields:
        return None, None, None, None

    cards_matches  = _filter_runs(cards,  run_hint, seed_filter, games_filter)
    fields_matches = _filter_runs(fields, run_hint, seed_filter, games_filter)

    cards_csv  = _pick_one(cards_matches,  prefer_latest)
    fields_csv = _pick_one(fields_matches, prefer_latest)

    # extract seed/games from whichever we found
    seed = games = None
    for p in (cards_csv, fields_csv):
        if p:
            m = RUN_RE.search(os.path.basename(p))
            if m:
                seed  = m.group("seed")
                games = m.group("games")
                break
    return cards_csv, fields_csv, seed, games

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summaries_dir", default="summaries")
    ap.add_argument("--cards_csv", default="cards.csv")
    ap.add_argument("--out", default="summaries/analysis_report.md")
    ap.add_argument("--run", default=None, help="Pick a specific run like 42_10games or 69games (substring match)")
    ap.add_argument("--seed", dest="seed_filter", default=None, help="Exact seed filter (e.g. 42)")
    ap.add_argument("--games", dest="games_filter", default=None, help="Exact games filter (e.g. 69)")
    ap.add_argument("--latest", action="store_true", help="Prefer newest-by-mtime when multiple candidates match")
    ap.add_argument("--sections", default="cards,fields,seats", help="Comma list: any of cards,fields,seats")
    # direct paths override discovery
    ap.add_argument("--cards_summary", default=None)
    ap.add_argument("--fields_summary", default=None)
    ap.add_argument("--seats_summary", default=None)
    ap.add_argument("--explore", type=float, default=None, help="(optional) exploration rate to show in header")
    args = ap.parse_args()

    sections = {s.strip().lower() for s in args.sections.split(",") if s.strip()}
    os.makedirs(args.summaries_dir, exist_ok=True)

    # If explicit paths not given, discover by filters
    if args.cards_summary or args.fields_summary:
        cards_csv = args.cards_summary
        fields_csv = args.fields_summary
        seed = args.seed_filter
        games = args.games_filter
    else:
        cards_csv, fields_csv, seed, games = pick_run_files(
            args.summaries_dir, args.run, args.seed_filter, args.games_filter, args.latest
        )

    if not cards_csv and not fields_csv and not args.seats_summary:
        print("[analyze] No matching summary CSVs found.")
        sys.exit(1)

    # Seat summary path (if not given explicitly)
    seats_csv = args.seats_summary
    if not seats_csv and seed and games:
        seats_csv = os.path.join(args.summaries_dir, f"summary_seats_{seed}_{games}games.csv")

    # Tell the user what we’re analyzing
    print("[analyze] Using:")
    print(f"  cards : {cards_csv or '-'}")
    print(f"  fields: {fields_csv or '-'}")
    print(f"  seats : {seats_csv or '-'}")

    report = ["# Scarecrovv Simulation – Analysis Report", ""]
    meta = []
    if games: meta.append(f"**Games simulated:** {games}")
    if seed:  meta.append(f"**Seed:** {seed}")
    if args.explore is not None: meta.append(f"**Explore:** {args.explore:.2f}")
    if meta:
        report.append(" | ".join(meta))
        report.append("")

    # card names (for pretty printing)
    card_names = load_card_names(args.cards_csv)

    # --- Card Summary ---
    if "cards" in sections and cards_csv:
        try:
            dfc = pd.read_csv(cards_csv)
        except pd.errors.EmptyDataError:
            dfc = pd.DataFrame(columns=["card_id"])
        if "card_id" in dfc.columns and "card_name" not in dfc.columns:
            dfc["card_name"] = dfc["card_id"].map(card_names).fillna(dfc["card_id"])
        report.append(f"## Card Summary ({os.path.basename(cards_csv)})\n")
        cols = [c for c in ["card_id","card_name","bought","played","to_mat_rate","games_owned","winrate_when_owned","time_to_first_play"] if c in dfc.columns]
        if cols:
            report.append(df_to_md(dfc[cols].sort_values("bought", ascending=False)))
        else:
            report.append("_No expected columns found in card summary._")
        report.append("")

    # --- Field Summary ---
    if "fields" in sections and fields_csv:
        try:
            dff = pd.read_csv(fields_csv)
        except pd.errors.EmptyDataError:
            dff = pd.DataFrame(columns=["player_id"])

        report.append(f"## Field Summary ({os.path.basename(fields_csv)})\n")
        report.append(df_to_md(dff))
        report.append("")

        # --- VP Summary (from fields file) ---
        vp_cols = [c for c in [
            "buy_vp_1","buy_vp_2","buy_vp_3",
            "play_vp_1","play_vp_2","play_vp_3",
            "plays_vp","vp_from_tokens","vp_bonus_from_slot1","vp_end_total","games"
        ] if c in dff.columns]

        if vp_cols:
            agg = dff[["player_id"] + vp_cols].copy()
            # per-seat rates per game if 'games' is present and >0
            if "games" in agg.columns and agg["games"].gt(0).all():
                for c in ["vp_from_tokens","vp_end_total","plays_vp",
                        "buy_vp_1","buy_vp_2","buy_vp_3",
                        "play_vp_1","play_vp_2","play_vp_3"]:
                    if c in agg.columns:
                        agg[f"{c}_per_game"] = agg[c] / agg["games"]

            report.append("## VP Summary\n")
            report.append(df_to_md(agg.fillna(0)))
            report.append("")

    # --- Seat Summary (winrate, avg VP, ties) ---
    if "seats" in sections and seats_csv and os.path.exists(seats_csv):
        dfs = pd.read_csv(seats_csv)
        report.append(f"## Seat Summary ({os.path.basename(seats_csv)})\n")
        cols = [c for c in ["seat","games","wins","winrate","avg_vp","vp_std","ties_games","starts"] if c in dfs.columns]
        if cols:
            report.append(df_to_md(dfs[cols]))
        else:
            report.append("_No expected columns found in seat summary._")
        report.append("")

    # Write the report to disk (always, not just if "seats" section is present)
    out_path = args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"[analyze] Wrote {out_path}")

if __name__ == "__main__":
    main()

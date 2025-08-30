#!/usr/bin/env python3
import argparse, os, glob, re, csv, math, ast
import pandas as pd

# --- helpers: markdown fallback if tabulate not installed ---
try:
    import tabulate  # noqa: F401
    _HAS_TAB = True
except Exception:
    _HAS_TAB = False

def df_to_md(df: pd.DataFrame) -> str:
    if _HAS_TAB:
        try: return df.to_markdown(index=False)
        except Exception: pass
    return "```\n" + df.to_string(index=False) + "\n```"

RUN_RE = re.compile(r"summary_(cards|fields)_(?P<seed>\d+)_(?P<games>\d+)games\.csv$")

def parse_seeds_arg(s: str | None):
    """Accept '42,100-105,200' (commas and ranges). Ignore blanks."""
    if not s:
        return None
    out = set()
    for part in s.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            try:
                a, b = int(a), int(b)
            except ValueError:
                continue
            if a <= b:
                out.update(range(a, b + 1))
            else:  # reversed range, still accept
                out.update(range(b, a + 1))
        else:
            try:
                out.add(int(part))
            except ValueError:
                pass
    return out


def _find_runs(summaries_dir: str):
    cards = glob.glob(os.path.join(summaries_dir, "summary_cards_*_*games.csv"))
    fields = glob.glob(os.path.join(summaries_dir, "summary_fields_*_*games.csv"))
    # Map (seed,games) -> paths (cards, fields)
    index = {}
    for p in cards + fields:
        m = RUN_RE.search(os.path.basename(p))
        if not m: continue
        k = (int(m.group("seed")), int(m.group("games")))
        role = m.group(1)  # "cards" or "fields"
        index.setdefault(k, {})[role] = p
    # keep only runs that have both files
    return {k:v for k,v in index.items() if "cards" in v and "fields" in v}

def _load_card_names(cards_csv: str) -> dict:
    names={}
    if os.path.exists(cards_csv):
        with open(cards_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cid = (row.get("id") or "").strip()
                nm  = (row.get("name") or cid).strip()
                if cid: names[cid]=nm
    return names

def _weighted_mean(values, weights):
    num = sum(v*w for v,w in zip(values,weights))
    den = sum(weights)
    return (num/den) if den else float("nan")

def _pooled_std(groups):
    """
    groups: iterable of (mean, std, n)
    returns pooled std (unbiased sample std across groups)
    """
    means, stds, ns = zip(*[(m,s,n) for (m,s,n) in groups if n and not math.isnan(m) and not math.isnan(s)])
    if not ns: return float("nan")
    N = sum(ns)
    if N <= 1: return float("nan")
    # pooled variance = [sum((n-1)*s^2) + sum(n*(m-gt)^2)] / (N-1)
    gt = sum(m*n for m,n in zip(means,ns)) / N
    within = sum((n-1)*(s**2) for s,n in zip(stds,ns))
    between = sum(n*((m-gt)**2) for m,n in zip(means,ns))
    var = (within + between) / (N-1)
    return math.sqrt(var)

def aggregate(summaries_dir: str, games_filter: int|None, seeds_filter: set[int]|None, cards_csv: str|None):
    runs = _find_runs(summaries_dir)

    # filter
    if games_filter is not None:
        runs = {k:v for k,v in runs.items() if k[1] == games_filter}
    if seeds_filter:
        runs = {k:v for k,v in runs.items() if k[0] in seeds_filter}

    if not runs:
        raise SystemExit("[aggregate] No matching runs found.")

        # --- aggregate CARDS ---
    # We'll sum base counts; recompute weighted rates
    card_rows = []
    slot_counter = {}
    for (seed, games), paths in sorted(runs.items()):
        dfc = pd.read_csv(paths["cards"])

        # Ensure expected columns exist
        for col in ("bought","played","games_owned","winrate_when_owned","time_to_first_play","to_mat_rate","slot_pref"):
            if col not in dfc.columns:
                dfc[col] = 0

        # estimate counts from rates for per-run
        dfc["__played_nonnull"] = dfc["played"].fillna(0)
        dfc["__to_mat_plays_est"] = dfc["to_mat_rate"].fillna(0) * dfc["__played_nonnull"]
        dfc["__wins_when_owned_est"] = dfc["winrate_when_owned"].fillna(0) * dfc["games_owned"].fillna(0)

        # fold slot_pref dicts across runs
        if "slot_pref" in dfc.columns:
            for cid, sp in zip(dfc.get("card_id", []), dfc["slot_pref"]):
                if pd.isna(sp):
                    continue
                try:
                    d = ast.literal_eval(sp) if isinstance(sp, str) else dict(sp)
                except Exception:
                    d = {}
                slot_counter.setdefault(cid, {})
                for k, v in d.items():
                    slot_counter[cid][int(k)] = slot_counter[cid].get(int(k), 0) + int(v)

        card_rows.append(
            dfc[[
                "card_id","bought","played","games_owned",
                "__wins_when_owned_est","time_to_first_play","__to_mat_plays_est"
            ]]
        )

    if card_rows:
        C = pd.concat(card_rows, ignore_index=True).fillna(0)
        # group & sum the counts we can sum safely
        sum_cols = ["bought","played","games_owned","__wins_when_owned_est","__to_mat_plays_est"]
        agg = C.groupby("card_id", as_index=False)[sum_cols].sum()

        # recompute rates from summed counts
        agg["to_mat_rate"] = agg["__to_mat_plays_est"] / agg["played"].replace(0, pd.NA)
        agg["winrate_when_owned"] = agg["__wins_when_owned_est"] / agg["games_owned"].replace(0, pd.NA)

        # earliest (minimum) time_to_first_play across runs
        ttfp = C.groupby("card_id")["time_to_first_play"].min().reset_index()
        agg = agg.merge(ttfp, on="card_id", how="left")

        # pretty names
        names = _load_card_names(cards_csv) if cards_csv else {}
        agg["card_name"] = agg["card_id"].map(names).fillna(agg["card_id"])

        # merged slot pref
        agg["slot_pref"] = agg["card_id"].map(lambda cid: slot_counter.get(cid, {}))

        agg = agg[[
            "card_id","card_name","bought","played","to_mat_rate",
            "games_owned","winrate_when_owned","time_to_first_play","slot_pref"
        ]]
        df_cards = agg.sort_values(["bought","played"], ascending=False)
    else:
        df_cards = pd.DataFrame(columns=["card_id"])

    # --- aggregate FIELDS (+ VP) ---
    field_rows = []
    for (seed, games), paths in sorted(runs.items()):
        dff = pd.read_csv(paths["fields"])
        field_rows.append(dff)
    df_fields = pd.concat(field_rows, ignore_index=True) if field_rows else pd.DataFrame(columns=["player_id"])
    # sum numeric columns per seat
    if not df_fields.empty:
        num_cols = [c for c in df_fields.columns if c != "player_id" and pd.api.types.is_numeric_dtype(df_fields[c])]
        df_fields_agg = df_fields.groupby("player_id", as_index=False)[num_cols].sum().fillna(0)
    else:
        df_fields_agg = df_fields

    # --- aggregate SEATS (if present) ---
    # Look for all seat CSVs that match the filtered set
    seat_rows = []
    for (seed, games) in runs.keys():
        p = os.path.join(summaries_dir, f"summary_seats_{seed}_{games}games.csv")
        if os.path.exists(p):
            dfs = pd.read_csv(p)
            dfs["__seed"] = seed; dfs["__games"] = games
            seat_rows.append(dfs)
    if seat_rows:
        S = pd.concat(seat_rows, ignore_index=True)
        # aggregate per seat
        def pooled(df):
            games = df["games"].sum()
            wins = df["wins"].sum()
            starts = df["starts"].sum() if "starts" in df.columns else 0
            winrate = wins / games if games else float("nan")
            # weighted mean of avg_vp + pooled std if available
            if "avg_vp" in df.columns:
                avg = _weighted_mean(df["avg_vp"], df["games"])
                if "vp_std" in df.columns:
                    std = _pooled_std([(m, s, n) for m,s,n in zip(df["avg_vp"], df.get("vp_std", [float("nan")]*len(df)), df["games"])])
                else:
                    std = float("nan")
            else:
                avg, std = float("nan"), float("nan")
            return pd.Series({"games":games, "wins":wins, "winrate":winrate, "avg_vp":avg, "vp_std":std, "ties_games":df.get("ties_games", pd.Series([0]*len(df))).sum(), "starts":starts})
        df_seats = S.groupby("seat").apply(pooled).reset_index()
    else:
        df_seats = pd.DataFrame()

    return df_cards, df_fields_agg, df_seats, runs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summaries_dir", default="summaries")
    ap.add_argument("--out", default="summaries/aggregate_report.md")
    ap.add_argument("--games", type=int, default=None, help="Only include runs with exactly this many games")
    ap.add_argument("--seeds", default=None, help="Comma-separated list of seeds to include (default: all)")
    ap.add_argument("--cards_csv", default="cards.csv")
    args = ap.parse_args()

    seeds = parse_seeds_arg(args.seeds)
    df_cards, df_fields, df_seats, runs = aggregate(args.summaries_dir, args.games, seeds, args.cards_csv)

    report = []
    report.append("# Scarecrovv — Aggregate Report\n")
    sel = f"games={args.games}" if args.games is not None else "games=ANY"
    if seeds: sel += f", seeds={sorted(seeds)}"
    report.append(f"_Aggregating {len(runs)} runs ({sel})._\n")

    if not df_cards.empty:
        report.append("## Cards (aggregated across runs)\n")
        report.append(df_to_md(df_cards))
        report.append("")
    if not df_fields.empty:
        report.append("## Fields & VP (aggregated across runs)\n")
        report.append(df_to_md(df_fields))
        report.append("")
    if not df_seats.empty:
        report.append("## Seats (aggregated across runs)\n")
        report.append(df_to_md(df_seats))
        report.append("")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"[aggregate] Wrote {args.out}")
    print(f"[aggregate] Included {len(runs)} runs: {sorted(runs.keys())}")

if __name__ == "__main__":
    main()

"""
fetch_elo.py — Club Elo Ratings via clubelo.com, per league
===============================================================
Your WC model's version computed Elo from scratch off the martj42
INTERNATIONAL results dataset, with its own K-factor system (World Cup=60,
Qualifiers=50, Friendly=30) and a hardcoded BASELINE_ELO dict of ~65 national
teams. None of that machinery applies to club football.

THE GOOD NEWS: config/leagues.py (and your original config/config.py) already
defined CLUBELO_BASE_URL = "http://api.clubelo.com" — it just was never
actually used anywhere in the WC pipeline (there's no such thing as club Elo
for a national team). clubelo.com maintains real, continuously-updated Elo
ratings for essentially every professional club in the world, updated after
every match — so for league play, we can pull real numbers directly instead
of computing our own from raw results the way the WC model had to.

clubelo.com's API: GET http://api.clubelo.com/{YYYY-MM-DD} returns a CSV
snapshot of every tracked club's Elo as of that date, with columns
Rank, Club, Country, Level, Elo, From, To. Filtering by Country + Level
(e.g. Country="ENG", Level=1 for the Premier League; Level=2 for the
Championship) gives exactly one league's ratings.

Output: data/processed/{league_key}_elo_ratings.json — a flat
{team_name: elo} dict, matching the path predictions_engine.py's main()
already looks for. Missing entirely -> predictions_engine.py runs fine
without an Elo cross-check (see DixonColesModel.predict_match).
"""

import requests
import pandas as pd
import json
import os
import sys
import io
from datetime import date
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.leagues import CLUBELO_BASE_URL, TEAM_ALIASES, LEAGUES, PROCESSED_DIR, get_league


def normalize_team(name) -> str:
    if not isinstance(name, str):
        return str(name) if name else ""
    return TEAM_ALIASES.get(name, name)


def fetch_clubelo_snapshot(as_of: str = None) -> pd.DataFrame:
    """Fetch clubelo.com's full Elo snapshot (every tracked club, every
    league) for a given date, default today. One request covers all 4 of
    our leagues since they're filtered out of the same snapshot below."""
    as_of = as_of or date.today().isoformat()
    url = f"{CLUBELO_BASE_URL}/{as_of}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    return df


def extract_league_elo(snapshot: pd.DataFrame, league_key: str) -> dict:
    """Filter the full snapshot down to one league's clubs via
    clubelo_country / clubelo_level from config/leagues.py."""
    cfg = get_league(league_key)
    country = cfg.get("clubelo_country")
    level = cfg.get("clubelo_level")

    if not country or level is None:
        print(f"  ⚠ {cfg['label']}: no clubelo_country/clubelo_level configured — skipping")
        return {}

    subset = snapshot[(snapshot["Country"] == country) & (snapshot["Level"] == level)]

    elo = {}
    for _, row in subset.iterrows():
        team = normalize_team(row["Club"])
        try:
            elo[team] = round(float(row["Elo"]))
        except (ValueError, TypeError):
            continue

    return elo


def get_elo_win_probability(elo_home: float, elo_away: float, neutral: bool = False):
    """Convert Elo to win/draw/loss probabilities. Kept as a standalone
    diagnostic utility (e.g. for spot-checking a matchup by hand) — the
    pipeline itself feeds raw Elo numbers into DixonColesModel.predict_match's
    elo_ratings cross-check rather than calling this directly. HOME_ADV=65
    is a rough club-football home-advantage estimate in Elo points; not
    identical to the WC model's international-football value, since home
    advantage genuinely differs between international and club matches."""
    HOME_ADV = 0 if neutral else 65
    elo_diff = (elo_home + HOME_ADV) - elo_away
    p_home_win_raw = 1 / (1 + 10 ** (-elo_diff / 400))
    diff_scale = abs(elo_diff) / 400
    p_draw = max(0.05, min(0.26 * (1 - 0.5 * diff_scale), 0.30))
    remaining = 1 - p_draw
    p_home_win = p_home_win_raw * remaining
    p_away_win = (1 - p_home_win_raw) * remaining
    return round(p_home_win, 4), round(p_draw, 4), round(p_away_win, 4)


def main(league_key: str = None):
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    keys = [league_key] if league_key else list(LEAGUES.keys())

    print("\n=== Fetching Club Elo Ratings (clubelo.com) ===\n")

    try:
        snapshot = fetch_clubelo_snapshot()
        print(f"  ✓ Snapshot fetched: {len(snapshot)} clubs tracked worldwide")
    except Exception as e:
        print(f"  ✗ ClubElo fetch failed: {e} — skipping Elo for this run "
              f"(pipeline still works without it)")
        return {}

    all_ratings = {}
    for lk in keys:
        cfg = LEAGUES[lk]
        elo = extract_league_elo(snapshot, lk)
        path = f"{PROCESSED_DIR}/{lk}_elo_ratings.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(elo, f, indent=2)
        print(f"  ✓ {cfg['label']}: {len(elo)} clubs -> {path}")
        all_ratings[lk] = elo

    return all_ratings


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", choices=list(LEAGUES.keys()))
    args = parser.parse_args()
    result = main(args.league)
    total = sum(len(v) for v in result.values())
    print(f"\n✅ Elo ratings ready — {total} clubs across {len(result)} league(s)")

"""
data/fetch_results.py — Historical results fetcher, per league
==================================================================
The WC model's version of this file pulled from a handful of hardcoded
tournaments (WC, EC, CL codes) plus a martj42 qualifiers dataset, because
that's genuinely all the relevant international data that exists.

For domestic leagues you get real season-over-season depth for free from
football-data.org's competition endpoints, so this fetches N seasons
(config: fd_seasons) per league and writes one clean CSV per league to
data/processed/{league}_matches.csv — exactly the input dixon_coles_model.py
expects.

Rate limits: football-data.org's free tier is 10 req/min. With 4 leagues x
~6 seasons each = ~24 requests, add a short sleep between calls.
"""

import os
import time
import requests
import pandas as pd

from config.leagues import (
    FD_BASE_URL, FOOTBALL_DATA_KEY, LEAGUES, TEAM_ALIASES, PROCESSED_DIR,
    current_season_start_year,
)


def _normalise_team(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def _season_start_years(n_seasons: int) -> list:
    """football-data.org identifies a season by its start year, e.g. 2024
    for the 2024-25 season. Build the list of the last n_seasons start years."""
    current_start = current_season_start_year()
    return list(range(current_start - n_seasons + 1, current_start + 1))


def fetch_league_matches(league_key: str) -> pd.DataFrame:
    cfg = LEAGUES[league_key]
    code = cfg["fd_code"]
    seasons = _season_start_years(cfg["fd_seasons"])

    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    rows = []

    for season in seasons:
        url = f"{FD_BASE_URL}/competitions/{code}/matches"
        params = {"season": season, "status": "FINISHED"}
        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code == 429:
            print(f"    rate-limited, waiting 60s...")
            time.sleep(60)
            resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code != 200:
            print(f"    ⚠ {cfg['label']} season {season}: HTTP {resp.status_code}, skipping")
            continue

        data = resp.json()
        for m in data.get("matches", []):
            score = m.get("score", {}).get("fullTime", {})
            if score.get("home") is None or score.get("away") is None:
                continue
            rows.append({
                "date": m["utcDate"][:10],
                "home_team": _normalise_team(m["homeTeam"]["name"]),
                "away_team": _normalise_team(m["awayTeam"]["name"]),
                "home_goals": score["home"],
                "away_goals": score["away"],
                "season": season,
            })

        time.sleep(6.5)  # stay under free-tier 10 req/min

    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def main():
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    for league_key, cfg in LEAGUES.items():
        print(f"Fetching {cfg['label']} ({cfg['fd_seasons']} seasons)...")
        df = fetch_league_matches(league_key)
        out_path = f"{PROCESSED_DIR}/{league_key}_matches.csv"
        df.to_csv(out_path, index=False)
        print(f"  ✓ {cfg['label']}: {len(df)} matches -> {out_path}")


if __name__ == "__main__":
    main()

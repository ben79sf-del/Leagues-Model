"""
data/fetch_fixtures.py — Upcoming fixtures fetcher, per league
==================================================================
Adapted directly from your WC model's fetch_wc2026_fixtures() (inside
fetch_results.py) — same football-data.org endpoint pattern
(status filtered to SCHEDULED/TIMED), just parameterized per league
instead of hardcoded to competition "WC" / season 2026.

CHANGES FROM THE WC VERSION:
  - `season` uses config.leagues.current_season_start_year() (the current
    league season) instead of a hardcoded "2026" tournament year.
  - Dropped the `group` field (World Cup group letter) — domestic league
    fixtures don't have groups, just `matchday`, which was already being
    captured alongside it.
  - Writes to data/raw/{league_key}_fixtures.json instead of a single
    wc2026_fixtures.json, matching what predictions_engine.py's main()
    already expects.

This is a separate step from data/fetch_results.py (which pulls SETTLED
history for model training) because football-data.org's /matches endpoint
serves both with the same call, just filtered by `status`.
"""

import requests
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.leagues import (
    FD_BASE_URL, FOOTBALL_DATA_KEY, LEAGUES, TEAM_ALIASES,
    current_season_start_year,
)

FD_HEADERS = {"X-Auth-Token": FOOTBALL_DATA_KEY}


def normalize_team(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def fetch_league_fixtures(league_key: str) -> list:
    """Fetch upcoming (SCHEDULED/TIMED) fixtures for one league's current season."""
    cfg = LEAGUES[league_key]
    url = f"{FD_BASE_URL}/competitions/{cfg['fd_code']}/matches"
    params = {"season": current_season_start_year()}

    try:
        r = requests.get(url, headers=FD_HEADERS, params=params, timeout=15)
        r.raise_for_status()
        matches = r.json().get("matches", [])
        print(f"  ✓ {cfg['label']}: {len(matches)} matches fetched")
    except Exception as e:
        print(f"  ✗ {cfg['label']} fixtures fetch error: {e}")
        return []

    upcoming = []
    for m in matches:
        if m.get("status") not in ("SCHEDULED", "TIMED"):
            continue
        try:
            upcoming.append({
                "fixture_id": m.get("id"),
                "date":       m.get("utcDate", "")[:10],
                "time":       m.get("utcDate", "")[11:16],
                "competition": cfg["fd_code"],
                "matchday":   m.get("matchday"),
                "home_team":  normalize_team(m["homeTeam"]["name"]),
                "away_team":  normalize_team(m["awayTeam"]["name"]),
                "venue":      m.get("venue", ""),
                "status":     m.get("status"),
            })
        except Exception:
            pass

    return upcoming


def main(league_key: str = None):
    os.makedirs("data/raw", exist_ok=True)
    keys = [league_key] if league_key else list(LEAGUES.keys())

    all_fixtures = {}
    for lk in keys:
        cfg = LEAGUES[lk]
        print(f"\n=== Fetching {cfg['label']} Fixtures ===\n")
        fixtures = fetch_league_fixtures(lk)
        out_path = f"data/raw/{lk}_fixtures.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(fixtures, f, indent=2)
        print(f"  ✓ Saved {len(fixtures)} upcoming fixtures -> {out_path}")
        all_fixtures[lk] = fixtures

    return all_fixtures


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", choices=list(LEAGUES.keys()))
    args = parser.parse_args()
    result = main(args.league)
    total = sum(len(v) for v in result.values())
    print(f"\n✅ Done — {total} total upcoming fixtures across {len(result)} league(s)")

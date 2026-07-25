"""
config/leagues.py — Multi-league configuration
=================================================
Replaces the World Cup model's single hardcoded ODDS_SPORT / FD_COMPETITION_WC
constants with a list of league configs that run_daily.py loops over.

Each league gets its own:
  - Odds API sport key
  - football-data.org competition code
  - historical data files (so leagues never mix training data)
  - predictions output path (so each league gets its own predictions/latest.json)
  - form weighting (leagues barely need competition-tier weights like the WC
    did — instead they lean on time-decay, set below per league)

Secrets (API keys, GitHub token) still come from environment variables via
write_config.py, unchanged from the WC model. Only the per-league structural
stuff lives here.
"""

import os
import datetime


def current_season_start_year() -> int:
    """football-data.org identifies a season by its start year (2026 for the
    2026-27 season). Shared by data/fetch_fixtures.py, data/fetch_results.py,
    and tracking/results_tracker.py so this convention lives in exactly one
    place."""
    today = datetime.date.today()
    return today.year if today.month >= 7 else today.year - 1

# ---------------------------------------------------------------------------
# Shared secrets / infra — same pattern as the WC model's config.py
# ---------------------------------------------------------------------------
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY", "")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ben79sf-del/Leagues-Model")
GITHUB_BRANCH = "main"

ODDS_BASE_URL = "https://api.the-odds-api.com/v4"
FD_BASE_URL = "https://api.football-data.org/v4"
CLUBELO_BASE_URL = "http://api.clubelo.com"

ODDS_REGIONS = "us,uk,eu,au"
ODDS_MARKETS = "h2h,spreads,totals"
ODDS_BOOKMAKERS = "pinnacle,bet365,draftkings,fanduel,betmgm"

# ---------------------------------------------------------------------------
# Staking / edge thresholds — reused as-is from the WC model. These are
# bet-sizing rules, not football rules, so there's no reason to change them
# per league. Revisit only once you have a season of league-specific P&L data.
# ---------------------------------------------------------------------------
MIN_EDGE_PCT = 6.0
MAX_EDGE_PCT = 30.0
KELLY_FRACTION = 0.25
MAX_KELLY = 0.05
DAILY_STAKE_CAP = 20.0

# ---------------------------------------------------------------------------
# Shared team-name alias map — grows over time as odds feed / football-data.org
# naming mismatches turn up (this pattern is identical to WC's TEAM_ALIASES,
# just no longer country names — club nicknames/short-forms instead)
# ---------------------------------------------------------------------------
TEAM_ALIASES = {
    "Man United": "Manchester United",
    "Man City": "Manchester City",
    "Spurs": "Tottenham Hotspur",
    "Nottm Forest": "Nottingham Forest",
    "Wolves": "Wolverhampton Wanderers",
    "Leeds": "Leeds United",
    "Real Sociedad": "Real Sociedad",
    "Atletico Madrid": "Atlético Madrid",
    "Atl. Madrid": "Atlético Madrid",
    "Bayern": "Bayern Munich",
    "Dortmund": "Borussia Dortmund",
    "M'gladbach": "Borussia Mönchengladbach",
    "Leverkusen": "Bayer Leverkusen",
}

DATA_DIR = "data/raw"
PROCESSED_DIR = "data/processed"
PREDICTIONS_DIR = "predictions"
DASHBOARD_DIR = "dashboard"

# ---------------------------------------------------------------------------
# THE CORE CHANGE: one config block per league instead of one WC_GROUPS dict.
#
# odds_sport_key  -> The Odds API "sport" identifier
# fd_code         -> football-data.org competition code
# fd_seasons      -> how many past seasons of results to train on (leagues
#                    give you this for free — the WC model only had a handful
#                    of tournaments total)
# time_decay_days -> Dixon-Coles exponential time-weighting half-life. Shorter
#                    for leagues with more squad turnover / less predictable
#                    form (Championship), longer for stable top-flight sides.
# totals_lines    -> the Over/Under lines actually posted for this league,
#                    since this is exactly what patch_engine_v2.py was
#                    hacking around — different leagues get different lines
#                    quoted by books (Bundesliga skews higher-scoring than
#                    Ligue 1, for instance), so hardcode what to expect instead
#                    of discovering it via a monkey-patch later.
# ---------------------------------------------------------------------------
LEAGUES = {
    "epl": {
        "label": "Premier League",
        "odds_sport_key": "soccer_epl",
        "fd_code": "PL",
        "fd_seasons": 6,
        "time_decay_days": 260,
        "totals_lines": [1.5, 2.0, 2.5, 3.0, 3.5],
        "clubelo_country": "ENG",
        "clubelo_level": 1,
        "predictions_path": "predictions/epl/latest.json",
        "history_path": "predictions/epl/history.json",
        "history_dir": "predictions/epl/history",
        "results_log_path": "predictions/epl/results_log.json",
        "model_params_path": "models/params/epl_dixon_coles.json",
    },
    "championship": {
        "label": "EFL Championship",
        "odds_sport_key": "soccer_efl_champ",
        "fd_code": "ELC",
        "fd_seasons": 6,
        "time_decay_days": 200,  # more squad/form volatility -> shorter half-life
        "totals_lines": [1.5, 2.0, 2.5, 3.0],
        "clubelo_country": "ENG",
        "clubelo_level": 2,
        "predictions_path": "predictions/championship/latest.json",
        "history_path": "predictions/championship/history.json",
        "history_dir": "predictions/championship/history",
        "results_log_path": "predictions/championship/results_log.json",
        "model_params_path": "models/params/championship_dixon_coles.json",
    },
    "la_liga": {
        "label": "La Liga",
        "odds_sport_key": "soccer_spain_la_liga",
        "fd_code": "PD",
        "fd_seasons": 6,
        "time_decay_days": 280,
        "totals_lines": [1.5, 2.0, 2.5, 3.0],
        "clubelo_country": "ESP",
        "clubelo_level": 1,
        "predictions_path": "predictions/la_liga/latest.json",
        "history_path": "predictions/la_liga/history.json",
        "history_dir": "predictions/la_liga/history",
        "results_log_path": "predictions/la_liga/results_log.json",
        "model_params_path": "models/params/la_liga_dixon_coles.json",
    },
    "bundesliga": {
        "label": "Bundesliga",
        "odds_sport_key": "soccer_germany_bundesliga",
        "fd_code": "BL1",
        "fd_seasons": 6,
        "time_decay_days": 260,
        "totals_lines": [2.0, 2.5, 3.0, 3.5, 4.0],  # higher-scoring league on average
        "clubelo_country": "GER",
        "clubelo_level": 1,
        "predictions_path": "predictions/bundesliga/latest.json",
        "history_path": "predictions/bundesliga/history.json",
        "history_dir": "predictions/bundesliga/history",
        "results_log_path": "predictions/bundesliga/results_log.json",
        "model_params_path": "models/params/bundesliga_dixon_coles.json",
    },
}


def get_league(key: str) -> dict:
    """Fetch a single league's config block, with a clear error if the key is wrong."""
    if key not in LEAGUES:
        raise KeyError(
            f"Unknown league '{key}'. Valid keys: {', '.join(LEAGUES.keys())}"
        )
    return LEAGUES[key]

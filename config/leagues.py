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
  - clubelo country/level (for Elo ratings)

Secrets (API keys, GitHub token) come from environment variables, read
directly here — no separate config-writing step needed.
"""

import os
import re
import datetime


def current_season_start_year() -> int:
    """football-data.org identifies a season by its start year (2026 for the
    2026-27 season). Shared by data/fetch_fixtures.py, data/fetch_results.py,
    and tracking/results_tracker.py so this convention lives in exactly one
    place."""
    today = datetime.date.today()
    return today.year if today.month >= 7 else today.year - 1


# ---------------------------------------------------------------------------
# Shared secrets / infra
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
# Staking / edge thresholds — bet-sizing rules, not football rules, so no
# reason to vary per league.
# ---------------------------------------------------------------------------
MIN_EDGE_PCT = 6.0
MAX_EDGE_PCT = 30.0
KELLY_FRACTION = 0.25
MAX_KELLY = 0.05
DAILY_STAKE_CAP = 20.0

# ---------------------------------------------------------------------------
# Shared team-name alias map — grows over time as odds feed / football-data.org
# naming mismatches turn up.
# ---------------------------------------------------------------------------
TEAM_ALIASES = {
    # --- Premier League / Championship ---
    "Man United": "Manchester United",
    "Man City": "Manchester City",
    "Spurs": "Tottenham Hotspur",
    "Nottm Forest": "Nottingham Forest",
    "Wolves": "Wolverhampton Wanderers",
    "Leeds": "Leeds United",

    # --- Bundesliga: odds feeds use short/English names, football-data.org
    # uses full official names ("FC Bayern München", "1. FC Köln", etc.) ---
    "Bayern Munich": "FC Bayern München",
    "Bayern": "FC Bayern München",
    "Dortmund": "Borussia Dortmund",
    "Leverkusen": "Bayer 04 Leverkusen",
    "Bayer Leverkusen": "Bayer 04 Leverkusen",
    "Monchengladbach": "Borussia Mönchengladbach",
    "Gladbach": "Borussia Mönchengladbach",
    "Borussia M'gladbach": "Borussia Mönchengladbach",
    "M'gladbach": "Borussia Mönchengladbach",
    "FC Koln": "1. FC Köln",
    "Koln": "1. FC Köln",
    "Cologne": "1. FC Köln",
    "Mainz": "1. FSV Mainz 05",
    "Mainz 05": "1. FSV Mainz 05",
    "Hoffenheim": "TSG 1899 Hoffenheim",
    "Union Berlin": "1. FC Union Berlin",
    "Werder Bremen": "SV Werder Bremen",
    "Freiburg": "SC Freiburg",
    "Augsburg": "FC Augsburg",
    "Elversberg": "SV 07 Elversberg",
    "Paderborn": "SC Paderborn 07",
    "Schalke": "FC Schalke 04",
    "Schalke 04": "FC Schalke 04",
    "Hamburg": "Hamburger SV",
    "Hamburger SV": "Hamburger SV",

    # --- La Liga: odds feeds use common names, football-data.org uses
    # full club names ("Real Betis Balompié", "RCD Espanyol de Barcelona") ---
    "Real Madrid": "Real Madrid CF",
    "Barcelona": "FC Barcelona",
    "Atletico Madrid": "Club Atlético de Madrid",
    "Atl. Madrid": "Club Atlético de Madrid",
    "Atletico": "Club Atlético de Madrid",
    "Real Sociedad": "Real Sociedad de Fútbol",
    "Real Betis": "Real Betis Balompié",
    "Betis": "Real Betis Balompié",
    "Espanyol": "RCD Espanyol de Barcelona",
    "Celta Vigo": "RC Celta de Vigo",
    "Celta": "RC Celta de Vigo",
    "Osasuna": "CA Osasuna",
    "Rayo Vallecano": "Rayo Vallecano de Madrid",
    "Rayo": "Rayo Vallecano de Madrid",
    "Alaves": "Deportivo Alavés",
    "Deportivo Alaves": "Deportivo Alavés",
    "Athletic Bilbao": "Athletic Club",
    "Bilbao": "Athletic Club",
    "Malaga": "Málaga CF",
    "Deportivo La Coruna": "RC Deportivo La Coruña",
    "Deportivo": "RC Deportivo La Coruña",
    "Racing Santander": "Real Racing Club de Santander",
    "Racing": "Real Racing Club de Santander",
    "Elche": "Elche CF",
    "Villarreal": "Villarreal CF",
    "Valencia": "Valencia CF",
    "Sevilla": "Sevilla FC",
    "Getafe": "Getafe CF",
    "Levante": "Levante UD",
}

DATA_DIR = "data/raw"
PROCESSED_DIR = "data/processed"
PREDICTIONS_DIR = "predictions"
DASHBOARD_DIR = "dashboard"

# ---------------------------------------------------------------------------
# Match-key normalization — THE FIX
# ---------------------------------------------------------------------------
# football-data.org (fixtures/training data) names teams with club-type
# suffixes: "Watford FC", "1. FC Köln", "SC Paderborn 07". The Odds API
# (live odds) uses short public names: "Watford", "FC Koln", "Paderborn".
# Building a lookup key by simple string concatenation ("{home} vs {away}")
# meant these two sources almost never produced matching keys — which is
# why market_odds silently came back empty for nearly every fixture across
# every league, and only Borussia Dortmund vs Hamburger SV (spelled
# identically in both sources) ever got real value bets.
#
# normalize_for_match() strips club-type abbreviations and trailing
# founding-year digits (but not meaningful words, so it won't accidentally
# collapse two genuinely different clubs into the same key) before either
# side builds its lookup key. Used by both data/fetch_odds.py (building the
# odds dict's keys) and predictions/predictions_engine.py (looking a
# fixture's odds up by the same key), so they now actually agree.
_MATCH_STOPWORDS = {
    "fc", "afc", "cf", "sc", "sv", "fsv", "ud", "cd", "rc", "ac", "fk",
    "1", "07", "05", "04", "06", "03", "09", "98", "1899", "1900",
}


def normalize_for_match(name: str) -> str:
    """Reduce a club name to a bare comparable core for cross-source
    matching. Applies TEAM_ALIASES first, strips accents (odds feeds often
    spell "Köln" as "Koln" or "FC Koln" — without this, Bundesliga names
    with umlauts silently never matched either), then strips punctuation
    and club-type/founding-year tokens."""
    import unicodedata
    n = TEAM_ALIASES.get(name, name).lower()
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode("ascii")
    n = re.sub(r"[^\w\s]", " ", n)
    tokens = [t for t in n.split() if t not in _MATCH_STOPWORDS]
    return " ".join(tokens).strip()


# ---------------------------------------------------------------------------
# Per-league config
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

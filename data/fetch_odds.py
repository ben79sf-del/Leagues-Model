"""
fetch_odds.py — The Odds API → League Markets, per league
=============================================================
Fetches: Moneyline (h2h), Asian Handicap (spreads), Totals
Outputs: Structured JSON ready for model comparison

CHANGES FROM THE WC VERSION: every function that talked to a hardcoded
ODDS_SPORT ("soccer_fifa_world_cup") now takes a `sport_key` argument
instead, sourced from config/leagues.py's odds_sport_key per league
(soccer_epl, soccer_efl_champ, soccer_spain_la_liga,
soccer_germany_bundesliga). The odds-parsing functions themselves
(parse_h2h_odds, parse_totals_odds, parse_btts_odds, parse_spreads_odds)
are sport-agnostic and untouched — they just work on whatever match data
comes back.

FIX: match_key() now runs both team names through
config.leagues.normalize_for_match() before building the lookup key. The
Odds API uses short public names ("Watford") while football-data.org (the
side predictions_engine.py reads fixtures from) appends club-type suffixes
("Watford FC"). Building the key by simple string concatenation meant these
two sources almost never agreed, so market_odds silently came back empty
for nearly every fixture in every league — this normalizes both sides to
the same bare comparable core before matching.
"""

import requests
import json
import os
import sys
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.leagues import (
    ODDS_API_KEY, ODDS_BASE_URL, ODDS_REGIONS, ODDS_BOOKMAKERS, get_league,
    normalize_for_match,
)

# Priority bookmakers for line shopping (most sharp first)
SHARP_BOOKS  = ["pinnacle", "betfair_ex_eu", "sport888"]
PUBLIC_BOOKS = ["draftkings", "fanduel", "betmgm", "bet365", "williamhill", "unibet"]
ALL_BOOKS    = SHARP_BOOKS + PUBLIC_BOOKS

MARKETS = ["h2h", "spreads", "totals"]     # moneyline, asian handicap, over/under


def american_to_decimal(american) -> float:
    """Convert American odds to decimal."""
    if american >= 100:
        return round(american / 100 + 1, 4)
    else:
        return round(100 / abs(american) + 1, 4)


def decimal_to_implied_prob(decimal: float) -> float:
    """Convert decimal odds to implied probability."""
    if decimal <= 0:
        return 0.0
    return round(1 / decimal, 4)


def remove_vig(probs: list) -> list:
    """Remove bookmaker margin from implied probabilities."""
    total = sum(probs)
    if total == 0:
        return probs
    return [round(p / total, 4) for p in probs]


def fetch_events(sport_key: str) -> list:
    """Fetch all upcoming events for this league."""
    url = f"{ODDS_BASE_URL}/sports/{sport_key}/events"
    params = {"apiKey": ODDS_API_KEY, "regions": "us"}

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        events = r.json()
        print(f"  ✓ {len(events)} events found")
        return events
    except requests.exceptions.HTTPError as e:
        if r.status_code == 401:
            print("  ✗ Invalid API key — check ODDS_API_KEY")
        elif r.status_code == 422:
            print(f"  ✗ Sport not available: {sport_key}")
        else:
            print(f"  ✗ Events fetch error: {e}")
        return []
    except Exception as e:
        print(f"  ✗ Events fetch error: {e}")
        return []


def fetch_odds_for_sport(sport_key: str, market: str = "h2h", n_books: int = 5) -> list:
    """Fetch odds for all of this league's matches for a given market.

    n_books: how many bookmakers to query. BTTS coverage tends to be
    sparser than h2h/totals, so callers may pass a higher value for
    that market to improve the odds of finding a price.
    """
    url = f"{ODDS_BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    ODDS_REGIONS,
        "markets":    market,
        "oddsFormat": "american",
        "bookmakers": ",".join(ALL_BOOKS[:n_books]),   # Limit to save API quota
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        # Log remaining quota
        remaining = r.headers.get("x-requests-remaining", "?")
        used      = r.headers.get("x-requests-used", "?")
        print(f"  API quota: {remaining} remaining / {used} used")

        r.raise_for_status()
        odds_data = r.json()
        print(f"  ✓ Market '{market}': {len(odds_data)} matches")
        return odds_data
    except Exception as e:
        print(f"  ✗ Odds fetch [{market}]: {e}")
        return []


def parse_h2h_odds(match_data: dict) -> dict:
    """Parse moneyline (1X2) odds into clean structure."""
    home = match_data.get("home_team", "")
    away = match_data.get("away_team", "")

    books_parsed = {}

    for book in match_data.get("bookmakers", []):
        book_key = book.get("key", "")
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue

            outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}

            home_odds = outcomes.get(home, None)
            away_odds = outcomes.get(away, None)
            draw_odds = outcomes.get("Draw", None)

            if home_odds is None or away_odds is None:
                continue

            home_dec  = american_to_decimal(home_odds)
            away_dec  = american_to_decimal(away_odds)
            draw_dec  = american_to_decimal(draw_odds) if draw_odds else None

            raw_probs = [decimal_to_implied_prob(home_dec), decimal_to_implied_prob(away_dec)]
            if draw_dec:
                raw_probs.append(decimal_to_implied_prob(draw_dec))

            vig_removed = remove_vig(raw_probs)

            books_parsed[book_key] = {
                "home_american": home_odds,
                "away_american": away_odds,
                "draw_american": draw_odds,
                "home_decimal":  home_dec,
                "away_decimal":  away_dec,
                "draw_decimal":  draw_dec,
                "home_implied":  vig_removed[0],
                "away_implied":  vig_removed[1],
                "draw_implied":  vig_removed[2] if draw_dec else None,
            }

    # Consensus: average across sharp books first, then all
    def consensus(books_parsed: dict, prob_key: str):
        sharp_vals = [v[prob_key] for k, v in books_parsed.items()
                      if k in SHARP_BOOKS and v.get(prob_key) is not None]
        all_vals   = [v[prob_key] for v in books_parsed.values()
                      if v.get(prob_key) is not None]
        vals = sharp_vals if sharp_vals else all_vals
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "home_team":     home,
        "away_team":     away,
        "books":         books_parsed,
        "consensus": {
            "home_implied": consensus(books_parsed, "home_implied"),
            "draw_implied": consensus(books_parsed, "draw_implied"),
            "away_implied": consensus(books_parsed, "away_implied"),
        },
        "best_home_odds": max((v["home_decimal"] for v in books_parsed.values()), default=None),
        "best_away_odds": max((v["away_decimal"] for v in books_parsed.values()), default=None),
        "best_draw_odds": max((v.get("draw_decimal", 0) or 0 for v in books_parsed.values()), default=None),
    }


def parse_totals_odds(match_data: dict) -> dict:
    """Parse over/under goals odds — works for whatever lines the league's
    bookmakers actually post (2.5 for a low-scoring league, up to 4.0+ for
    a high-scoring one like the Bundesliga), not a fixed 2.5."""
    home = match_data.get("home_team", "")
    away = match_data.get("away_team", "")

    lines = {}

    for book in match_data.get("bookmakers", []):
        book_key = book.get("key", "")
        for market in book.get("markets", []):
            if market.get("key") != "totals":
                continue
            for outcome in market.get("outcomes", []):
                line  = outcome.get("point", 2.5)
                side  = outcome.get("name", "")    # "Over" or "Under"
                price = outcome.get("price", 0)

                line_key = f"{line}_{side.lower()}"
                if line_key not in lines:
                    lines[line_key] = {}
                lines[line_key][book_key] = {
                    "american": price,
                    "decimal":  american_to_decimal(price),
                    "implied":  decimal_to_implied_prob(american_to_decimal(price)),
                }

    # Find consensus for each line
    parsed_lines = {}
    for line_key, book_data in lines.items():
        avg_implied = sum(v["implied"] for v in book_data.values()) / len(book_data)
        best_odds   = max(v["decimal"] for v in book_data.values())
        parsed_lines[line_key] = {
            "avg_implied": round(avg_implied, 4),
            "best_decimal": round(best_odds, 4),
            "n_books": len(book_data),
        }

    return {
        "home_team": home,
        "away_team": away,
        "totals":    parsed_lines,
    }


def parse_btts_odds(match_data: dict) -> dict:
    """Parse Both Teams To Score (BTTS) odds.

    Handles both American odds (from bulk endpoint) and decimal odds
    (from the per-event endpoint which returns decimal format).
    """
    home = match_data.get("home_team", "")
    away = match_data.get("away_team", "")

    yes_books = {}
    no_books  = {}

    for book in match_data.get("bookmakers", []):
        book_key = book.get("key", "")
        for market in book.get("markets", []):
            if market.get("key") not in ("btts", "both_teams_to_score"):
                continue
            for outcome in market.get("outcomes", []):
                side  = outcome.get("name", "").lower()
                price = outcome.get("price", 0)

                # Per-event endpoint returns decimal; bulk returns American
                # Decimal odds are always > 1.0 and typically < 20 for BTTS
                # American odds for BTTS Yes are usually -120 to +120 range
                if isinstance(price, float) and 1.0 < price < 30:
                    dec = round(price, 4)   # already decimal
                else:
                    dec = american_to_decimal(price)

                entry = {
                    "decimal": dec,
                    "implied": decimal_to_implied_prob(dec),
                }
                if side == "yes":
                    yes_books[book_key] = entry
                elif side == "no":
                    no_books[book_key] = entry

    result = {"home_team": home, "away_team": away, "btts": {}}

    if yes_books:
        result["btts"]["btts_yes"] = {
            "avg_implied":  round(sum(v["implied"] for v in yes_books.values()) / len(yes_books), 4),
            "best_decimal": round(max(v["decimal"] for v in yes_books.values()), 4),
            "n_books": len(yes_books),
        }
    if no_books:
        result["btts"]["btts_no"] = {
            "avg_implied":  round(sum(v["implied"] for v in no_books.values()) / len(no_books), 4),
            "best_decimal": round(max(v["decimal"] for v in no_books.values()), 4),
            "n_books": len(no_books),
        }

    return result


def parse_spreads_odds(match_data: dict) -> dict:
    """Parse Asian Handicap / spread odds."""
    home = match_data.get("home_team", "")
    away = match_data.get("away_team", "")

    lines = {}

    for book in match_data.get("bookmakers", []):
        book_key = book.get("key", "")
        for market in book.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                team  = outcome.get("name", "")
                point = outcome.get("point", 0)
                price = outcome.get("price", 0)

                line_key = f"{team}_{point:+.1f}"
                if line_key not in lines:
                    lines[line_key] = {"team": team, "handicap": point, "books": {}}
                lines[line_key]["books"][book_key] = {
                    "american": price,
                    "decimal":  american_to_decimal(price),
                    "implied":  decimal_to_implied_prob(american_to_decimal(price)),
                }

    for lk in lines:
        book_data = lines[lk]["books"]
        if book_data:
            lines[lk]["avg_implied"] = round(
                sum(v["implied"] for v in book_data.values()) / len(book_data), 4
            )
            lines[lk]["best_decimal"] = round(
                max(v["decimal"] for v in book_data.values()), 4
            )

    return {
        "home_team": home,
        "away_team": away,
        "spreads":   {k: {kk: vv for kk, vv in v.items() if kk != "books"}
                      for k, v in lines.items()},
    }


def fetch_btts_for_event(sport_key: str, event_id: str, home: str, away: str) -> dict:
    """
    Fetch BTTS odds for a single event via the per-event endpoint.
    The bulk /odds endpoint returns 422 for btts on most soccer sport keys —
    this market must be fetched per-event using /events/{eventId}/odds.
    """
    url = f"{ODDS_BASE_URL}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    "uk,eu,au",   # UK/EU books have best BTTS coverage
        "markets":    "btts",
        "oddsFormat": "decimal",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        # parse_btts_odds expects a dict with a "bookmakers" key
        return parse_btts_odds(data)
    except Exception:
        return {"home_team": home, "away_team": away, "btts": {}}


def build_full_odds(league_key: str) -> dict:
    """Fetch and combine all markets into unified structure per match,
    for one league."""
    cfg = get_league(league_key)
    sport_key = cfg["odds_sport_key"]
    print(f"\n=== Fetching Odds: {cfg['label']} ===\n")

    h2h_data     = fetch_odds_for_sport(sport_key, "h2h")
    totals_data  = fetch_odds_for_sport(sport_key, "totals")
    spreads_data = fetch_odds_for_sport(sport_key, "spreads")

    # BTTS must be fetched per-event (bulk /odds endpoint returns 422 for btts)
    # First get the event IDs, then fetch BTTS for each upcoming event
    events = fetch_events(sport_key)
    event_id_map = {
        f"{e['home_team']} vs {e['away_team']}": e["id"]
        for e in events if "id" in e
    }
    print(f"  Fetching BTTS per-event for {len(event_id_map)} matches...")

    # FIX: normalize both sides of the key so football-data.org fixture
    # names ("Watford FC") and Odds API names ("Watford") actually collide
    # on the same key instead of silently never matching.
    def match_key(h, a):
        return f"{normalize_for_match(h)} vs {normalize_for_match(a)}"

    totals_idx  = {match_key(m["home_team"], m["away_team"]): m for m in totals_data}
    spreads_idx = {match_key(m["home_team"], m["away_team"]): m for m in spreads_data}

    combined = {}
    btts_found = 0

    for match in h2h_data:
        h = match["home_team"]
        a = match["away_team"]
        k = match_key(h, a)

        h2h_parsed     = parse_h2h_odds(match)
        totals_parsed  = parse_totals_odds(totals_idx.get(k, {"home_team": h, "away_team": a, "bookmakers": []}))
        spreads_parsed = parse_spreads_odds(spreads_idx.get(k, {"home_team": h, "away_team": a, "bookmakers": []}))

        # Fetch BTTS via per-event endpoint if we have an event ID.
        # event_id_map is keyed by the RAW (un-normalized) Odds API name
        # pairing since that's what fetch_events() returns — this lookup
        # is separate from the normalized match_key used for the combined
        # dict below.
        raw_key = f"{h} vs {a}"
        event_id = event_id_map.get(raw_key)
        if event_id:
            btts_parsed = fetch_btts_for_event(sport_key, event_id, h, a)
            if btts_parsed.get("btts"):
                btts_found += 1
        else:
            btts_parsed = {"home_team": h, "away_team": a, "btts": {}}

        # Merge BTTS into totals dict so downstream lookups work unchanged
        totals_parsed["totals"].update(btts_parsed.get("btts", {}))

        combined[k] = {
            "home_team":     h,
            "away_team":     a,
            "commence_time": match.get("commence_time", ""),
            "sport":         match.get("sport_title", cfg["label"]),
            "h2h":           h2h_parsed,
            "totals":        totals_parsed,
            "spreads":       spreads_parsed,
            "fetched_at":    datetime.utcnow().isoformat(),
        }

    print(f"  ✓ BTTS odds found for {btts_found}/{len(h2h_data)} matches")
    return combined


def main(league_key: str):
    os.makedirs("data/raw", exist_ok=True)
    cfg = get_league(league_key)

    odds = build_full_odds(league_key)

    out_path = f"data/raw/{league_key}_odds_latest.json"
    with open(out_path, "w") as f:
        json.dump(odds, f, indent=2)

    print(f"\n✓ Saved odds for {len(odds)} matches -> {out_path}")

    # Preview
    for k, v in list(odds.items())[:3]:
        con = v["h2h"].get("consensus", {})
        home_p = con.get("home_implied")
        draw_p = con.get("draw_implied")
        away_p = con.get("away_implied")
        print(f"\n  {k}")
        print(f"    Home: {home_p:.1%}  Draw: {draw_p:.1%}  Away: {away_p:.1%}"
              if home_p and draw_p and away_p else "    (incomplete consensus)")

    return odds


if __name__ == "__main__":
    import argparse
    from config.leagues import LEAGUES
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", choices=list(LEAGUES.keys()), required=True)
    args = parser.parse_args()
    odds = main(args.league)
    print(f"\n✅ Odds fetch complete — {len(odds)} matches")

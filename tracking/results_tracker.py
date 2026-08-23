"""
tracking/results_tracker.py — Track Model Accuracy vs Real Outcomes, per league
==================================================================================
Checks football-data.org for newly FINISHED matches, matches them against the
prediction snapshot made for them, scores accuracy + value-bet P&L, and pushes
the results log to GitHub for the dashboard.
"""

import requests
import json
import os
import re
import sys
import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.leagues import (
    FD_BASE_URL, FOOTBALL_DATA_KEY, TEAM_ALIASES, get_league,
    season_start_year_for,
)

FD_HEADERS = {"X-Auth-Token": FOOTBALL_DATA_KEY}


def normalize_team(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def reference_totals_line(cfg: dict) -> float:
    """Pick a single 'canonical' totals line per league for the model's own
    Over/Under accuracy stat (separate from any specific bet's line)."""
    lines = cfg.get("totals_lines", [2.5])
    if 2.5 in lines:
        return 2.5
    return sorted(lines)[len(lines) // 2]


def fetch_finished_matches(league_key: str) -> list:
    """Fetch all FINISHED matches for this league's current season."""
    cfg = get_league(league_key)
    url = f"{FD_BASE_URL}/competitions/{cfg['fd_code']}/matches"
    params = {"season": season_start_year_for(league_key), "status": "FINISHED"}

    try:
        r = requests.get(url, headers=FD_HEADERS, params=params, timeout=15)
        r.raise_for_status()
        matches = r.json().get("matches", [])
        print(f"  ✓ {len(matches)} finished {cfg['label']} matches this season")
        return matches
    except Exception as e:
        print(f"  ✗ Fetch failed for {cfg['label']}: {e}")
        return []


def load_results_log(results_log_path: str) -> dict:
    if os.path.exists(results_log_path):
        with open(results_log_path) as f:
            return json.load(f)
    return {
        "last_updated": None,
        "matches_scored": [],
        "results": [],
        "summary": {},
    }


def find_prediction_for_fixture(fixture_id: int, history_dir: str) -> dict | None:
    """Search history snapshots for the prediction made for this fixture.
    Uses the MOST RECENT snapshot before the match (closest to kickoff =
    most informed prediction)."""
    if not os.path.exists(history_dir):
        return None

    candidates = []
    for fname in sorted(os.listdir(history_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(history_dir, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
            for pred in data.get("predictions", []):
                meta = pred.get("match_meta", {})
                if meta.get("fixture_id") == fixture_id:
                    candidates.append((fname, pred))
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def score_1x2(pred: dict, actual_result: str) -> dict:
    """Did the model pick the right outcome? Plus Brier score (lower=better)."""
    model = pred.get("model", {})
    ph = model.get("p_home_win", 0.333)
    pd_ = model.get("p_draw", 0.333)
    pa = model.get("p_away_win", 0.333)

    probs = {"H": ph, "D": pd_, "A": pa}
    predicted = max(probs, key=probs.get)
    correct = (predicted == actual_result)

    actual_vec = {"H": 1 if actual_result == "H" else 0,
                  "D": 1 if actual_result == "D" else 0,
                  "A": 1 if actual_result == "A" else 0}
    brier = sum((probs[k] - actual_vec[k]) ** 2 for k in probs)

    return {
        "predicted_outcome": predicted,
        "actual_outcome": actual_result,
        "correct": correct,
        "brier_score": round(brier, 4),
        "model_probs": {"home": round(ph, 4), "draw": round(pd_, 4), "away": round(pa, 4)},
    }


def score_totals(pred: dict, total_goals: int, cfg: dict) -> dict:
    """Score the model's Over/Under call against this league's reference line."""
    line = reference_totals_line(cfg)
    model = pred.get("model", {})
    p_over = model.get("p_over_2_5", 0.5)
    predicted = "Over" if p_over >= 0.5 else "Under"
    actual = "Over" if total_goals > line else "Under"
    return {
        "line": line,
        "predicted": predicted,
        "actual": actual,
        "correct": predicted == actual,
        "p_over": round(p_over, 4),
        "actual_total_goals": total_goals,
    }


def score_btts(pred: dict, home_goals: int, away_goals: int) -> dict:
    model = pred.get("model", {})
    p_btts = model.get("p_btts", 0.5)
    predicted = "Yes" if p_btts >= 0.5 else "No"
    actual = "Yes" if (home_goals > 0 and away_goals > 0) else "No"
    return {
        "predicted": predicted,
        "actual": actual,
        "correct": predicted == actual,
        "p_btts": round(p_btts, 4),
    }


_LINE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _parse_market_line(market: str, default: float = 2.5) -> float:
    m = _LINE_RE.search(market)
    return float(m.group(1)) if m else default


def score_value_bets(pred: dict, actual_result: str, total_goals: int,
                      home_goals: int, away_goals: int) -> list:
    """For each value bet flagged, determine if it won and its P&L in units."""
    scored_bets = []

    for bet in pred.get("value_bets", []):
        market = bet["market"]
        odds = bet.get("best_odds", 0)
        won = False

        if market == "Home Win":
            won = (actual_result == "H")
        elif market == "Away Win":
            won = (actual_result == "A")
        elif market == "Draw":
            won = (actual_result == "D")
        elif market.startswith("Over"):
            line = _parse_market_line(market)
            won = (total_goals > line)
        elif market.startswith("Under"):
            line = _parse_market_line(market)
            won = (total_goals <= line)
        elif market == "BTTS Yes":
            won = (home_goals > 0 and away_goals > 0)
        elif market == "BTTS No":
            won = not (home_goals > 0 and away_goals > 0)
        else:
            continue

        pnl = round(odds - 1, 4) if won else -1.0

        scored_bets.append({
            "market": market,
            "odds": odds,
            "edge_pct": bet.get("edge_pct"),
            "rating": bet.get("rating"),
            "won": won,
            "pnl_units": pnl,
        })

    return scored_bets


def process_finished_matches(league_key: str) -> dict:
    cfg = get_league(league_key)
    log = load_results_log(cfg["results_log_path"])
    already_scored = set(log["matches_scored"])

    finished = fetch_finished_matches(league_key)
    new_results = []

    for match in finished:
        fixture_id = match.get("id")
        if fixture_id in already_scored:
            continue

        score = match.get("score", {}).get("fullTime", {})
        home_goals = score.get("home")
        away_goals = score.get("away")
        if home_goals is None or away_goals is None:
            continue

        home_team = normalize_team(match["homeTeam"]["name"])
        away_team = normalize_team(match["awayTeam"]["name"])

        if home_goals > away_goals:
            actual_result = "H"
        elif home_goals < away_goals:
            actual_result = "A"
        else:
            actual_result = "D"

        total_goals = home_goals + away_goals

        pred = find_prediction_for_fixture(fixture_id, cfg["history_dir"])
        if pred is None:
            print(f"  ⚠ No prediction found for {home_team} vs {away_team} "
                  f"(fixture {fixture_id}) — skipping")
            continue

        result_record = {
            "fixture_id": fixture_id,
            "date": match.get("utcDate", "")[:10],
            "home_team": home_team,
            "away_team": away_team,
            "score": f"{home_goals}-{away_goals}",
            "matchday": match.get("matchday"),
            "scored_1x2": score_1x2(pred, actual_result),
            "scored_totals": score_totals(pred, total_goals, cfg),
            "scored_btts": score_btts(pred, home_goals, away_goals),
            "value_bets_results": score_value_bets(
                pred, actual_result, total_goals, home_goals, away_goals
            ),
            "scored_at": datetime.datetime.utcnow().isoformat(),
        }

        new_results.append(result_record)
        already_scored.add(fixture_id)

        outcome_str = "✓" if result_record["scored_1x2"]["correct"] else "✗"
        print(f"  {outcome_str} {home_team} {home_goals}-{away_goals} {away_team} "
              f"(predicted {result_record['scored_1x2']['predicted_outcome']}, "
              f"actual {actual_result})")

    if new_results:
        log["results"].extend(new_results)
        log["matches_scored"] = sorted(already_scored)
        log["last_updated"] = datetime.datetime.utcnow().isoformat()
        log["summary"] = compute_summary(log["results"])
        print(f"\n  ✓ {len(new_results)} new match(es) scored")
    else:
        print("\n  No new finished matches to score")
        log["summary"] = compute_summary(log["results"])

    return log


def compute_summary(results: list) -> dict:
    if not results:
        return {
            "total_matches": 0,
            "1x2_accuracy": None,
            "avg_brier_score": None,
            "totals_accuracy": None,
            "btts_accuracy": None,
            "value_bets": {},
        }

    n = len(results)
    correct_1x2 = sum(1 for r in results if r["scored_1x2"]["correct"])
    avg_brier = sum(r["scored_1x2"]["brier_score"] for r in results) / n
    correct_totals = sum(1 for r in results if r["scored_totals"]["correct"])
    correct_btts = sum(1 for r in results if r["scored_btts"]["correct"])

    all_bets = []
    for r in results:
        all_bets.extend(r.get("value_bets_results", []))

    vb_summary = {}
    if all_bets:
        total_bets = len(all_bets)
        wins = sum(1 for b in all_bets if b["won"])
        total_pnl = sum(b["pnl_units"] for b in all_bets)

        by_rating = {}
        for rating in ["🔥 STRONG", "✅ GOOD", "👀 WATCH"]:
            rating_bets = [b for b in all_bets if b.get("rating") == rating]
            if rating_bets:
                r_wins = sum(1 for b in rating_bets if b["won"])
                r_pnl = sum(b["pnl_units"] for b in rating_bets)
                by_rating[rating] = {
                    "n_bets": len(rating_bets),
                    "wins": r_wins,
                    "win_rate": round(r_wins / len(rating_bets), 4),
                    "pnl_units": round(r_pnl, 2),
                    "roi_pct": round(r_pnl / len(rating_bets) * 100, 2),
                }

        vb_summary = {
            "total_bets": total_bets,
            "wins": wins,
            "win_rate": round(wins / total_bets, 4),
            "total_pnl_units": round(total_pnl, 2),
            "roi_pct": round(total_pnl / total_bets * 100, 2),
            "by_rating": by_rating,
        }

    by_matchday = {}
    for r in results:
        md = r.get("matchday") or "Unknown"
        if md not in by_matchday:
            by_matchday[md] = {"n": 0, "correct": 0}
        by_matchday[md]["n"] += 1
        if r["scored_1x2"]["correct"]:
            by_matchday[md]["correct"] += 1

    return {
        "total_matches": n,
        "1x2_correct": correct_1x2,
        "1x2_accuracy": round(correct_1x2 / n, 4),
        "avg_brier_score": round(avg_brier, 4),
        "totals_correct": correct_totals,
        "totals_accuracy": round(correct_totals / n, 4),
        "btts_correct": correct_btts,
        "btts_accuracy": round(correct_btts / n, 4),
        "value_bets": vb_summary,
        "by_matchday": by_matchday,
        "last_5_results": [
            {
                "match": f"{r['home_team']} {r['score']} {r['away_team']}",
                "predicted": r["scored_1x2"]["predicted_outcome"],
                "actual": r["scored_1x2"]["actual_outcome"],
                "correct": r["scored_1x2"]["correct"],
            }
            for r in results[-5:]
        ],
    }


def main(league_key: str):
    cfg = get_league(league_key)
    os.makedirs(os.path.dirname(cfg["results_log_path"]), exist_ok=True)

    print(f"\n=== Results Tracker: {cfg['label']} ===\n")

    log = process_finished_matches(league_key)

    with open(cfg["results_log_path"], "w") as f:
        json.dump(log, f, indent=2)

    print(f"\n  ✓ Saved -> {cfg['results_log_path']}")

    summary = log["summary"]
    if summary.get("total_matches", 0) > 0:
        print(f"\n{'='*50}")
        print(f"  📊 {cfg['label'].upper()} PERFORMANCE — {summary['total_matches']} matches")
        print(f"{'='*50}")
        print(f"  1X2 Accuracy:    {summary['1x2_accuracy']:.1%} "
              f"({summary['1x2_correct']}/{summary['total_matches']})")
        print(f"  Brier Score:     {summary['avg_brier_score']:.4f} (lower = better, 0.667 = baseline)")
        print(f"  Totals Accuracy: {summary['totals_accuracy']:.1%}")
        print(f"  BTTS Accuracy:   {summary['btts_accuracy']:.1%}")

        vb = summary.get("value_bets", {})
        if vb:
            print(f"\n  💰 Value Bet Performance:")
            print(f"  Total bets:  {vb['total_bets']}")
            print(f"  Win rate:    {vb['win_rate']:.1%}")
            print(f"  Total P&L:   {vb['total_pnl_units']:+.2f} units")
            print(f"  ROI:         {vb['roi_pct']:+.1f}%")
            for rating, stats in vb.get("by_rating", {}).items():
                print(f"\n  {rating}: {stats['n_bets']} bets, "
                      f"{stats['win_rate']:.1%} win rate, "
                      f"{stats['roi_pct']:+.1f}% ROI")
    else:
        print("\n  No matches scored yet — check back after fixtures finish")

    return log


if __name__ == "__main__":
    import argparse
    from config.leagues import LEAGUES
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", choices=list(LEAGUES.keys()), required=True)
    args = parser.parse_args()
    main(args.league)
    print("\n✅ Results tracking complete")

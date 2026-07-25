"""
run_daily.py — Multi-League Daily Prediction Pipeline
========================================================
Same pipeline shape as the World Cup model's run_daily.py, but each step now
loops over LEAGUES (config/leagues.py) instead of running once for a single
tournament:

  1. Fetch historical results        -> per league (data/fetch_results.py)
  2. Update Elo ratings               -> per league
  3. Train / refresh Dixon-Coles      -> per league (models/dixon_coles_model.py)
  4. Fetch upcoming fixtures          -> per league (data/fetch_fixtures.py)
  5. Fetch live odds                  -> per league (different odds_sport_key)
  6. Generate predictions             -> per league, own predictions_path
  7. Push to GitHub                   -> per league, separate JSON files
  8. Track settled results            -> per league

DROPPED FROM THE WC VERSION: "Step 4b: Patch Fixtures From Odds" — that step
existed only because football-data.org's knockout bracket lagged reality
during the tournament. League fixtures are known a season in advance, so
there's nothing to patch; this whole ~80-line function is deleted rather
than adapted. A plain "fetch this season's SCHEDULED/TIMED matches" step
(4, above) replaces it — adapted directly from the WC model's
fetch_wc2026_fixtures(), which did the same football-data.org call, just
for one hardcoded tournament instead of 4 leagues.

Usage (same flags as before, now applying across all 4 leagues):
    python run_daily.py                  # Full run, all leagues
    python run_daily.py --league epl      # Just one league
    python run_daily.py --odds-only
    python run_daily.py --train
"""

import argparse
import os
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.leagues import LEAGUES, ODDS_API_KEY, FOOTBALL_DATA_KEY, GITHUB_TOKEN


def check_config():
    errors = []
    if not ODDS_API_KEY:
        errors.append("ODDS_API_KEY not set")
    if not FOOTBALL_DATA_KEY:
        errors.append("FOOTBALL_DATA_KEY not set")
    if not GITHUB_TOKEN:
        errors.append("GITHUB_TOKEN not set (predictions won't be pushed)")
    if errors:
        print("⚠ Configuration warnings:")
        for e in errors:
            print(f"  - {e}")
        print()
    # Missing GitHub token shouldn't block a local dry run; missing data keys should.
    return len([e for e in errors if "GITHUB" not in e]) == 0


def step_fetch_results(force: bool = False):
    print("  Fetching historical results (all leagues)...")
    from data.fetch_results import main as fetch_results
    try:
        fetch_results()
        return True
    except Exception as e:
        print(f"  ✗ Results fetch failed: {e}")
        return False


def step_fetch_elo(force: bool = False):
    print("  Fetching Elo ratings (all leagues)...")
    try:
        from data.fetch_elo import main as fetch_elo
        fetch_elo()
        return True
    except Exception as e:
        print(f"  ✗ Elo fetch failed: {e}")
        return False


def step_train_models(force: bool = False):
    print("  Training Dixon-Coles models (per league)...")
    from models.dixon_coles_model import train_all
    try:
        train_all(force=force)
        return True
    except Exception as e:
        print(f"  ✗ Model training failed: {e}")
        return False


def step_fetch_fixtures(league_keys):
    print("  Fetching upcoming fixtures (per league)...")
    from data.fetch_fixtures import fetch_league_fixtures
    import json
    try:
        for lk in league_keys:
            fixtures = fetch_league_fixtures(lk)
            with open(f"data/raw/{lk}_fixtures.json", "w", encoding="utf-8") as f:
                json.dump(fixtures, f, indent=2)
            time.sleep(1)
        return True
    except Exception as e:
        print(f"  ✗ Fixtures fetch failed: {e}")
        return False


def step_fetch_odds(league_keys):
    print("  Fetching live odds (per league)...")
    try:
        from data.fetch_odds import main as fetch_odds
        for lk in league_keys:
            fetch_odds(lk)
            time.sleep(1)
        return True
    except Exception as e:
        print(f"  ✗ Odds fetch failed: {e}")
        return False


def step_generate_predictions(league_keys):
    print("  Generating predictions (per league)...")
    from predictions.predictions_engine import main as run_predictions
    outputs = {}
    for lk in league_keys:
        try:
            outputs[lk] = run_predictions(lk)
            print(f"    ✓ {LEAGUES[lk]['label']}: "
                  f"{len(outputs[lk].get('predictions', []))} fixtures predicted")
        except Exception as e:
            print(f"    ✗ {LEAGUES[lk]['label']} prediction failed: {e}")
            import traceback
            traceback.print_exc()
    return outputs


def step_push_to_github(outputs: dict):
    print("  Pushing to GitHub (per league)...")
    from predictions.github_storage import push_predictions
    for lk, output in outputs.items():
        if not output:
            continue
        try:
            push_predictions(output, path=LEAGUES[lk]["predictions_path"])
        except Exception as e:
            print(f"    ✗ {LEAGUES[lk]['label']} push failed: {e}")


def step_track_results(league_keys, push: bool = True):
    print("  Checking settled matches (per league)...")
    from tracking.results_tracker import main as run_tracker
    from predictions.github_storage import push_results_log
    for lk in league_keys:
        try:
            log = run_tracker(lk)
            if push:
                push_results_log(log, path=LEAGUES[lk]["results_log_path"])
        except Exception as e:
            print(f"    ✗ {LEAGUES[lk]['label']} tracking failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Multi-League Prediction Pipeline")
    parser.add_argument("--league", choices=list(LEAGUES.keys()),
                         help="Run for a single league instead of all four")
    parser.add_argument("--odds-only", action="store_true")
    parser.add_argument("--predict", action="store_true")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    league_keys = [args.league] if args.league else list(LEAGUES.keys())

    print(f"\n{'='*60}")
    print(f" ⚽ League Model — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f" Leagues: {', '.join(LEAGUES[k]['label'] for k in league_keys)}")
    print(f"{'='*60}\n")

    if not check_config():
        print("✗ Missing required API keys. Exiting.")
        return

    os.makedirs("data/raw", exist_ok=True)
    os.makedirs("data/processed", exist_ok=True)
    os.makedirs("models/params", exist_ok=True)
    for lk in league_keys:
        os.makedirs(os.path.dirname(LEAGUES[lk]["predictions_path"]), exist_ok=True)

    if args.odds_only:
        print("── Step: Refresh Odds Only ──\n")
        step_fetch_odds(league_keys)
        return

    if not args.predict:
        print("── Step 1: Historical Results ──\n")
        step_fetch_results(force=args.force)
        time.sleep(1)

        print("\n── Step 2: Elo Ratings ──\n")
        step_fetch_elo(force=args.force)
        time.sleep(1)

        print("\n── Step 3: Model Training ──\n")
        step_train_models(force=args.train)
        time.sleep(1)

        print("\n── Step 4: Upcoming Fixtures ──\n")
        step_fetch_fixtures(league_keys)
        time.sleep(1)

        print("\n── Step 5: Live Odds ──\n")
        step_fetch_odds(league_keys)
        time.sleep(1)

    print("\n── Step 6: Predictions ──\n")
    outputs = step_generate_predictions(league_keys)

    if not args.no_push:
        print("\n── Step 7: GitHub Push ──\n")
        step_push_to_github(outputs)

    print("\n── Step 8: Results Tracking ──\n")
    step_track_results(league_keys, push=not args.no_push)

    total_preds = sum(len(o.get("predictions", [])) for o in outputs.values() if o)
    print(f"\n✅ Pipeline complete! {total_preds} total fixtures predicted "
          f"across {len(league_keys)} league(s).\n")


if __name__ == "__main__":
    main()

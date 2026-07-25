"""
github_storage.py — Push predictions to GitHub, per league
==============================================================
Same push_file()/get_file_sha() mechanics as your WC model — those were
already generic (take a `path` argument), so they're untouched below.

CHANGES FROM THE WC VERSION: push_predictions() and push_results_log() now
take a `path` argument instead of a hardcoded "predictions/latest.json" /
"predictions/results_log.json", matching how run_daily.py and
results_tracker.py already call them:

    push_predictions(output, path=LEAGUES[lk]["predictions_path"])
    push_results_log(log, path=LEAGUES[lk]["results_log_path"])

Everything derived from that path (the dated history archive, the
value-bets-today summary) is built relative to the same directory, so each
league's predictions/epl/, predictions/championship/, etc. stay fully
separate in the repo. `group` (World Cup group letter) is replaced with
`matchday` in the value-bets summary, matching the rest of the rebuild.
"""

import json
import base64
import requests
import os
import sys
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.leagues import GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH, LEAGUES


GITHUB_API = "https://api.github.com"
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept":        "application/vnd.github.v3+json",
    "Content-Type":  "application/json",
}


def get_file_sha(path: str):
    """Get current SHA of a file (needed for updates)."""
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    params = {"ref": GITHUB_BRANCH}
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if r.status_code == 200:
        return r.json().get("sha")
    return None


def push_file(path: str, content, message: str = None) -> bool:
    """Push JSON content to GitHub repo."""
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"

    content_str    = json.dumps(content, indent=2)
    content_b64    = base64.b64encode(content_str.encode()).decode()
    commit_message = message or f"Update {path} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"

    sha = get_file_sha(path)

    payload = {
        "message": commit_message,
        "content": content_b64,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(url, headers=HEADERS, json=payload, timeout=30)
        r.raise_for_status()
        action = "Updated" if sha else "Created"
        print(f"  ✓ GitHub: {action} {path}")
        return True
    except Exception as e:
        print(f"  ✗ GitHub push failed for {path}: {e}")
        return False


def _league_label_for_path(path: str) -> str:
    """Best-effort: derive a human label for commit messages from a
    predictions_path like 'predictions/epl/latest.json' -> 'Premier League'.
    Falls back to the raw directory name if it doesn't match a known league
    (e.g. if you point this at a custom path)."""
    parts = path.split("/")
    if len(parts) >= 2:
        league_key = parts[-2]
        if league_key in LEAGUES:
            return LEAGUES[league_key]["label"]
        return league_key
    return "League"


def push_predictions(predictions_output: dict, path: str) -> bool:
    """Push latest predictions + append to history, for one league.

    `path` is that league's predictions_path (e.g.
    "predictions/epl/latest.json"); the dated history archive and
    value-bets summary are written alongside it in the same directory.
    """
    timestamp = datetime.utcnow().strftime("%Y-%m-%d")
    base_dir = os.path.dirname(path) or "predictions"
    label = _league_label_for_path(path)

    # Push latest
    success = push_file(
        path,
        predictions_output,
        f"{label} predictions update — {timestamp}"
    )

    # Append to daily history (GitHub-side archive — separate from the local
    # predictions/{league}/history/ snapshots predictions_engine.py writes
    # for the results tracker to read locally)
    history_path = f"{base_dir}/history/{timestamp}.json"
    push_file(history_path, predictions_output, f"{label} daily archive — {timestamp}")

    # Update value_bets summary (easy reading)
    all_bets = []
    for pred in predictions_output.get("predictions", []):
        for bet in pred.get("value_bets", []):
            all_bets.append({
                "match":        f"{pred['home_team']} vs {pred['away_team']}",
                "date":         pred.get("match_meta", {}).get("date", ""),
                "matchday":     pred.get("match_meta", {}).get("matchday", ""),
                "market":       bet["market"],
                "edge_pct":     bet["edge_pct"],
                "model_prob":   bet["model_prob"],
                "market_prob":  bet["market_prob"],
                "best_odds":    bet["best_odds"],
                "kelly_pct":    bet["kelly_pct"],
                "rating":       bet["rating"],
            })

    all_bets.sort(key=lambda x: -x["edge_pct"])

    push_file(
        f"{base_dir}/value_bets_today.json",
        {
            "date":       timestamp,
            "league":     label,
            "generated":  predictions_output.get("generated_at"),
            "count":      len(all_bets),
            "value_bets": all_bets,
        },
        f"{label} value bets — {timestamp}"
    )

    return success


def push_results_log(results_log: dict, path: str) -> bool:
    """Push results tracking log to GitHub. `path` is that league's
    results_log_path (e.g. "predictions/epl/results_log.json")."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d")
    label = _league_label_for_path(path)
    return push_file(
        path,
        results_log,
        f"{label} results tracking update — {timestamp}"
    )


def fetch_predictions_from_github(path: str) -> dict:
    """Pull latest predictions back from GitHub (for the dashboard, or for
    debugging). `path` is that league's predictions_path."""
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    params = {"ref": GITHUB_BRANCH}

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        content_b64 = r.json().get("content", "")
        content_str = base64.b64decode(content_b64).decode()
        return json.loads(content_str)
    except Exception as e:
        print(f"  ✗ Failed to fetch from GitHub ({path}): {e}")
        return {}


if __name__ == "__main__":
    # Test connection
    print("Testing GitHub connection...")
    sha = get_file_sha("README.md")
    if sha:
        print(f"  ✓ Connected to {GITHUB_REPO}")
    else:
        print(f"  ✗ Could not connect — check GH_TOKEN and GITHUB_REPO")

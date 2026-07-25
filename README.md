# ⚽ Multi-League Prediction Model — EPL, Championship, La Liga, Bundesliga

Rebuild of `worldcup-model` for domestic league play. Same Dixon-Coles core,
same GitHub-storage + dashboard architecture — restructured to run 4
independent leagues instead of 1 tournament.

## What's in this scaffold (built)

| File | Status |
|---|---|
| `config/leagues.py` | **New.** Replaces the single `config/config.py` with one config block per league (odds sport key, football-data.org code, seasons of history, time-decay half-life, totals lines). |
| `models/dixon_coles_model.py` | **Rebuilt.** Same DC algorithm (attack/defense ratings, rho low-score correction, MLE fit), now trains one independent model per league from that league's own multi-season history, weighted by recency instead of competition tier. |
| `data/fetch_results.py` | **Rebuilt.** Pulls N seasons per league from football-data.org's competition endpoints (`PL`, `ELC`, `PD`, `BL1`) instead of the WC's one-off tournament + qualifiers dataset. |
| `run_daily.py` | **Rebuilt.** Same 7-step pipeline shape, now loops over all 4 leagues. The WC version's "Step 4b: Patch Fixtures From Odds" is **deleted** — that logic existed only to patch a lagging knockout bracket, which doesn't exist in a round-robin season. |

## Also rebuilt, from your actual source this time

You sent over `results_tracker.py`, `daily_predictions.yml`, and `index.html`,
so these are edited directly on your real code rather than guessed at:

- **`tracking/results_tracker.py`** — takes a `--league` flag, reads
  `fd_code`/`history_dir`/`results_log_path` from `config/leagues.py` per
  league. World Cup groups → football-data.org matchday numbers for the
  breakdown. Totals scoring against a per-league reference line instead of
  a hardcoded 2.5 — Bundesliga matches average higher-scoring, so its
  reference line is picked from `totals_lines` accordingly. Value-bet
  scoring now parses the actual line out of the bet's market string
  (`"Over 3.5"` → 3.5) instead of assuming every totals bet was quoted at
  2.5 — same class of bug `patch_engine_v2.py` was patching on the odds
  side, fixed properly here on the scoring side.
- **`.github/workflows/daily_predictions.yml`** — simplified: since
  `config/leagues.py` reads secrets straight from `os.environ`, the whole
  "write config.py from secrets via inline Python" step is gone. Supports
  running all 4 leagues on schedule, or a single league via manual dispatch.
- **`dashboard/index.html`** — added a league switcher (4 buttons, one per
  `predictions/{league}/latest.json`), replaced World Cup groups/Elo-by-group
  with a "Matchday" badge on fixture cards, keyed the value-bet tracker's
  localStorage separately per league (so an EPL bet never shows up mixed
  into your Bundesliga ledger), and updated the Model Info panel copy to
  describe per-league training instead of tournament-tier weighting. Your
  actual CSS, card layouts, filters, and Export/Import tracker logic are
  untouched.

## Also rebuilt this pass, from your real `predictions_engine.py`

- **`models/dixon_coles_model.py`** — added the `DixonColesModel` class your
  `predictions_engine.py` actually expects (`.load()`, `.predict_match(home,
  away, neutral, elo_ratings)` returning lambda/1X2/totals/BTTS/top
  scorelines/Asian handicap). My first pass had a plain function that didn't
  match this interface at all — that's fixed now, and tested end-to-end.
  `neutral` now defaults to **halving** home advantage rather than the WC's
  neutral-venue assumption, since domestic fixtures are (almost) always at a
  real home ground. Totals probabilities are generated for every standard
  line (1.5–4.0), named `p_over_2_5`-style, matching what
  `predictions_engine.py` already parses for — so no per-league special
  casing is needed anywhere.
- **`predictions/predictions_engine.py`** — edited your real file directly.
  Changes: `PredictionEngine(model, league_key, elo_ratings)` now carries a
  league config; `neutral=False` by default; `main(league_key)` loads that
  league's own model/fixtures/odds/output paths from `config/leagues.py`.
  The important fix is in `_compare_markets()` and `_find_value_bets()`:
  totals matching now loops over **whatever lines the bookmaker actually
  quoted** for that match and looks up the correspondingly-named model
  probability (`p_over_3_5`, `p_over_1_5`, etc.) instead of assuming 2.5 is
  always the line — I tested this specifically with a synthetic
  "3.5-only" odds card and confirmed it matches the right model probability
  rather than silently defaulting to the 2.5 number. This is the proper fix
  for the class of bug `patch_engine_v2.py` was patching around on the WC
  model, now handled generically rather than with another one-off patch.

I ran this full chain end-to-end on synthetic data (fit a model → fixtures →
odds → `predictions_engine.main()`) to confirm it actually produces correct
edge/Kelly numbers before handing it over, not just that it imports cleanly.

One design note flagged in the code: `apply_daily_stake_cap()` still caps
per league, per day — with 4 leagues all posting Saturday fixtures, that's
effectively a per-league cap, not one shared $20/day cap across all of them.
If you want one true combined daily cap, that needs to move out of
`predictions_engine.py` and into `run_daily.py` after all 4 leagues'
predictions are generated. Said which behavior you want and I'll wire it up.

## Also rebuilt this pass, from your real `fetch_odds.py` and `fetch_results.py`

- **`data/fetch_fixtures.py`** — new file, adapted directly from the
  `fetch_wc2026_fixtures()` function that was living inside your real
  `fetch_results.py`. Same football-data.org call, same
  SCHEDULED/TIMED status filter — just parameterized per league (season =
  the current league season via `config.leagues.current_season_start_year()`,
  not a hardcoded 2026) and with the WC's `group` field dropped (no groups
  in league play — `matchday` was already being captured alongside it and
  does the same job). Wired into `run_daily.py` as a new Step 4. Tested with
  a mocked football-data.org response — confirmed it correctly keeps only
  SCHEDULED/TIMED matches and drops FINISHED ones.
- **`data/fetch_odds.py`** — edited your real file directly. Every function
  that talked to a hardcoded `ODDS_SPORT` now takes a `sport_key` argument
  sourced from `config/leagues.py`'s `odds_sport_key` per league
  (`soccer_epl`, `soccer_efl_champ`, `soccer_spain_la_liga`,
  `soccer_germany_bundesliga`). The parsing functions themselves
  (`parse_h2h_odds`, `parse_totals_odds`, `parse_btts_odds`,
  `parse_spreads_odds`) are sport-agnostic and completely untouched — the
  WC model already built these correctly for any market shape. Tested with
  mocked API responses for a non-WC sport key end-to-end through
  `build_full_odds()`, confirming the consensus/edge numbers come out right
  and a non-2.5 totals line (tested at 3.5) parses cleanly.
- **`data/fetch_results.py`** — your real version pulls from the martj42
  international-results dataset, which only covers international football,
  not club leagues — confirming the football-data.org multi-season approach
  I built earlier was the right call for this rebuild rather than something
  to reconcile with martj42. One thing your version does that mine doesn't
  (yet): `fetch_team_recent_form()` builds a separate recency-weighted
  attack/defense form rating per team, independent of the Dixon-Coles fit.
  I left this out since the model's own fitted attack/defense ratings
  already capture similar signal and I didn't want to duplicate blind — say
  the word if you want an equivalent per-league form file alongside the
  model fit.
- Consolidated the "current season start year" calculation (previously
  copy-pasted in 2 places) into one `current_season_start_year()` helper in
  `config/leagues.py`, used by `fetch_fixtures.py`, `fetch_results.py`, and
  `results_tracker.py`.

I ran the odds fetcher and fixtures fetcher against mocked network responses
(not just import-checked) to confirm the league parameterization actually
works before handing this over.

## Also rebuilt this pass, from your real `github_storage.py`

- **`predictions/github_storage.py`** — edited your real file directly.
  `push_file()`/`get_file_sha()` were already fully generic (take a `path`
  argument), so those are untouched. `push_predictions()` and
  `push_results_log()` now take a `path` argument instead of hardcoded
  `predictions/latest.json` / `predictions/results_log.json` — matching
  exactly how `run_daily.py` already calls them
  (`push_predictions(output, path=LEAGUES[lk]["predictions_path"])`). The
  dated history archive and value-bets-today summary are derived from that
  same path's directory, so each league's `predictions/epl/`,
  `predictions/bundesliga/`, etc. stay fully separate in the repo. `group`
  in the value-bets summary → `matchday`. Tested with mocked GitHub API
  calls — confirmed the right league label shows up in commit messages and
  all three files (`latest.json`, dated history, `value_bets_today.json`)
  land at the correct per-league paths.
- **Found and fixed a real bug while wiring this up**: `run_daily.py`'s
  `step_track_results()` was pushing the results log to `history_path`
  instead of `results_log_path` — a mismatch that would have silently
  overwritten the wrong file every day. Fixed now that the two paths are
  both actually in use by real code instead of just sitting in config.

## Also rebuilt this pass, from your real `fetch_elo.py`

- **`data/fetch_elo.py`** — this one isn't an edit of your file so much as a
  full replacement, because the WC version's approach (compute Elo from
  scratch off martj42's international match history, with its own
  World-Cup/Qualifier/Friendly K-factor system and a hardcoded
  `BASELINE_ELO` dict of ~65 national teams) has no club-football
  equivalent — there's no "friendly" vs "World Cup" distinction for a club
  side, and martj42 doesn't cover club fixtures at all.

  The fix: your original `config/config.py` (and my `config/leagues.py`)
  already defined `CLUBELO_BASE_URL = "http://api.clubelo.com"` — it just
  was never actually called anywhere in the WC pipeline, since club Elo
  isn't a thing for national teams. clubelo.com maintains real,
  continuously-updated Elo for essentially every professional club
  worldwide, so the new version just pulls it directly instead of computing
  its own from raw results: one CSV snapshot request
  (`http://api.clubelo.com/{date}`), filtered per league by a new
  `clubelo_country`/`clubelo_level` pair I added to each league's config
  (`ENG`/1 for the Premier League, `ENG`/2 for the Championship, `ESP`/1 for
  La Liga, `GER`/1 for the Bundesliga). One snapshot request covers all 4
  leagues. Output path matches exactly what `predictions_engine.py` already
  looks for (`data/processed/{league_key}_elo_ratings.json`).

  Tested with a mocked 6-club CSV snapshot spanning all 4 leagues — confirmed
  it correctly filters by country/level, applies `TEAM_ALIASES` normalization
  (e.g. `Bayern` → `Bayern Munich`), and writes to the right per-league
  paths.

## Pipeline status: complete

Every function `run_daily.py` calls now exists, has a tested (not just
imported) implementation, and its signature has been checked against every
caller: `fetch_results.py` → `fetch_elo.py` → `dixon_coles_model.py` →
`fetch_fixtures.py` → `fetch_odds.py` → `predictions_engine.py` →
`github_storage.py` → `results_tracker.py`. `python run_daily.py --force
--train` should now run start to finish for any/all 4 leagues, given real
API keys and a real GitHub repo.

What I still haven't been able to do: run the *actual* live APIs together
end-to-end (only mocked/synthetic data, since I don't have network access to
football-data.org, The Odds API, or clubelo.com from here) — so the first
real run is genuinely the first time all of this touches live data at once.
Treat step 4 in the earlier walkthrough (run one league locally first) as
non-optional.

## Not started yet — lineup/player data

This is genuinely new territory (the WC model never touched squads). Once
the pipeline above is running cleanly, the natural next step is a
`data/fetch_lineups.py` that pulls confirmed/predicted XIs (football-data.org
doesn't reliably have this — you'd want a source like an official league API
or a lineup-confirmation service) and a small pre-match adjustment step
between "Dixon-Coles rating" and "final lambda" that nudges team strength
down for missing key players. Worth doing as its own pass once the core
4-league pipeline has a few weeks of live data to validate against.

## Setup

Same pattern as before — `write_config.py` isn't needed anymore since
`config/leagues.py` reads env vars directly:

```bash
pip install -r requirements.txt
export ODDS_API_KEY="..."
export FOOTBALL_DATA_KEY="..."
export GH_TOKEN="..."
export GITHUB_REPO="yourusername/leagues-model"

python run_daily.py --force --train        # first-time full setup, all 4 leagues
python run_daily.py                        # daily run, all leagues
python run_daily.py --league epl           # just one league
python run_daily.py --odds-only            # quick odds refresh
```

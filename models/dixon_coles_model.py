"""
models/dixon_coles_model.py — Dixon-Coles Poisson model, per league
=====================================================================
Same core algorithm as the WC model (Dixon & Coles, 1997):

    lambda_home = attack_home * defense_away * mu * exp(home_adv)
    lambda_away = attack_away * defense_home * mu

...with the low-score correlation correction (rho) for 0-0/1-0/0-1/1-1,
and exponential time-decay weighting so recent matches count more.

THE CHANGE FROM THE WC MODEL: instead of one global fit across a handful of
international tournaments (weighted by competition tier — WC=1.0,
Friendly=0.3), this fits ONE INDEPENDENT MODEL PER LEAGUE, using that
league's own multi-season match history, weighted purely by recency
(time_decay_days from config/leagues.py). Competition-tier weighting made
sense when "World Cup vs. friendly" was the main signal quality gap; in a
domestic league, nearly every match is at the same competitive intensity, so
recency is what matters.

Fits are cached to models/params/{league}_dixon_coles.json (see
model_params_path in config/leagues.py) so run_daily.py can skip refitting
on every run and only retrain periodically (e.g. weekly) or with --train.
"""

import json
import os
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

from config.leagues import LEAGUES, get_league


def time_weight(match_date: pd.Timestamp, ref_date: pd.Timestamp, decay_days: float) -> float:
    """Exponential decay weight — a match `decay_days` ago carries weight 1/e."""
    days_ago = (ref_date - match_date).days
    days_ago = max(days_ago, 0)
    return float(np.exp(-days_ago / decay_days))


def _dc_correction(x: int, y: int, lambda_home: float, lambda_away: float, rho: float) -> float:
    """Dixon-Coles low-score adjustment factor (tau)."""
    if x == 0 and y == 0:
        return 1 - lambda_home * lambda_away * rho
    elif x == 0 and y == 1:
        return 1 + lambda_home * rho
    elif x == 1 and y == 0:
        return 1 + lambda_away * rho
    elif x == 1 and y == 1:
        return 1 - rho
    return 1.0


def _neg_log_likelihood(params, teams, matches, ref_date, decay_days):
    n = len(teams)
    attack = dict(zip(teams, params[:n]))
    defense = dict(zip(teams, params[n:2 * n]))
    home_adv, rho, mu = params[2 * n], params[2 * n + 1], params[2 * n + 2]

    ll = 0.0
    for m in matches:
        lh = attack[m.home_team] * defense[m.away_team] * mu * np.exp(home_adv)
        la = attack[m.away_team] * defense[m.home_team] * mu

        w = time_weight(m.date, ref_date, decay_days)

        p_home = poisson.pmf(m.home_goals, lh)
        p_away = poisson.pmf(m.away_goals, la)
        tau = _dc_correction(m.home_goals, m.away_goals, lh, la, rho)
        tau = max(tau, 1e-10)  # guard against negative/zero from bad rho during search

        ll += w * (np.log(p_home) + np.log(p_away) + np.log(tau))

    return -ll


def fit_league(league_key: str, matches_df: pd.DataFrame, ref_date=None) -> dict:
    """
    Fit a Dixon-Coles model for one league.

    matches_df columns required: date, home_team, away_team, home_goals, away_goals
    Returns a dict of fitted parameters, ready to json.dump to model_params_path.
    """
    cfg = get_league(league_key)
    decay_days = cfg["time_decay_days"]

    if ref_date is None:
        ref_date = pd.Timestamp.utcnow().tz_localize(None)

    teams = sorted(set(matches_df["home_team"]) | set(matches_df["away_team"]))
    n = len(teams)
    matches = list(matches_df.itertuples(index=False))

    # Initial guesses: attack=defense=1 for all teams, home_adv~0.25 (typical),
    # rho~-0.1 (typical negative correlation), mu = league-wide average goals/team/game
    total_goals = matches_df["home_goals"].sum() + matches_df["away_goals"].sum()
    total_team_matches = 2 * len(matches_df)
    mu_init = total_goals / total_team_matches if total_team_matches else 1.3

    x0 = np.concatenate([
        np.ones(n),        # attack
        np.ones(n),        # defense
        [0.25, -0.1, mu_init],  # home_adv, rho, mu
    ])

    # Constrain average attack rating to 1.0 to keep the model identifiable
    # (otherwise attack/defense can drift arbitrarily while their product
    # stays fixed)
    constraints = [{
        "type": "eq",
        "fun": lambda p, n=n: np.mean(p[:n]) - 1.0,
    }]

    bounds = (
        [(0.05, 5.0)] * n +      # attack
        [(0.05, 5.0)] * n +      # defense
        [(-1.0, 1.0), (-0.3, 0.3), (0.3, 3.0)]  # home_adv, rho, mu
    )

    result = minimize(
        _neg_log_likelihood,
        x0,
        args=(teams, matches, ref_date, decay_days),
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 300, "ftol": 1e-8},
    )

    if not result.success:
        print(f"  ⚠ {league_key}: optimizer did not fully converge ({result.message}); "
              f"using best-found parameters anyway.")

    params = result.x
    attack = dict(zip(teams, params[:n]))
    defense = dict(zip(teams, params[n:2 * n]))
    home_adv, rho, mu = params[2 * n], params[2 * n + 1], params[2 * n + 2]

    return {
        "league": league_key,
        "label": cfg["label"],
        "fitted_at": pd.Timestamp.utcnow().isoformat(),
        "n_matches": len(matches_df),
        "decay_days": decay_days,
        "home_adv": float(home_adv),
        "rho": float(rho),
        "mu": float(mu),
        "teams": {
            t: {"attack": float(attack[t]), "defense": float(defense[t])}
            for t in teams
        },
    }


class DixonColesModel:
    """
    Wraps a fitted params dict (from fit_league / train_all) and exposes the
    rich prediction interface predictions_engine.py expects:

        model = DixonColesModel().load("models/params/epl_dixon_coles.json")
        pred  = model.predict_match(home, away, neutral=False, elo_ratings=elo)

    predict_match() returns everything predictions_engine.py reads off the
    result: lambda_home/away, full 1X2, a family of Over/Under lines (not
    just 2.5/3.5 — every league's totals_lines get a matching p_over_X key,
    using the same "p_over_2_5"-style naming predictions_engine.py already
    parses for), BTTS, most-likely score, top scorelines, and a basic Asian
    Handicap probability table.
    """

    def __init__(self):
        self.params = None
        self.teams = {}
        self.league = None
        self.label = None

    def load(self, path: str) -> "DixonColesModel":
        with open(path, "r", encoding="utf-8") as f:
            self.params = json.load(f)
        self.teams = self.params.get("teams", {})
        self.league = self.params.get("league")
        self.label = self.params.get("label")
        self._fallback = self._compute_fallback_rating()
        return self

    def _compute_fallback_rating(self) -> dict:
        """Rating to use for a team with zero matches in the fitted history —
        almost always a team newly promoted/relegated into this league since
        the training window closed (a real case hit in production: Coventry
        City, Wolves, Real Racing Club de Santander, and SC Paderborn 07 all
        failed this way on the first live run, each one newly in their
        league this season).

        Using the league AVERAGE would overrate a promoted side — they're
        typically weaker than a side that's survived in this division.
        Instead this uses the bottom-quartile attack rating and top-quartile
        defense rating (defense is parameterized so HIGHER = leakier) among
        already-fitted teams: a deliberately below-average "likely relegation
        candidate" prior, which is a more honest starting point than average
        until the team accumulates its own in-league results.
        """
        if not self.teams:
            return {"attack": 0.85, "defense": 1.15}

        attacks = sorted(v["attack"] for v in self.teams.values())
        defenses = sorted(v["defense"] for v in self.teams.values())
        n = len(attacks)

        def pct(sorted_vals, p):
            idx = min(max(int(round(p * (n - 1))), 0), n - 1)
            return sorted_vals[idx]

        return {
            "attack": pct(attacks, 0.25),
            "defense": pct(defenses, 0.75),
        }

    @staticmethod
    def _line_key(prefix: str, line: float) -> str:
        """'p_over_2_5' style key — matches the naming predictions_engine.py's
        _compare_markets() already builds when parsing bookmaker line strings,
        so arbitrary per-league totals_lines just work without special-casing
        2.5/3.5 the way the WC version did."""
        return f"{prefix}_{str(line).replace('.', '_')}"

    def predict_match(self, home_team: str, away_team: str,
                       neutral: bool = False, elo_ratings: dict = None) -> dict:
        missing = [t for t in (home_team, away_team) if t not in self.teams]
        if missing:
            fallback = getattr(self, "_fallback", None) or self._compute_fallback_rating()
            print(f"  ⚠ {', '.join(missing)} not in fitted model (likely newly "
                  f"promoted/relegated) — using a below-average fallback rating "
                  f"instead of skipping this fixture.")

        home_rating = self.teams.get(home_team, fallback if missing else None)
        away_rating = self.teams.get(away_team, fallback if missing else None)

        a_h, d_h = home_rating["attack"], home_rating["defense"]
        a_a, d_a = away_rating["attack"], away_rating["defense"]
        mu, home_adv, rho = self.params["mu"], self.params["home_adv"], self.params["rho"]

        # Optional light Elo cross-check: nudges attack ratings toward
        # relative Elo strength when both teams are rated. A soft sanity
        # check on the model's own fitted ratings, not a replacement for
        # them — capped at +/-15% either way so a stale/thin Elo feed can't
        # dominate a well-fitted model.
        if elo_ratings and home_team in elo_ratings and away_team in elo_ratings:
            elo_diff = (elo_ratings[home_team] - elo_ratings[away_team]) / 400.0
            elo_adj = 1 + max(-0.15, min(0.15, elo_diff * 0.05))
            a_h_eff, a_a_eff = a_h * elo_adj, a_a / elo_adj
        else:
            a_h_eff, a_a_eff = a_h, a_a

        # Domestic league fixtures are almost never at a neutral venue
        # (unlike WC matches), so `neutral` defaults to False upstream in
        # predictions_engine.py. Kept configurable here for cup competitions
        # played at neutral grounds (finals, etc.) — home advantage is halved
        # rather than zeroed, since even "neutral" venues rarely offer zero
        # net advantage to either side in practice.
        home_adv_factor = np.exp(home_adv / 2) if neutral else np.exp(home_adv)

        lambda_home = a_h_eff * d_a * mu * home_adv_factor
        lambda_away = a_a_eff * d_h * mu

        max_goals = 8
        matrix = np.zeros((max_goals, max_goals))
        for x in range(max_goals):
            for y in range(max_goals):
                p = poisson.pmf(x, lambda_home) * poisson.pmf(y, lambda_away)
                p *= _dc_correction(x, y, lambda_home, lambda_away, rho)
                matrix[x, y] = max(p, 0)
        matrix /= matrix.sum()

        p_home_win = float(np.tril(matrix, -1).sum())
        p_draw = float(np.trace(matrix))
        p_away_win = float(np.triu(matrix, 1).sum())

        # Over/Under across every standard line, not just 2.5/3.5 — covers
        # whatever's in this league's config/leagues.py totals_lines
        # (Bundesliga needs 4.0, Championship rarely needs 3.5+, etc.)
        totals_result = {}
        for line in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
            over_mask = np.array([[1 if (x + y) > line else 0
                                    for y in range(max_goals)] for x in range(max_goals)])
            p_over = float((matrix * over_mask).sum())
            totals_result[self._line_key("p_over", line)] = round(p_over, 4)
            totals_result[self._line_key("p_under", line)] = round(1 - p_over, 4)

        # BTTS
        btts_mask = np.array([[1 if x >= 1 and y >= 1 else 0
                                for y in range(max_goals)] for x in range(max_goals)])
        p_btts = float((matrix * btts_mask).sum())

        # Most likely scoreline + top 6
        flat = [((x, y), matrix[x, y]) for x in range(max_goals) for y in range(max_goals)]
        flat.sort(key=lambda t: -t[1])
        most_likely_score = f"{flat[0][0][0]}-{flat[0][0][1]}"
        top_scorelines = [
            {"score": f"{s[0]}-{s[1]}", "probability": round(p, 4)}
            for (s, p) in flat[:6]
        ]

        # Basic Asian Handicap: probability home "covers" each standard line,
        # accounting for pushes on integer/half-integer split lines.
        asian_handicap = {}
        for hcap in (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0):
            home_cover, push, away_cover = 0.0, 0.0, 0.0
            for x in range(max_goals):
                for y in range(max_goals):
                    margin = (x - y) + hcap
                    p = matrix[x, y]
                    if margin > 0:
                        home_cover += p
                    elif margin == 0:
                        push += p
                    else:
                        away_cover += p
            asian_handicap[str(hcap)] = {
                "home_covers": round(home_cover, 4),
                "push": round(push, 4),
                "away_covers": round(away_cover, 4),
            }

        result = {
            "data_source": "dixon_coles",
            "lambda_home": round(float(lambda_home), 4),
            "lambda_away": round(float(lambda_away), 4),
            "expected_total": round(float(lambda_home + lambda_away), 4),
            "p_home_win": round(p_home_win, 4),
            "p_draw": round(p_draw, 4),
            "p_away_win": round(p_away_win, 4),
            "p_btts": round(p_btts, 4),
            "p_no_btts": round(1 - p_btts, 4),
            "most_likely_score": most_likely_score,
            "top_scorelines": top_scorelines,
            "asian_handicap": asian_handicap,
            # Populated when either team had no in-league history and got the
            # below-average fallback rating instead of a real fit — a signal
            # to treat this prediction's edge with extra caution (a newly
            # promoted side's true strength is genuinely unknown pre-season).
            "provisional_teams": missing,
        }
        result.update(totals_result)  # adds p_over_1_5 ... p_under_4_0
        return result


def train_all(force: bool = False):
    """Train (or skip-if-cached) a model for every league in config/leagues.py."""
    for league_key, cfg in LEAGUES.items():
        params_path = cfg["model_params_path"]
        if not force and os.path.exists(params_path):
            print(f"  ✓ {cfg['label']}: cached params exist (use --train to refit)")
            continue

        hist_path = f"data/processed/{league_key}_matches.csv"
        if not os.path.exists(hist_path):
            print(f"  ✗ {cfg['label']}: no processed history at {hist_path} — "
                  f"run data/fetch_results.py first")
            continue

        print(f"  Training {cfg['label']} ({cfg['fd_seasons']} seasons of history)...")
        df = pd.read_csv(hist_path, parse_dates=["date"])
        params = fit_league(league_key, df)

        os.makedirs(os.path.dirname(params_path), exist_ok=True)
        with open(params_path, "w", encoding="utf-8") as f:
            json.dump(params, f, indent=2)
        print(f"  ✓ {cfg['label']}: fitted on {len(df)} matches "
              f"(home_adv={params['home_adv']:.3f}, rho={params['rho']:.3f})")


if __name__ == "__main__":
    train_all(force=True)

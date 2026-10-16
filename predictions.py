"""Prediction journal: a true forward test of the bot's predictions.

Every run, for each market, it writes down two predictions about the next 5 trading days:
  * the ML model's probability that the price will be higher (gradient-boosted trees,
    retrained weekly on all history up to that day), and
  * the trend signals' call (3–4 of 4 trend speeds up = "up", 0–1 = "down", 2 = no call).
Five trading days later it checks what really happened and scores each prediction.

Nothing here trades. It only records and scores, so we can see, month by month and on data
the model has never seen, whether the predictions are better than a coin flip.

    python predictions.py           # print the scorecard from output/predictions.csv

Files: output/predictions.csv (one row per market per day), output/models/ (cached models).
"""
import os, pickle
from datetime import datetime
import numpy as np
import pandas as pd

OUT = "output"
JOURNAL = os.path.join(OUT, "predictions.csv")
MODELS = os.path.join(OUT, "models")
HORIZON = 5            # trading days ahead
RETRAIN_DAYS = 7       # retrain each market's model at most once a week
MIN_HISTORY = 400      # bars needed before a market gets an ML prediction


def _read():
    try:
        return pd.read_csv(JOURNAL, parse_dates=["signal_date"]) if os.path.exists(JOURNAL) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _model_for(sym, df):
    """Load this market's cached model, or retrain it if it is missing or over a week old.
    Trained only on rows whose 5-day outcome is already known (no look-ahead)."""
    import ml_model
    from sklearn.ensemble import HistGradientBoostingClassifier
    os.makedirs(MODELS, exist_ok=True)
    path = os.path.join(MODELS, "".join(ch if ch.isalnum() else "_" for ch in sym) + ".pkl")
    last = df.index[-1]
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                saved = pickle.load(f)
            if (last - saved["trained_through"]).days < RETRAIN_DAYS:
                return saved
        except Exception:
            pass
    X, y = ml_model.make_features(df), ml_model.make_label(df, HORIZON)
    ok = X.notna().all(axis=1) & y.notna()
    if ok.sum() < MIN_HISTORY - HORIZON or y[ok].nunique() < 2:
        return None
    model = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                           l2_regularization=1.0, min_samples_leaf=50, random_state=0)
    model.fit(X[ok], y[ok])
    saved = {"model": model, "trained_through": X[ok].index[-1], "columns": list(X.columns)}
    with open(path, "wb") as f:
        pickle.dump(saved, f)
    return saved


def record(frames, sig, std_name=None):
    """Write today's predictions and score any that have matured.
    frames: {symbol: OHLCV DataFrame of COMPLETED daily bars}; sig: {symbol: {"votes_up": n, ...}}."""
    import ml_model
    std_name = std_name or (lambda s: s)
    old = _read()
    done = set(zip(old["signal_date"].dt.normalize(), old["symbol"])) if not old.empty else set()
    rows = []
    for sym, df in frames.items():
        if df is None or len(df) < MIN_HISTORY:
            continue
        day = df.index[-1].normalize()
        if (day, sym) in done:
            continue                                   # already recorded (e.g. a second preview today)
        prob, trained = np.nan, ""
        try:
            m = _model_for(sym, df)
            if m is not None:
                X = ml_model.make_features(df)[m["columns"]].iloc[[-1]]
                if X.notna().all(axis=1).iloc[0]:
                    prob = float(m["model"].predict_proba(X)[0, 1])
                    trained = str(pd.Timestamp(m["trained_through"]).date())
        except Exception as e:
            print(f"  (no ML prediction for {sym}: {e})")
        votes = sig.get(sym, {}).get("votes_up")
        rows.append({"signal_date": day, "symbol": sym, "market": std_name(sym), "horizon": HORIZON,
                     "close": float(df["Close"].iloc[-1]), "ml_prob_up": prob,
                     "trend_votes_up": votes, "model_trained_through": trained,
                     "recorded_at": datetime.now().isoformat(timespec="seconds"),
                     "outcome_date": pd.NaT, "future_return": np.nan, "actual_up": np.nan,
                     "ml_correct": np.nan, "trend_call": _trend_call(votes), "trend_correct": np.nan})
    journal = pd.concat([old, pd.DataFrame(rows)], ignore_index=True) if rows else old
    if journal.empty:
        return journal
    journal = score(journal, frames)
    os.makedirs(OUT, exist_ok=True)
    journal.to_csv(JOURNAL, index=False)
    n_new = len(rows)
    n_scored = int(journal["actual_up"].notna().sum())
    print(f"Prediction journal: {n_new} new predictions recorded, {n_scored} scored so far (output/predictions.csv).")
    return journal


def _trend_call(votes):
    if votes is None or (isinstance(votes, float) and np.isnan(votes)):
        return ""
    return "up" if votes >= 3 else "down" if votes <= 1 else "none"


def score(journal, frames):
    """Fill in the outcome of every prediction whose 5 trading days have passed."""
    j = journal.copy()
    j["signal_date"] = pd.to_datetime(j["signal_date"])
    for col in ("outcome_date",):
        j[col] = pd.to_datetime(j[col], errors="coerce")
    todo = j["actual_up"].isna()
    for i in j.index[todo]:
        df = frames.get(j.at[i, "symbol"])
        if df is None:
            continue
        idx = df.index.normalize()
        pos = idx.searchsorted(j.at[i, "signal_date"])
        if pos >= len(idx) or idx[pos] != j.at[i, "signal_date"] or pos + HORIZON >= len(idx):
            continue
        c0, c1 = float(df["Close"].iloc[pos]), float(df["Close"].iloc[pos + HORIZON])
        up = float(c1 > c0)
        j.at[i, "outcome_date"] = idx[pos + HORIZON]
        j.at[i, "future_return"] = c1 / c0 - 1
        j.at[i, "actual_up"] = up
        p = j.at[i, "ml_prob_up"]
        if pd.notna(p):
            j.at[i, "ml_correct"] = float((p > 0.5) == bool(up))
        call = j.at[i, "trend_call"]
        if call in ("up", "down"):
            j.at[i, "trend_correct"] = float((call == "up") == bool(up))
    return j


def stats(j):
    """Scorecard numbers for a set of journal rows (e.g. one month, or everything)."""
    s = j[j["actual_up"].notna()] if not j.empty else j
    out = {"scored": int(len(s)), "ml_hit_rate": np.nan, "ml_auc": np.nan, "base_rate_up": np.nan,
           "trend_calls": 0, "trend_hit_rate": np.nan}
    if s.empty:
        return out
    out["base_rate_up"] = float(s["actual_up"].mean())
    ml = s[s["ml_prob_up"].notna()]
    if len(ml):
        out["ml_hit_rate"] = float(ml["ml_correct"].mean())
        if ml["actual_up"].nunique() > 1:
            from sklearn.metrics import roc_auc_score
            out["ml_auc"] = float(roc_auc_score(ml["actual_up"], ml["ml_prob_up"]))
    tr = s[s["trend_correct"].notna()]
    out["trend_calls"] = int(len(tr))
    if len(tr):
        out["trend_hit_rate"] = float(tr["trend_correct"].mean())
    return out


def verdict(st, min_n=500):
    """Plain-English judgement of the ML predictions."""
    n, auc = st["scored"], st["ml_auc"]
    if n < min_n or pd.isna(auc):
        return "too early", f"{n} predictions scored; needs about {min_n} before the result means much."
    se = 0.5 / np.sqrt(max(n, 1))           # rough uncertainty of an AUC near 0.5
    if auc - 2 * se > 0.5:
        return "pass", f"AUC {auc:.3f} on {n} unseen predictions: better than a coin flip."
    if auc + 2 * se < 0.5:
        return "fail", f"AUC {auc:.3f}: worse than a coin flip."
    return "fail", f"AUC {auc:.3f} on {n} unseen predictions: no better than a coin flip (0.5)."


def main():
    j = _read()
    if j.empty:
        print("No predictions recorded yet. They are written by every bot run (Preview or Trade).")
        return
    st = stats(j)
    print(f"Predictions recorded: {len(j)}   scored: {st['scored']}")
    if st["scored"]:
        print(f"  ML model:      hit rate {st['ml_hit_rate']:.1%}   AUC {st['ml_auc']:.3f}   "
              f"(price actually rose {st['base_rate_up']:.1%} of the time)")
        print(f"  Trend signals: hit rate {st['trend_hit_rate']:.1%} on {st['trend_calls']} up/down calls")
    v, why = verdict(st)
    print(f"  Verdict: {v}. {why}")
    s = j[j["actual_up"].notna()].copy()
    if not s.empty:
        s["month"] = pd.to_datetime(s["signal_date"]).dt.to_period("M")
        print("\n  Month     scored  ML hit  ML AUC  trend hit")
        for m, g in s.groupby("month"):
            t = stats(g)
            print(f"  {m}  {t['scored']:6d}  {t['ml_hit_rate']:6.1%}  {t['ml_auc']:6.3f}  {t['trend_hit_rate']:8.1%}")


if __name__ == "__main__":
    main()

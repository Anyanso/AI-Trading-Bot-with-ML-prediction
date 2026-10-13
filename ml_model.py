"""ML direction model.

Predicts the PROBABILITY that price will be higher `horizon` days from now,
using gradient-boosted decision trees on technical features.

Trained walk-forward: fit on all data before a test window, predict that
window (data the model has never seen), slide forward, repeat. Every
prediction in the backtest is therefore out-of-sample.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, accuracy_score
from indicators import sma, rsi, macd, atr, bollinger_pct


FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786)


def fibonacci_features(df: pd.DataFrame, lookbacks=(60, 120)) -> pd.DataFrame:
    """Fibonacci retracement features over trailing swing ranges.

    For each lookback window, take the swing high and low and measure:
      fib_pos_N     where price sits in the range (0 = at the high, 1 = at the low),
                    i.e. how far it has retraced from the high
      fib_near_N    distance to the NEAREST Fibonacci level (0.236 ... 0.786);
                    small = price is sitting on a "Fib level"
      fib_level_N   which level that is (the ratio itself)
      fib_up_N      1 if the high came after the low (retracing an up-move)
    The model decides whether any of this carries information.
    """
    c, h, l = df["Close"], df["High"], df["Low"]
    f = pd.DataFrame(index=df.index)
    levels = np.array(FIB_LEVELS)
    for n in lookbacks:
        hi, lo = h.rolling(n).max(), l.rolling(n).min()
        rng = (hi - lo).replace(0, np.nan)
        pos = (hi - c) / rng
        dist = np.abs(pos.values[:, None] - levels[None, :])
        f[f"fib_pos_{n}"] = pos
        f[f"fib_near_{n}"] = np.where(pos.notna(), dist.min(axis=1), np.nan)
        f[f"fib_level_{n}"] = np.where(pos.notna(), levels[dist.argmin(axis=1)], np.nan)
        hi_age = h.rolling(n).apply(np.argmax, raw=True)
        lo_age = l.rolling(n).apply(np.argmin, raw=True)
        f[f"fib_up_{n}"] = (hi_age > lo_age).astype(float).where(hi_age.notna())
    return f


def make_features(df: pd.DataFrame, fib: bool = True) -> pd.DataFrame:
    c, v = df["Close"], df["Volume"]
    f = pd.DataFrame(index=df.index)
    for n in (1, 5, 10, 20, 60):
        f[f"ret_{n}"] = c.pct_change(n)
    for n in (20, 50, 200):
        f[f"dist_sma_{n}"] = c / sma(c, n) - 1
    f["sma50_vs_200"] = sma(c, 50) / sma(c, 200) - 1
    f["rsi_14"] = rsi(c, 14)
    line, sig, hist = macd(c)
    f["macd_hist_norm"] = hist / c
    f["atr_pct"] = atr(df, 14) / c
    f["vol_20"] = c.pct_change().rolling(20).std()
    f["vol_ratio"] = f["vol_20"] / c.pct_change().rolling(100).std()
    f["boll_pct"] = bollinger_pct(c)
    f["volume_chg"] = v / v.rolling(20).mean() - 1
    f["dist_high_60"] = c / c.rolling(60).max() - 1
    f["dist_low_60"] = c / c.rolling(60).min() - 1
    if fib:
        f = f.join(fibonacci_features(df))
    return f


def make_label(df: pd.DataFrame, horizon: int) -> pd.Series:
    """1 if the close `horizon` days ahead is higher than today's close."""
    fwd = df["Close"].shift(-horizon) / df["Close"] - 1
    return (fwd > 0).astype(float).where(fwd.notna())


def walk_forward_probabilities(df, horizon=5, min_train=750, step=63, seed=0, fib=True):
    """Returns (probabilities, metrics). Retrains every `step` days (~a quarter)."""
    X, y = make_features(df, fib=fib), make_label(df, horizon)
    valid = X.notna().all(axis=1)
    X, y = X[valid], y[valid]
    probs = pd.Series(np.nan, index=df.index)

    for start in range(min_train, len(X), step):
        # Drop the last `horizon` training rows: their labels use prices that
        # fall inside the test window (that would leak the future).
        train_end = start - horizon
        Xtr, ytr = X.iloc[:train_end], y.iloc[:train_end]
        keep = ytr.notna()
        model = HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=200,
            l2_regularization=1.0, min_samples_leaf=50, random_state=seed)
        model.fit(Xtr[keep], ytr[keep])
        Xte = X.iloc[start:start + step]
        probs.loc[Xte.index] = model.predict_proba(Xte)[:, 1]

    scored = probs.dropna().index.intersection(y.dropna().index)
    yt, pt = y.loc[scored], probs.loc[scored]
    metrics = {
        "oos_days": int(len(scored)),
        "accuracy": float(accuracy_score(yt, pt > 0.5)),
        "base_rate_up": float(yt.mean()),   # accuracy of always guessing "up"
        "auc": float(roc_auc_score(yt, pt)) if yt.nunique() > 1 else float("nan"),
    }
    return probs, metrics


def ml_positions(probs: pd.Series, enter=0.55, exit=0.45, short=False) -> pd.Series:
    """Turn P(up) into positions, with a gap between entry and exit so the bot
    doesn't flip-flop on noise.
      long-only:  long when P(up) > enter, back to cash when P(up) < exit
      with short: also short when P(up) < 1-enter, cover when P(up) > 1-exit
    """
    pos, state = [], 0
    for p in probs.values:
        if np.isnan(p):
            state = 0
        elif p > enter:
            state = 1
        elif short and p < 1 - enter:
            state = -1
        elif state == 1 and p < exit:
            state = 0
        elif state == -1 and p > 1 - exit:
            state = 0
        pos.append(state)
    return pd.Series(pos, index=probs.index)

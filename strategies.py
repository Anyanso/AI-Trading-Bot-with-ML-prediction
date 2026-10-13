"""Rule-based strategies.

Each strategy returns a "target position" series decided at the CLOSE of bar t:
 1 = long (bet price rises), 0 = flat (cash), -1 = short (bet price falls).
The backtester only acts on it from bar t+1, so no strategy can peek at the future.

With short=False (stocks, or if you only want to buy) the -1s become 0.

To add your own: write a function (df, short=True, **params) -> pd.Series of
1/0/-1 and register it in STRATEGIES.
"""
import numpy as np
import pandas as pd
from indicators import sma, ema, rsi, macd, atr


def _side(long_cond, short_cond, short):
    pos = long_cond.astype(int)
    if short:
        pos = pos - short_cond.astype(int)
    return pos


def ma_crossover(df, fast=50, slow=200, short=True):
    """Long while the fast moving average is above the slow one, short while below."""
    c = df["Close"]
    f, s = sma(c, fast), sma(c, slow)
    return _side(f > s, f < s, short)


def ema_trend_rsi(df, fast=20, slow=100, rsi_n=14, rsi_exit=80, short=True):
    """EMA trend direction, stepping aside when RSI says the move is overheated."""
    c = df["Close"]
    f, s, r = ema(c, fast), ema(c, slow), rsi(c, rsi_n)
    return _side((f > s) & (r < rsi_exit), (f < s) & (r > 100 - rsi_exit), short)


def macd_trend(df, fast=12, slow=26, signal=9, filter_n=200, short=True):
    """MACD momentum, only in the direction of the long-term average."""
    c = df["Close"]
    line, sig, _ = macd(c, fast, slow, signal)
    m = sma(c, filter_n)
    return _side((line > sig) & (c > m), (line < sig) & (c < m), short)


def breakout_atr_stop(df, entry_n=55, atr_n=20, atr_mult=3.0, short=True):
    """Long a new N-bar high / short a new N-bar low; exit on an ATR trailing stop."""
    c = df["Close"].values
    hi = df["High"].rolling(entry_n).max().shift(1).values
    lo = df["Low"].rolling(entry_n).min().shift(1).values
    a = atr(df, atr_n).values
    pos = np.zeros(len(df), dtype=int)
    state, extreme = 0, 0.0
    for i in range(len(df)):
        if np.isnan(hi[i]) or np.isnan(a[i]):
            continue
        if state == 1:
            extreme = max(extreme, c[i])
            if c[i] < extreme - atr_mult * a[i]:
                state = 0
        elif state == -1:
            extreme = min(extreme, c[i])
            if c[i] > extreme + atr_mult * a[i]:
                state = 0
        if state == 0:
            if c[i] > hi[i]:
                state, extreme = 1, c[i]
            elif short and c[i] < lo[i]:
                state, extreme = -1, c[i]
        pos[i] = state
    return pd.Series(pos, index=df.index)


STRATEGIES = {
    "ma_crossover": ma_crossover,
    "ema_trend_rsi": ema_trend_rsi,
    "macd_trend": macd_trend,
    "breakout_atr_stop": breakout_atr_stop,
}

# Small grids for parameter search. Keep these modest: the more you search,
# the more likely the "best" result is luck (overfitting).
PARAM_GRIDS = {
    "ma_crossover": {"fast": [20, 50], "slow": [100, 200]},
    "ema_trend_rsi": {"fast": [10, 20], "slow": [50, 100], "rsi_exit": [75, 85]},
    "macd_trend": {"filter_n": [100, 200]},
    "breakout_atr_stop": {"entry_n": [20, 55], "atr_mult": [2.0, 3.0]},
}

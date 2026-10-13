"""Backtester: turns a 0/1 target-position series into an equity curve and stats."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def run_backtest(df: pd.DataFrame, target: pd.Series, cost_bps: float = 10.0) -> pd.DataFrame:
    """target[t] (1 long, 0 flat, -1 short) is decided at the close of bar t and held during bar t+1.
    A short position earns the negative of the price return.
    cost_bps is charged on every change in position (fees + slippage)."""
    ret = df["Close"].pct_change().fillna(0)
    pos = target.reindex(df.index).fillna(0).shift(1).fillna(0)
    turnover = pos.diff().abs().fillna(pos.abs())
    strat_ret = pos * ret - turnover * cost_bps / 10_000
    return pd.DataFrame({
        "position": pos,
        "ret": strat_ret,
        "equity": (1 + strat_ret).cumprod(),
        "turnover": turnover,
    })


def stats(bt: pd.DataFrame, periods_per_year: int = TRADING_DAYS) -> dict:
    r, eq = bt["ret"], bt["equity"]
    years = len(r) / periods_per_year
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(periods_per_year)
    sharpe = r.mean() / r.std() * np.sqrt(periods_per_year) if r.std() > 0 else np.nan
    dd = eq / eq.cummax() - 1
    pos = bt["position"]
    entries = int(((pos != 0) & (pos != pos.shift(1).fillna(0))).sum())  # new or flipped trades
    return {
        "total_return": eq.iloc[-1] - 1,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": dd.min(),
        "time_invested": pos.abs().mean(),
        "time_short": (pos < 0).mean(),
        "trades": entries,
    }


def window(df: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Slice data, then re-base so every strategy is compared on the same dates."""
    return df.loc[start:end]

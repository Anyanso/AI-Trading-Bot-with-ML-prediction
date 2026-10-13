"""Price data loading: Yahoo Finance, a local CSV, or simulated data for testing."""
import os
import numpy as np
import pandas as pd


def load_yahoo(ticker: str, start: str = "2010-01-01", end: str | None = None) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):  # newer yfinance returns (field, ticker)
        df.columns = df.columns.get_level_values(0)
    return _clean(df)


# MetaTrader 5 timeframes and roughly how many bars each has per year
# (forex trades ~24h x 5 days; used to annualise returns and Sharpe).
MT5_TIMEFRAMES = {"M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
                  "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1"}
BARS_PER_YEAR = {"M5": 260 * 288, "M15": 260 * 96, "M30": 260 * 48,
                 "H1": 260 * 24, "H4": 260 * 6, "D1": 260, "W1": 52}


def mt5_connect():
    """Attach to the MetaTrader 5 terminal running on this (Windows) computer.

    With no settings it uses whatever account the terminal is already logged into.
    Optional environment variables (never put passwords in code):
      MT5_PATH      full path to terminal64.exe, if MT5 isn't found automatically
      MT5_LOGIN, MT5_PASSWORD, MT5_SERVER   to log into a specific account
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise SystemExit("MetaTrader5 package missing. On Windows run:  pip install MetaTrader5")
    kwargs = {}
    if os.environ.get("MT5_PATH"):
        kwargs["path"] = os.environ["MT5_PATH"]
    if os.environ.get("MT5_LOGIN"):
        kwargs.update(login=int(os.environ["MT5_LOGIN"]),
                      password=os.environ.get("MT5_PASSWORD", ""),
                      server=os.environ.get("MT5_SERVER", ""))
    if not mt5.initialize(**kwargs):
        err = mt5.last_error()
        mt5.shutdown()
        raise SystemExit(f"Could not connect to MetaTrader 5: {err}\n"
                         "Check that MT5 is installed, open, and logged into an account.")
    return mt5


def load_mt5(symbol: str, timeframe: str = "D1", bars: int = 5000) -> pd.DataFrame:
    """Download the last `bars` candles for `symbol` from the MT5 terminal (read-only)."""
    tf = timeframe.upper()
    if tf not in MT5_TIMEFRAMES:
        raise SystemExit(f"Unknown timeframe {timeframe}. Use one of {list(MT5_TIMEFRAMES)}")
    mt5 = mt5_connect()
    try:
        if not mt5.symbol_select(symbol, True):
            raise SystemExit(f"Symbol {symbol} not found. Check the exact name in MT5's Market Watch "
                             "(some accounts use suffixes like EURUSDm).")
        rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, MT5_TIMEFRAMES[tf]), 0, bars)
        if rates is None or len(rates) == 0:
            raise SystemExit(f"No price history returned for {symbol}: {mt5.last_error()}")
    finally:
        mt5.shutdown()
    df = pd.DataFrame(rates)
    df.index = pd.to_datetime(df["time"], unit="s")
    df.index.name = "Date"
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                            "close": "Close", "tick_volume": "Volume"})
    return _clean(df)


def load_csv(path: str) -> pd.DataFrame:
    """CSV with a Date column and Open, High, Low, Close, Volume columns."""
    df = pd.read_csv(path, parse_dates=[0], index_col=0)
    df.columns = [c.strip().title() for c in df.columns]
    return _clean(df)


def simulate(n_days: int = 3000, seed: int = 7) -> pd.DataFrame:
    """Regime-switching random walk (bull / bear / sideways) for testing the pipeline.
    It is NOT real market data; results on it say nothing about real profitability."""
    rng = np.random.default_rng(seed)
    regimes = {0: (0.0006, 0.009), 1: (-0.0007, 0.016), 2: (0.0, 0.011)}  # (drift, vol)
    state, rets = 0, []
    for _ in range(n_days):
        if rng.random() < 0.012:
            state = rng.choice([s for s in regimes if s != state])
        mu, sd = regimes[state]
        rets.append(rng.normal(mu, sd))
    close = 100 * np.exp(np.cumsum(rets))
    idx = pd.bdate_range("2014-01-01", periods=n_days)
    noise = rng.uniform(0.002, 0.012, n_days)
    df = pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.003, n_days)),
        "High": close * (1 + noise),
        "Low": close * (1 - noise),
        "Close": close,
        "Volume": rng.integers(1_000_000, 5_000_000, n_days).astype(float),
    }, index=idx)
    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df = df[~df.index.duplicated()].sort_index().dropna()
    return df

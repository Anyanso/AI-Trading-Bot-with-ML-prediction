# Trading research bot

Tests rule-based trend strategies and an ML price-direction model side by side, on the same unseen data, with trading costs included. It also logs current signals so you can monitor them over time.

**Research tool plus a demo paper-trading executor (`execute.py`, see PAPER_TRADING.md). Not financial advice.**

## Setup

```bash
pip install -r requirements.txt
python run.py --ticker SPY          # any Yahoo Finance symbol: AAPL, XIU.TO, BTC-USD, ...
python run.py --mt5 EURUSD --timeframe H4   # data from MetaTrader 5 (Windows; see SETUP_MT5.md)
python scan.py --cost-bps 2                 # test 8 MT5 symbols at once and rank strategies
python portfolio.py                         # trend + long-only blend across ~40 markets (--mix 0.5, --profile balanced)
python compare.py                           # bot versions vs the bank, the S&P 500 and 60/40, after costs (Yahoo data)
python list_symbols.py                      # list every symbol your MT5 account offers
python execute.py                           # dry run of today's trades; add --live to trade the demo
python dashboard.py                         # plain-English dashboard of what the bot holds and did
python signals.py                           # shareable market readings (page + short message)
python app.py                               # control panel: dashboard + buttons (or double-click "Start bot.bat")
```

Open `output/report.html` in a browser.

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--ticker` / `--mt5` / `--csv` / `--simulate` | | Data source (CSV needs Date, Open, High, Low, Close, Volume) |
| `--timeframe` / `--bars` | D1 / 5000 | MT5 candle size (M15 … W1) and how many candles to download |
| `--side` | auto | `long` = buy-only, `both` = long and short. Auto: both for MT5/CSV, long for `--ticker` |
| `--start` | 2010-01-01 | First date to download |
| `--horizon` | 5 | ML predicts direction this many candles ahead |
| `--enter` / `--exit` | 0.55 / 0.45 | ML goes long above `enter` and closes below `exit`; with shorts, mirror image (short below 1−enter) |
| `--cost-bps` | 10 | Fees + slippage per trade, in basis points (10 = 0.10%) |
| `--test-frac` | 0.4 | Share of history kept unseen for the final test |

## How it works

| File | Role |
|---|---|
| `data.py` | Loads prices (Yahoo, MetaTrader 5, CSV, or simulated) |
| `check_mt5.py` | Tests the MT5 connection, account type and symbol data (read-only) |
| `indicators.py` | SMA, EMA, RSI, MACD, ATR, Bollinger |
| `strategies.py` | Rule strategies: MA crossover, EMA trend + RSI exit, MACD with trend filter, breakout with ATR trailing stop |
| `ml_model.py` | Feature engineering (incl. Fibonacci retracements), gradient-boosted tree classifier, walk-forward training |
| `backtest.py` | Applies signals the *next* day, charges costs, computes stats |
| `run.py` | Runs everything for one symbol, writes the report and signal log |
| `scan.py` | Runs everything across many symbols and ranks strategies by how consistently they work |
| `portfolio.py` | Volatility-targeted trend portfolio across forex, metals, indices, bond and commodity ETFs; outputs today's target positions |
| `compare.py` | Side-by-side test of the bot's versions against the bank, buy-and-hold S&P 500 and 60/40, including overnight financing costs. See BALANCED_SETUP.md |
| `symbols.py` | Finds each market under your broker's own name (e.g. GER40.cash = DAX); manual overrides via `symbol_map` in settings.json |
| `list_symbols.py` | Lists every symbol on your MT5 account by category |
| `app.py` / `Start bot.bat` | Local control panel: risk switch, preview / trade now / close everything, daily auto-trading |
| `signals.py` | Shareable "Market readings" report and a short message to paste into WhatsApp/Telegram, with the model's track record and a not-advice notice |
| `alerts.py` | Phone and email alerts through the free ntfy app (stop hit, near safety switch, kill switch, failed runs, daily summary) |
| `dashboard.py` | Builds output/dashboard.html: account value, positions in plain words, today's changes and why, markets to watch |
| `execute.py` | Trades the 25% trend / 75% long-only blend on MT5: dry run by default, demo unless `--real --confirm-account`, with broker pre-checks, crash-guard stop-losses, a 3-day re-entry wait after a stop, kill switch, alerts and logs |

**Long and short.** Each strategy can be long (+1), flat (0) or short (−1). Shorts profit when price falls, which matters in forex where trends run both ways.

**Trend portfolio.** Four trend speeds (EMA 8/32, 16/64, 32/128, 64/256) vote long or short in each market. Each position is sized by the market's volatility so every market carries equal risk, and the whole book is scaled to a target volatility (10% a year by default). Nothing is tuned on your data. It is compared with holding the same markets long-only at the same risk, and with a **blend** of the two (`--mix`, default 50% of risk in trend), re-scaled to the same target volatility. No single position may exceed 25% of equity (`--max-weight`).

**Rule strategies.** Parameters are chosen on the first 60% of history, then judged only on the last 40%.

**ML model.** Gradient-boosted decision trees output the probability that price is higher in N days. Training is walk-forward: fit on the past, predict the next quarter, retrain, repeat. The model never scores on data it trained on.

**Fibonacci.** Retracement position, distance to the nearest Fib level (23.6–78.6%) and swing direction over 60- and 120-day ranges are fed in as features. The model is also run *without* them, and the report shows whether they helped.

**Monitoring.** Each run appends the latest signal of every model to `output/signals_log.csv`. To run it daily, schedule `python run.py --ticker SPY` after market close (cron on Mac/Linux, Task Scheduler on Windows).

## Reading the results honestly

- Compare every strategy with **Buy & hold**. Beating it after costs, with a smaller drawdown, is the bar.
- ML **accuracy** only matters relative to the "always up" base rate. **AUC** near 0.50 means no real predictive power.
- Don't keep tweaking settings until the test period looks good. That turns the test into training data and the result into luck.
- Results on one asset prove little. Check several assets and time periods.

## Suggested next steps

1. Run it on assets you actually care about and compare.
2. Paper-trade the best candidate for a few months using the signal log.
3. Only then consider connecting a broker API for live orders, with small size and hard loss limits.

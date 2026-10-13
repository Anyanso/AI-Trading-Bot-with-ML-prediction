# Paper trading on the demo, and when to consider real money

`execute.py` trades the portfolio: by default **25% of risk in trend-following, 75% long-only**, across the ~37 markets, at a 10% yearly volatility target. Once a day it works out today's target positions and sends only the trades needed to get there.

## 0. The easy way: the control panel

Double-click **`Start bot.bat`** in the tradebot folder (MT5 must be open and logged in). A black window opens. Leave it open. Your browser shows the dashboard with buttons:

- **Risk level**: Cautious / Normal / Aggressive. Takes effect at the next trade run.
  | Level | Yearly swings | Safety switch closes everything at |
  |---|---|---|
  | Cautious | about ±5% | −10% |
  | Normal (tested default) | about ±10% | −15% |
  | Aggressive | about ±15% | −25% |
  On a real-money account each level uses half these position sizes.
- **Strategy**: *Classic* (every market gets equal risk, currencies included) or *Balanced* (stocks + bonds: the steady part puts half its risk in stock indices). Balanced needs a broker with tradable stock indices; see BALANCED_SETUP.md. The next run rebuilds the portfolio.
- **Preview today's trades**: shows what it would do; sends nothing.
- **Trade now**: places today's trades on the practice account (asks you to confirm).
- **Close everything**: closes every position the bot opened (asks you to confirm).
- **Trade automatically every weekday**: runs by itself **while the control panel window is open**. Choose *Follow US market hours* (recommended: 45 minutes after the US market opens; in Saskatchewan that's 8:15 a.m. in US summer time and 9:15 a.m. in US winter time, and it adjusts itself when the US changes its clocks), or *At a fixed time*. The panel shows today's US market hours and the next run time. You get a phone alert 3 days and 1 day before each US clock change. A run missed by up to 3 hours (e.g. the PC was off) is caught up once.
- **Signal report to share**: today's market readings in plain English, plus a short message with a Copy button for WhatsApp or Telegram. Also saved as `output\signal_report.html` and `output\signal_message.txt`.
- **Phone alerts**: install the free **ntfy** app, subscribe to the private topic shown in the panel, tick *Send alerts to my phone*, and press *Send test alert*. Optionally add an email address. You're alerted when a safety stop closes a position, the account nears the safety switch, the safety switch triggers, a run fails, or orders are rejected, plus an optional short summary after each daily run.
- **Live numbers**: while the panel is open, the account value, gains and each position's profit / loss refresh themselves from MT5 once a minute ("updated … (live)" at the top). The rest of the page refreshes after each run, or when you press F5.
- **Resume trading** appears only after the safety switch has stopped the bot.

The buttons never trade a real-money account. That stays a deliberate command-line step (section 6).

The command-line steps below do the same things, for reference.

## 1. First run: dry run (sends nothing)

MT5 open, logged into the demo, Algo Trading button green. In the Command Prompt (with `(venv)` showing):

```
python execute.py
```

It downloads ~6 years of daily data per market (1–2 minutes), then prints the plan, one market per line:

```
  XAUUSD   weight +12.0%  target   +0.11 lots  (now +0)
    [dry run] BUY 0.11 XAUUSD @ ~2650.4  open/increase
```

- **weight**: position size as a % of your account equity
- **target**: that weight converted to lots for this symbol
- **now**: what the bot currently holds

## 2. Go live on the demo

```
python execute.py --live
```

Watch MT5's **Toolbox → Trade** tab fill with positions (comment "tradebot"). Some markets may say *market closed*; they'll be traded on the next run. Best time to run: **weekdays around 8:30–10:00 Edmonton time** (US, European and forex markets are all open then).

Check on it any time:

```
python execute.py --status
```

## 3. Run it automatically every weekday

Windows **Task Scheduler** → *Create Basic Task* → Name "tradebot" → *Weekly*, tick Mon–Fri, time 08:45 → *Start a program*:

- Program: `C:\path\to\tradebot\venv\Scripts\python.exe`
- Arguments: `execute.py --live`
- Start in: `C:\path\to\tradebot`

MT5 must be open and logged in at that time, and the PC awake.

## 4. The dashboard: the easy way to follow it

After every run the bot refreshes **`output\dashboard.html`**. Double-click it to open it in your browser, or run:

```
python dashboard.py
```

It shows, in plain English: what the account is worth, gain or loss since the start and since yesterday, every position (what it is, buy or sell, size, profit or loss, trend signals), what changed at the last check and why, markets whose trend may change soon, and recent orders.

The dashboard also shows **What trading has cost so far**: spread (estimated at each trade), the broker's commissions and fees, and overnight swap, read from MT5's own deal history. This is the key number to watch on the demo: the backtest's expected gain was only about 2–3% a year, so costs much above 1% a year would eat most of it.

Every order is also **pre-checked with the broker** (MT5's order_check) before it is sent, in previews too. Problems like "the market is closed" or "not enough free margin" show up in plain English instead of as failed orders.

## 4b. Behind the scenes (for detail)

| File / tab | What it tells you |
|---|---|
| `output\trade_log.csv` | every order: time, lots, fill price, success or the broker's error |
| `output\account_log.csv` | equity after each run: your paper track record |
| MT5 **History** tab | real fills, commissions |
| `--status` → swap column | overnight rollover costs, which the backtest did *not* include |

Things to learn from the demo: how big a lot is in each market, how wide spreads are at your run time, how much margin the book uses, and what swap costs add up to.

## 5. Safety features

- **Crash-guard stops at the broker**: after each run, every bot position gets a stop-loss about 5 normal daily moves away from the current price (e.g. roughly 2–3% for EURUSD, more for silver). The broker holds it, so it works even when your PC is off. It only triggers on a genuine crash day, and is reset each morning. After a stop is hit, the bot waits 3 days before buying back into that market. Weekend or overnight price gaps can fill a stop beyond its level. Turn the stops off with `--no-stops`.

- **Dry run by default**: nothing is sent without `--live`.
- **Only its own trades**: positions you open by hand are never touched.
- **Rebalance band**: skips changes smaller than 10% of a position, to avoid paying spreads on noise.
- **Margin limit**: won't add positions once margin used exceeds 50% of equity (`--max-margin`).
- **Kill switch**: if equity falls 15% below its peak (`--max-drawdown`), it closes all bot positions and writes `output\HALTED`. It won't trade again until you delete that file.
- **Emergency stop**: `python execute.py --close-all --live` closes every bot position.

## 6. Moving to a real account (only if the demo is convincing)

The command needs two confirmations, so it can never happen by accident:

```
python execute.py --real --confirm-account 12345678 --live
```

`12345678` must be the real account's number. Real mode uses **half the risk** (5% target volatility) unless you set `--target-vol`.

**Checklist before you do it**

- [ ] At least **3 months** of demo trading with no unexplained errors in `trade_log.csv`.
- [ ] Demo results are in line with the backtest (expect roughly 0.3 Sharpe: small gains, ups and downs).
- [ ] You know your real broker's lot sizes, margin and swap costs for these symbols. They differ from the demo.
- [ ] The money is an amount you can afford to lose entirely.
- [ ] You've run `python execute.py --real --confirm-account <n>` **without** `--live` and checked the plan.
- [ ] Your broker is permitted to serve clients where you live.

Keep in mind: the backtest edge of this strategy is small (it roughly matched holding the same markets), and a few months of demo results are mostly luck either way. A good demo proves the *mechanics* work; it does not prove the strategy will make money.

## 7. Sharing signals with family and friends

The signal report is built for sharing for free, as information about what an automated model is doing:

- It always includes the model's practice-account track record, and warns "still early" until there are at least 60 days of it.
- It always includes a not-financial-advice notice. Keep it in when you share.
- Wait until the model has a track record you're comfortable showing people.
- If you ever want to charge for signals or share them publicly, check first with your provincial securities regulator (in Saskatchewan, the FCAA), because that can count as giving investment advice.

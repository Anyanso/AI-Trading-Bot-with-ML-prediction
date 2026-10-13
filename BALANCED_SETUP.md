# Switching the bot to stocks + bonds: step by step

**Goal:** earn more than the bank by letting the bot hold stock indices, the main long-term source of return, alongside bonds and gold. The current MetaQuotes demo can't trade stock indices, so this needs a broker demo that can.

**The rule:** test first, switch second, judge with real numbers last. Not financial advice.

---

## Phase 1: Test it on your PC (tonight or tomorrow, about 10 minutes)

**Step 1. Install the new files.** Unzip `tradebot.zip` and copy these files into your tradebot folder, replacing the old ones:
`app.py, dashboard.py, execute.py, portfolio.py, signals.py, symbols.py, compare.py, BALANCED_SETUP.md`.
Leave `settings.json`, the `output` folder and `venv` alone.

**Step 2. Run the comparison.** Close the control panel window. Open Command Prompt in the tradebot folder and run:

```
venv\Scripts\activate
python compare.py
```

It downloads about 20 years of prices from Yahoo (around 2 minutes). When it finishes, double-click **`output\compare.html`**.

**Step 3. Read the verdict.** The page compares, after all costs:

| Line | What it is |
|---|---|
| Bot, balanced profile | the new stocks + bonds version |
| Bot, classic mix with stock indices | the current design on a broker with indices |
| Bot today (no stock indices) | what you're running now |
| Buy and hold S&P 500 | a plain stock ETF, no borrowing |
| 60/40 stocks/bonds | the classic investor's portfolio, no borrowing |
| The bank | 3-month Treasury bills, close to a high-interest savings account |

**Decision rule, set before looking at the results:**
- **Go ahead** if the balanced bot beats the bank by **more than 1% a year** and its worst drop is something you could live through.
- **Stop and rethink** if it doesn't. Financing costs may be eating the gains, and a plain ETF in a TFSA could be the better choice.

Send me a screenshot of the page and we'll decide together. Then restart the control panel. Keep the strategy on **Classic** for now: on the MetaQuotes demo, Balanced can't buy stocks.

---

## Phase 2: Open a demo with a broker that has stock indices (about 30 minutes)

**Step 4. Pick a broker.** All three are regulated in Canada (CIRO) and offer MetaTrader 5. Check each one's site for current details.

| Broker | Why | Check |
|---|---|---|
| **FOREX.com Canada** (first choice) | Offers MT5 in Canada with stock indices, commodities and stock CFDs | That the MT5 demo includes indices |
| **AvaTrade** (via Friedberg Mercantile) | MT5 demo with a $100,000 balance | Index list and overnight fees on the demo |
| **CMC Markets Canada** | Large CFD range; MT5 on a separate account type | That MT5 (not just their own platform) has the indices |

Questions to answer for each (from its website or support chat):
1. Does the **MT5 demo** include **stock index CFDs** (S&P 500, Nasdaq, DAX, FTSE, Nikkei…)?
2. Does it offer **bond** CFDs or ETFs (TLT, IEF, or "US T-Note / T-Bond")? If not, the bot puts the bond share into the other classes, which makes it riskier.
3. What's the **overnight financing** on index CFDs? It's usually the bank rate + 2–3% a year. Over 3% makes the strategy much less attractive.
4. **Minimum trade size** on indices and gold (helps with the small-account follow-up).

**Step 5. Open the demo.** On the broker's website choose *Demo account → MetaTrader 5*. Set a **USD** account with **100,000** balance if you can choose. Download *their* MT5 if they ask; a second MT5 installation sits fine beside the first. Write down the login, password and server.

**Step 6. Log in and allow algo trading.** Open the new MT5, log into the demo, and make sure **Algo Trading** is **green**. Close the old MetaQuotes MT5: the bot works with whichever MT5 is open. If both are open, close the one you don't want, or ask me how to point the bot at a specific one.

**Step 7. Preview on Classic first.** Start the control panel and press **Preview today's trades**. Near the top, look for:
- `This broker's names: US500→SPX500, DE40→GER40…` shows the bot found the markets under the broker's own names.
- `Not offered by this broker: …`
- `Not tradable on this account, left out: …`

Copy the preview text and send it to me. If an important market is missing (e.g. the S&P 500 under an unusual name), I'll add it to `settings.json` as `"symbol_map": {"US500": "<broker's name>"}`.

The bot keeps a separate track record per account, so the new demo starts fresh.

---

## Phase 3: Switch to Balanced (5 minutes, during US market hours)

**Step 8.** In the panel, under **Strategy**, click **Balanced** and confirm.

**Step 9.** Press **Preview today's trades** and check:
- Stock indices appear with a **BUY**.
- There's no `⚠ No stock indices are tradable` line.
- Note any `Margin check… Scaling` line. With index CFDs margin is usually small (5–20%), so it should disappear.

**Step 10.** Between **7:30 a.m. and 2:00 p.m.** your time (8:30–3:00 after 1 November), press **Trade now**, or leave *Trade automatically* on and let the morning run do it.

---

## Phase 4: Judge it (2–3 months)

**Step 11. Watch three numbers on the dashboard:**
1. **Gain since the bot started**, against what the bank would have paid over the same time.
2. **Overnight swap** in *What trading has cost so far*: the real financing cost. Compare it with what `compare.py` assumed (2.5% a year above the bank rate). If it's higher, re-run with the broker's real markup, for example `python compare.py --markup 0.03`.
3. **Worst dip.** Is it in line with the backtest?

**Step 12. Decide.** After 2–3 months, compare the results with Phase 1's expectations. A few months is still mostly luck, so we're checking that costs and mechanics match the plan, not judging the profit. Only then consider real money, which uses half the risk and needs the two-step confirmation. Always check the alternative too: a low-cost global stock ETF in a TFSA, with no leverage, no swap costs and no tax on gains.

---

### What changed in the bot

- **Strategy switch** in the panel: *Classic* (as before) or *Balanced*. The setting is saved, used by Preview, Trade now and the automatic runs, and can be switched back at any time. The next run rebuilds the portfolio.
- **Balanced profile:** the trend part (25% of risk) is unchanged. The steady part (75%) puts 50% of its risk in stock indices, 35% in bonds, and 15% in gold, silver and commodities. Currencies are left out of it, since they have no long-term return. These shares are fixed round numbers, not tuned to past data.
- **Broker names are found automatically** (e.g. GER40.cash = DAX), and the dashboard still shows plain names.
- **`compare.py`:** the side-by-side test, including overnight financing costs and dividends.
- **`python portfolio.py --profile balanced`:** the detailed research report for the new profile.

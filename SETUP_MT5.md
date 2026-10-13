# Setup guide: MetaTrader 5 + the trading bot (Windows)

This connects the bot to MetaTrader 5 for **price data only**. It places no trades. Order execution comes later.

## Part 1 – MetaTrader 5

1. **Download MT5** from the official site, metatrader5.com (Download → MetaTrader 5 for Windows). Run the installer with default settings.
2. **Open a demo account.** On first launch MT5 offers to open an account. Choose the *MetaQuotes-Demo* server → "Open a demo account" → fill in the form, deposit e.g. 10,000, leverage 1:100. Save the login number and password it shows you.
   - Later you can log into your broker's demo or live account instead (File → Login to Trade Account).
3. **Show the symbols you want.** In the Market Watch panel (Ctrl+M), right-click → *Show All*, or *Symbols* to find e.g. EURUSD, GBPUSD, XAUUSD. Note the **exact** symbol name; some accounts add suffixes like `EURUSDm`.
4. **Load more history.** Tools → Options → Charts → set *Max bars in chart* to *Unlimited*. Then open a chart of each symbol (right-click → Chart Window) on the timeframe you'll use and scroll left a little. This makes MT5 download the history.
5. **Allow algorithmic trading.** Tools → Options → Expert Advisors → tick *Allow algorithmic trading*, and make sure the **Algo Trading** button on the toolbar is green. (Not needed for data, but needed later for trading.)

## Part 2 – Python

6. **Install Python 3.11 or 3.12** (64-bit) from python.org. On the first installer screen, tick **"Add python.exe to PATH"**.
7. **Unzip the bot** to a simple folder, e.g. `C:\tradebot`.
8. **Open Command Prompt in that folder:** in File Explorer, open `C:\tradebot`, click the address bar, type `cmd`, press Enter.
9. **Create a virtual environment and install packages:**
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
   Each time you open a new Command Prompt later, run `venv\Scripts\activate` again first.

## Part 3 – Connect and run

10. **Keep MT5 open and logged in**, then check the connection:
    ```
    python check_mt5.py EURUSD
    ```
    You should see ✔ Connected, your account marked **[DEMO]**, and the number of candles available.
11. **Run the bot on MT5 data:**
    ```
    python run.py --mt5 EURUSD --timeframe D1
    python run.py --mt5 EURUSD --timeframe H4 --bars 10000
    ```
    Open `output\report.html` in your browser.

### Useful settings for forex

- `--cost-bps 2` – EURUSD's spread is roughly 1 pip ≈ 1 basis point, so the default of 10 is too pessimistic for major pairs. Use a higher value for exotic pairs and gold.
- `--horizon` counts **candles**: on H4, `--horizon 6` means one day ahead.
- At least 1,500 candles are needed; 5,000+ is better.

## Troubleshooting

| Message | Fix |
|---|---|
| `MetaTrader5 package missing` | You're not on Windows or the venv isn't active: `venv\Scripts\activate`, then `pip install MetaTrader5` |
| `Could not connect to MetaTrader 5` | Open MT5 and log in. If it still fails, set the path: `set MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe` |
| `Symbol ... not found` | Use the exact name from Market Watch, including any suffix |
| `Need at least 1500 bars` | Raise `--bars`, raise *Max bars in chart* (step 4), or use a smaller timeframe |
| `'python' is not recognized` | Reinstall Python with "Add python.exe to PATH" ticked |

## Running it every day (monitoring)

Windows Task Scheduler → Create Basic Task → Daily → Action "Start a program":
- Program: `C:\tradebot\venv\Scripts\python.exe`
- Arguments: `run.py --mt5 EURUSD --timeframe D1`
- Start in: `C:\tradebot`

MT5 must be running at that time. New signals are appended to `output\signals_log.csv`.

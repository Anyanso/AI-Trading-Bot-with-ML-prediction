"""Check that Python can talk to your MetaTrader 5 terminal. Read-only: places no trades.

    python check_mt5.py            # checks connection, account and EURUSD data
    python check_mt5.py GBPUSD     # check a different symbol
"""
import sys
import data


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    mt5 = data.mt5_connect()
    try:
        term, acc = mt5.terminal_info(), mt5.account_info()
        print("✔ Connected to MetaTrader 5")
        print(f"  Terminal:  {term.name}  (build {mt5.version()[1]})")
        print(f"  Algo Trading enabled in terminal: {'YES' if term.trade_allowed else 'NO (turn on the Algo Trading button)'}")
        if acc is None:
            print("✘ No account logged in. In MT5: File → Login to Trade Account.")
            return
        demo = acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        print(f"  Account:   {acc.login} on {acc.server}  [{'DEMO' if demo else 'REAL MONEY'}]")
        print(f"  Balance:   {acc.balance:,.2f} {acc.currency}   Leverage 1:{acc.leverage}")
        if not demo:
            print("  ⚠ This is a REAL account. Use a demo account while building and testing.")

        if not mt5.symbol_select(symbol, True):
            names = [s.name for s in (mt5.symbols_get() or [])][:15]
            print(f"✘ Symbol {symbol} not found. Some available symbols: {', '.join(names)}")
            return
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 5000)
        if rates is None or len(rates) == 0:
            print(f"✘ No price history for {symbol}: {mt5.last_error()}")
            return
        print(f"✔ {symbol}: {len(rates)} daily candles available")
        print("\nAll good. Next:  python run.py --mt5 " + symbol + " --timeframe D1")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()

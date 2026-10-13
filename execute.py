"""Trade the portfolio on your MT5 account: demo by default, real money only by explicit opt-in.

Each run: download data → compute today's target positions (25% trend / 75% long-only
blend by default) → compare with what the bot already holds → send only the trades
needed to get there.

    python execute.py                 # DRY RUN: shows the plan, sends nothing
    python execute.py --live          # actually send the orders (demo accounts only)
    python execute.py --status        # bot positions, P&L and account summary
    python execute.py --close-all --live   # close every position the bot opened

Real money (only after a convincing demo record):
    python execute.py --real --confirm-account 12345678 --live
  --real alone is not enough: --confirm-account must match the logged-in account number,
  so the bot can never trade a real account by accident (e.g. if you log MT5 into it).
  Real mode defaults to HALF the risk (5% target volatility) unless you set --target-vol.

Safety
  * Real-money accounts are refused unless --real and the matching --confirm-account are given.
  * Kill switch: if equity falls more than --max-drawdown (default 15%) below its peak since the
    bot started on this account, it closes all its positions and writes output/HALTED.
    It will not trade again until you delete that file.
  * Only touches positions tagged with this bot's magic number; your manual trades are left alone.
  * Skips trades that are tiny (rebalance band), that the market won't accept right now
    (closed, disabled), or that would use too much margin.
  * Every order and every run is logged to output/trade_log.csv and output/account_log.csv.
"""
import argparse, json, os, time
from datetime import datetime
import numpy as np
import pandas as pd

import data
import alerts
import portfolio as pf
import symbols as symmap

MAGIC = 260826           # tags this bot's orders and positions
COMMENT = "tradebot"
DEVIATION = 20           # max slippage in points
OUT = "output"
ACTIONS = []             # orders sent during this run, for the dashboard
SETTINGS_FILE = "settings.json"
RISK_PRESETS = {   # target_vol = yearly swing size; max_drawdown = safety-switch level
    "cautious":   {"target_vol": 0.05, "max_drawdown": 0.10},
    "normal":     {"target_vol": 0.10, "max_drawdown": 0.15},
    "aggressive": {"target_vol": 0.15, "max_drawdown": 0.25},
}


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


SPEED_LABEL = {8: "short-term (weeks)", 16: "medium-term (1–2 months)",
               32: "longer-term (2–4 months)", 64: "long-term (3–12 months)"}


# ---------------------------------------------------------------- account & symbols
def connect(real=False, confirm=None):
    """Connect to MT5 and check the account is one we are allowed to trade."""
    mt5 = data.mt5_connect()
    acc = mt5.account_info()
    if acc is None:
        mt5.shutdown(); raise SystemExit("No account logged in to MT5.")
    is_demo = acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    if not is_demo:
        if not real:
            mt5.shutdown()
            raise SystemExit(f"REFUSED: account {acc.login} is a REAL-MONEY account. "
                             "Use --real --confirm-account <number> only if you mean it.")
        if str(confirm) != str(acc.login):
            mt5.shutdown()
            raise SystemExit(f"REFUSED: --confirm-account {confirm} does not match the logged-in "
                             f"real account {acc.login}.")
        print(f"⚠ REAL-MONEY account {acc.login} on {acc.server}: orders use real funds.")
    elif real:
        print("Note: --real given, but this is a demo account. Trading it as demo.")
    if not mt5.terminal_info().trade_allowed:
        print("⚠ Algo Trading is OFF in MT5 (toolbar button). Orders will be rejected until it's on.")
    return mt5, acc, is_demo


STOP_DAILY_MOVES = 5     # crash-guard distance, in "normal daily moves" of each market
COOLDOWN_DAYS = 3        # after a stop is hit, wait this long before buying back in


def stopped_recently(mt5, symbol, days=COOLDOWN_DAYS):
    """Date the bot's position in `symbol` was last closed by its stop-loss, if within `days`."""
    try:
        from datetime import timedelta
        deals = mt5.history_deals_get(datetime.now() - timedelta(days=days), datetime.now()) or []
    except Exception:
        return None
    sl = getattr(mt5, "DEAL_REASON_SL", 4)
    hits = [d for d in deals if getattr(d, "magic", None) == MAGIC and d.symbol == symbol
            and getattr(d, "reason", None) == sl]
    return datetime.fromtimestamp(max(d.time for d in hits)) if hits else None


def protect_positions(mt5, sig, dry, k=STOP_DAILY_MOVES):
    """Crash guard: put a stop-loss about k normal daily moves from today's price on every bot
    position, held by the broker so it works even while the bot isn't running. Reset each run."""
    for p in bot_positions(mt5):
        vol = (sig.get(p.symbol) or {}).get("vol")
        info, tick = mt5.symbol_info(p.symbol), mt5.symbol_info_tick(p.symbol)
        if not vol or vol != vol or info is None or tick is None or not tick.bid:
            continue
        price = tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask
        dist = k * price * vol / np.sqrt(260)
        min_dist = (getattr(info, "trade_stops_level", 0) or 0) * (getattr(info, "point", 0) or 0) * 1.5
        dist = max(dist, min_dist)
        sl = round(price - dist if p.type == mt5.POSITION_TYPE_BUY else price + dist, info.digits)
        if p.sl and abs(p.sl - sl) < 0.1 * dist:      # already about right: don't spam the broker
            continue
        if dry:
            print(f"    [dry run] safety stop {p.symbol} → {sl:g}")
            continue
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": p.ticket,
               "sl": sl, "tp": p.tp or 0.0, "magic": MAGIC}
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            why = explain_code(res.retcode, res.comment) if res else str(mt5.last_error())
            print(f"    safety stop {p.symbol}: not set ({why})")


def kill_switch(mt5, acc, max_dd, dry):
    """Returns True (and flattens the book) if equity has fallen too far from its peak."""
    halt_file = os.path.join(OUT, "HALTED")
    if os.path.exists(halt_file):
        print(f"HALTED: {open(halt_file).read().strip()}\nDelete {halt_file} to allow trading again.")
        return True
    path = os.path.join(OUT, "account_log.csv")
    if not os.path.exists(path):
        return False
    log = pd.read_csv(path)
    log = log[log.get("login", pd.Series(dtype=str)).astype(str) == str(acc.login)] if "login" in log else log
    if log.empty:
        return False
    peak = max(log.equity.max(), acc.equity)
    dd = acc.equity / peak - 1
    print(f"Drawdown from peak equity {peak:,.2f}: {dd:.1%} (kill switch at -{max_dd:.0%})")
    if dd > -max_dd:
        return False
    reason = (f"{datetime.now():%Y-%m-%d %H:%M} equity {acc.equity:,.2f} is {dd:.1%} below peak {peak:,.2f} "
              f"on account {acc.login}")
    print(f"KILL SWITCH: {reason}. Closing all bot positions.")
    close_all(mt5, dry)
    if not dry:
        alerts.send("Trading bot: safety switch triggered",
                    f"The account fell {-dd:.1%} below its peak, so the bot closed all its positions and "
                    f"stopped. Open the control panel and press Resume when you're ready.",
                    priority="urgent", tags="rotating_light")
        with open(halt_file, "w") as f:
            f.write(reason)
        log_account(mt5, "KILL SWITCH")
    return True


LOT_WARNINGS = {}   # symbol -> note, when the broker reports a lot's value inconsistently


def _to_account_ccy(mt5, amount, ccy):
    """Convert an amount in currency `ccy` to the account currency, if a rate is available."""
    acc = mt5.account_info().currency
    if not ccy or ccy == acc:
        return amount
    for sym, invert in ((ccy + acc, False), (acc + ccy, True)):
        if mt5.symbol_select(sym, True):
            t = mt5.symbol_info_tick(sym)
            if t and t.bid:
                mid = (t.bid + t.ask) / 2
                return amount / mid if invert else amount * mid
    return None


def lot_value(mt5, info, price):
    """Value of 1.0 lot in the account currency, cross-checked three ways:
    MT5's profit calculator, the tick value, and contract size x price.
    If they disagree by more than 3x, the LARGEST is used: that gives the smaller,
    safer position, and the market is flagged so it can be checked."""
    cands = {}
    try:
        pnl = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, info.name, 1.0, price, price * 1.01)
        if pnl and pnl > 0:
            cands["profit calculator"] = pnl / 0.01
    except Exception:
        pass
    if getattr(info, "trade_tick_size", 0) and getattr(info, "trade_tick_value", 0):
        cands["tick value"] = price * info.trade_tick_value / info.trade_tick_size
    if getattr(info, "trade_contract_size", 0):
        v = _to_account_ccy(mt5, price * info.trade_contract_size, getattr(info, "currency_profit", ""))
        if v and v > 0:
            cands["contract size"] = v
    if not cands:
        return price
    # 4th check: the margin for 1 lot can't be more than the lot is worth (that would be
    # leverage below 1:1). If it is, the lot's value is under-reported: use the margin
    # figure as the minimum true value, so one mis-described market can't distort the book.
    try:
        m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, info.name, 1.0, price)
        if m and m > 1.5 * max(cands.values()):
            cands["margin required"] = m
    except Exception:
        pass
    lo, hi = min(cands.values()), max(cands.values())
    if hi / lo > 3:
        LOT_WARNINGS[info.name] = ("the broker reports this market's lot size inconsistently ("
                                   + ", ".join(f"{k}: {v:,.0f}" for k, v in cands.items())
                                   + "); the bot uses the largest, safest value")
        return hi
    return cands.get("profit calculator", hi)


def tradable(mt5, symbol):
    """True if the account can open new positions in this symbol (prices alone aren't enough)."""
    if not mt5.symbol_select(symbol, True):
        return False
    info = mt5.symbol_info(symbol)
    full = getattr(mt5, "SYMBOL_TRADE_MODE_FULL", 4)
    return info is not None and info.trade_mode == full


def round_lots(lots, info):
    step = info.volume_step or 0.01
    v = np.floor(abs(lots) / step + 1e-9) * step
    if v < info.volume_min:
        return 0.0
    return float(np.sign(lots) * min(round(v, 8), info.volume_max))


def filling(mt5, info):
    flags = getattr(info, "filling_mode", 0)
    if flags & 1:
        return mt5.ORDER_FILLING_FOK
    if flags & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def bot_positions(mt5, symbol=None):
    pos = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    return [p for p in (pos or []) if p.magic == MAGIC]


def net_lots(positions, mt5):
    return sum(p.volume if p.type == mt5.POSITION_TYPE_BUY else -p.volume for p in positions)


# ---------------------------------------------------------------- orders
RETCODE_TEXT = {   # broker answers, in plain English (MT5 trade return codes)
    10004: "the price moved (requote)", 10006: "the broker rejected the order",
    10013: "the order was invalid", 10014: "the size is not allowed for this market",
    10015: "the price was invalid", 10016: "the stop levels were invalid",
    10017: "trading is disabled for this market", 10018: "the market is closed",
    10019: "not enough free margin", 10020: "prices changed", 10021: "no prices available right now",
    10024: "too many requests", 10027: "Algo Trading is switched off in MT5",
    10030: "this order fill type is not supported", 10031: "no connection to the broker",
}


def explain_code(code, comment=""):
    return RETCODE_TEXT.get(code, f"broker code {code}{': ' + comment if comment else ''}")


def _fillings(mt5, info):
    """Fill modes to try, the symbol's preferred one first."""
    first = filling(mt5, info)
    rest = [m for m in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN) if m != first]
    return [first] + rest


def precheck(mt5, req, info):
    """Ask the broker if this order would be accepted (order_check), BEFORE sending it.
    Tries other fill modes if the first one isn't supported. Returns (ok, message, request)."""
    for mode in _fillings(mt5, info):
        r = dict(req, type_filling=mode)
        try:
            chk = mt5.order_check(r)
        except Exception as e:
            return True, f"pre-check unavailable ({e})", req   # don't block trading on a missing check
        if chk is None:
            return True, "pre-check unavailable", req
        if chk.retcode in (0, mt5.TRADE_RETCODE_DONE):
            return True, "", r
        if chk.retcode != 10030:      # anything other than "fill type not supported" is final
            return False, explain_code(chk.retcode, getattr(chk, "comment", "")), r
    return False, explain_code(10030), req


def send(mt5, symbol, lots, info, dry, position=None, reason=""):
    """lots > 0 buys, < 0 sells. With `position`, the order closes (part of) that position.
    Every order is pre-checked with the broker (order_check) first, in dry runs too."""
    tick = mt5.symbol_info_tick(symbol)
    side = mt5.ORDER_TYPE_BUY if lots > 0 else mt5.ORDER_TYPE_SELL
    price = tick.ask if lots > 0 else tick.bid
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": abs(lots), "type": side,
           "price": price, "deviation": DEVIATION, "magic": MAGIC, "comment": COMMENT,
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling(mt5, info)}
    if position is not None:
        req["position"] = position
    action = ("CLOSE " if position is not None else "") + ("BUY" if lots > 0 else "SELL")
    # Estimated spread cost of this order: half the bid/ask gap, valued in account currency.
    mid = (tick.ask + tick.bid) / 2
    spread_cost = abs(lots) * lot_value(mt5, info, mid) * (tick.ask - tick.bid) / mid / 2 if mid else 0.0

    ok_check, why, req = precheck(mt5, req, info)
    if not ok_check:
        msg = f"not sent: {why}"
        print(f"    {'[dry run] ' if dry else ''}{action} {abs(lots):g} {symbol} → {msg}")
        if not dry:
            log_trade(symbol, action, abs(lots), price, False, msg, reason, 0.0)
        ACTIONS.append({"symbol": symbol, "ok": False, "msg": msg})
        return False
    if dry:
        print(f"    [dry run] {action} {abs(lots):g} {symbol} @ ~{price:g}  {reason}  (broker pre-check OK)")
        ACTIONS.append({"symbol": symbol, "ok": True, "msg": "dry run"})
        return True
    res = mt5.order_send(req)
    ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
    msg = "done" if ok else (f"FAILED: {explain_code(res.retcode, res.comment)}" if res
                             else f"FAILED: {mt5.last_error()}")
    fill = getattr(res, "price", price) or price
    print(f"    {action} {abs(lots):g} {symbol} @ {fill:g}  → {msg}")
    log_trade(symbol, action, abs(lots), fill, ok, msg, reason, round(spread_cost, 2) if ok else 0.0)
    ACTIONS.append({"symbol": symbol, "ok": ok, "msg": msg})
    return ok


def plan_orders(positions, target, info, mt5):
    """Orders that move the bot's net position to `target` lots on a hedging account.
    Returns a list of (signed_lots, ticket_or_None); a ticket means 'close (part of) this position'."""
    net = round(net_lots(positions, mt5), 8)
    orders = []
    flip = net != 0 and (target == 0 or np.sign(target) != np.sign(net))
    to_reduce = abs(net) if flip else max(0.0, abs(net) - abs(target))
    for p in sorted(positions, key=lambda p: p.volume):          # close smallest first
        if to_reduce < info.volume_min / 2:
            break
        v = round_lots(min(p.volume, to_reduce), info)
        if v == 0:
            continue
        sign_p = 1 if p.type == mt5.POSITION_TYPE_BUY else -1
        orders.append((-sign_p * v, p.ticket))
        to_reduce -= v
        net -= sign_p * v
    add = round_lots(target - net, info)
    if add != 0 and np.sign(add) == np.sign(target):
        orders.append((add, None))
    return orders


def rebalance_symbol(mt5, symbol, target, info, dry, band):
    """Move the bot's net position in `symbol` to `target` lots, unless the change is tiny."""
    positions = bot_positions(mt5, symbol)
    net = round(net_lots(positions, mt5), 8)
    delta = target - net
    if abs(delta) < 1e-9:
        return "hold"
    flip = net != 0 and (target == 0 or np.sign(target) != np.sign(net))
    if not flip and abs(delta) < max(info.volume_min, band * abs(target)):
        return f"hold (change {delta:+g} lots is inside the {band:.0%} rebalance band)"
    orders = plan_orders(positions, target, info, mt5)
    for lots, ticket in orders:
        send(mt5, symbol, lots, info, dry, position=ticket,
             reason="reduce/close" if ticket is not None else "open/increase")
    return "traded" if orders else "hold (change smaller than one lot step)"


# ---------------------------------------------------------------- logging
def log_trade(symbol, action, lots, price, ok, msg, reason, spread_cost=0.0):
    _append(os.path.join(OUT, "trade_log.csv"), {
        "time": datetime.now().isoformat(timespec="seconds"), "symbol": symbol, "action": action,
        "lots": lots, "price": price, "ok": ok, "result": msg, "reason": reason,
        "spread_cost": spread_cost})


def log_account(mt5, note):
    acc = mt5.account_info()
    pos = bot_positions(mt5)
    _append(os.path.join(OUT, "account_log.csv"), {
        "time": datetime.now().isoformat(timespec="seconds"), "login": acc.login,
        "demo": acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO, "balance": acc.balance,
        "equity": acc.equity, "margin": acc.margin, "free_margin": acc.margin_free,
        "bot_positions": len(pos), "bot_open_pnl": round(sum(p.profit for p in pos), 2), "note": note})


def _append(path, row):
    os.makedirs(OUT, exist_ok=True)
    new = pd.DataFrame([row])
    if os.path.exists(path):
        try:
            cols = pd.read_csv(path, nrows=0).columns.tolist()
        except Exception:
            cols = []
        if cols and cols != new.columns.tolist():   # log written by an older version: rewrite with all columns
            pd.concat([pd.read_csv(path), new], ignore_index=True).to_csv(path, index=False)
            return
    new.to_csv(path, mode="a", header=not os.path.exists(path), index=False)


# ---------------------------------------------------------------- commands
def status(mt5, acc):
    pos = bot_positions(mt5)
    kind = "DEMO" if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL"
    print(f"Account {acc.login} [{kind}]  balance {acc.balance:,.2f}  equity {acc.equity:,.2f} {acc.currency}  "
          f"margin used {acc.margin:,.2f}  free {acc.margin_free:,.2f}")
    if not pos:
        print("The bot has no open positions."); return
    rows = [{"symbol": p.symbol, "side": "LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT",
             "lots": p.volume, "open": p.price_open, "now": p.price_current,
             "swap": p.swap, "pnl": p.profit} for p in pos]
    df = pd.DataFrame(rows).sort_values("pnl")
    print(df.to_string(index=False))
    print(f"\nBot open P&L {df.pnl.sum():+,.2f}  (of which swap/rollover {df.swap.sum():+,.2f}) on {len(df)} positions")
    if os.path.exists(os.path.join(OUT, "account_log.csv")):
        log = pd.read_csv(os.path.join(OUT, "account_log.csv"))
        print(f"Equity when logging began: {log.equity.iloc[0]:,.2f}  →  now {acc.equity:,.2f}")


def close_all(mt5, dry):
    pos = bot_positions(mt5)
    print(f"Closing {len(pos)} bot positions{' (dry run)' if dry else ''}…")
    for p in pos:
        info = mt5.symbol_info(p.symbol)
        send(mt5, p.symbol, (-1 if p.type == mt5.POSITION_TYPE_BUY else 1) * p.volume, info, dry,
             position=p.ticket, reason="close-all")


def targets(a):
    """Compute target weights from COMPLETED daily bars only."""
    profile = a.profile or load_settings().get("profile", "classic")
    if profile not in pf.PROFILES:
        profile = "classic"
    budget = pf.PROFILES[profile]["long_budget"]
    print(f"Strategy: {pf.PROFILES[profile]['label']}. "
          f"Computing targets ({a.mix:.0%} trend / {1 - a.mix:.0%} long-only)…")
    standard = a.symbols or [s for syms in pf.UNIVERSE.values() for s in syms]
    # Find each market under this broker's own name, and only build the portfolio from markets
    # this account can actually trade, so the risk of untradable ones goes to the rest.
    mt5, _, _ = connect(a.real, a.confirm_account)
    try:
        smap = symmap.resolve(mt5, standard, load_settings().get("symbol_map"))
        ok = [b for s, b in smap.items() if tradable(mt5, b)]
    finally:
        mt5.shutdown()
    for s, b in smap.items():
        pf.CLASS_OF[b] = pf.CLASS_OF.get(s, "Other")
        pf.STD[b] = s
    symmap.save_map(smap)
    renamed = [f"{s}→{b}" for s, b in smap.items() if s != b]
    if renamed:
        print(f"This broker's names: {', '.join(renamed)}")
    not_found = [s for s in standard if s not in smap]
    dropped = [s for s, b in smap.items() if b not in ok]
    if not_found:
        print(f"Not offered by this broker: {', '.join(not_found)}")
    if dropped:
        print(f"Not tradable on this account, left out: {', '.join(dropped)}")
    if budget and not any(pf.CLASS_OF.get(b) == "Indices" for b in ok):
        print("⚠ No stock indices are tradable on this account, so the balanced profile can only use "
              "bonds, gold and commodities. It needs a broker with tradable index CFDs to work as intended.")
    closes, skipped = pf.load_universe(ok, a.bars)
    closes = closes.iloc[:-1]  # drop the newest day: it is still forming
    kw = dict(target_vol=a.target_vol, max_gross=a.max_gross, max_weight=a.max_weight, cost_bps=0)
    tf = pf.build_portfolio(closes, **kw)
    lo = pf.build_portfolio(closes, long_only=True, class_budget=budget, **kw)
    w = pf.blend(tf, lo, a.mix, **kw)["weights"].iloc[-1]
    print(f"Signals as of {closes.index[-1].date()} close; {len(skipped)} markets unavailable.")
    return w, signal_details(closes, tf["vol"].iloc[-1])


def signal_details(closes, vol):
    """For each market: how many of the 4 trend signals point up, and whether one is about to switch."""
    px = closes.ffill()
    sd = px.pct_change(fill_method=None).ewm(span=60, min_periods=40).std().iloc[-1]
    out = {}
    for s in px:
        c = px[s].dropna()
        if len(c) < 4 * max(pf.SPEEDS) or pd.isna(sd.get(s)):
            continue
        gaps = {f: (pf.ema(c, f) - pf.ema(c, 4 * f)).iloc[-1] / (c.iloc[-1] * sd[s]) for f in pf.SPEEDS}
        near = [f for f in sorted(pf.SPEEDS, reverse=True) if abs(gaps[f]) < 0.25]
        out[s] = {"votes_up": int(sum(g > 0 for g in gaps.values())), "vol": float(vol.get(s, float("nan"))),
                  "near_flip": (f"its {SPEED_LABEL[near[0]]} trend signal is about to turn "
                                f"{'down' if gaps[near[0]] > 0 else 'up'}") if near else ""}
    return out


def explain(sym, action, cur, tgt, sig, prev):
    """One plain-English sentence on why the bot did what it did."""
    v, pv = sig.get("votes_up"), prev.get("votes_up")
    if action in ("held", "skipped", "failed"):
        return ""
    if action == "closed":
        return "Its trend turned down enough to cancel out the steady holding." if v is not None and v <= 1 \
            else "No longer part of the portfolio."
    if action == "flipped":
        return f"The trend reversed: {v} of 4 trend signals now point up."
    if action == "opened" or not prev:
        lean = " so it holds a bit extra" if v is not None and v >= 3 else \
            " so it holds less than usual" if v is not None and v <= 1 else ""
        return f"Part of the steady, spread-out portfolio; {v} of 4 trend signals point up{lean}."
    if pv is not None and v is not None and v != pv:
        return f"Trend signals changed from {pv} to {v} of 4 pointing up."
    pvol, vol = prev.get("vol"), sig.get("vol")
    if pvol and vol and abs(vol / pvol - 1) > 0.10:
        return ("The market got calmer, so a bigger position carries the same risk." if vol < pvol else
                "The market got jumpier, so a smaller position keeps risk the same.")
    return "Routine resize to keep each market's risk in balance as prices and the account value moved."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="really send orders. Default is a dry run.")
    ap.add_argument("--real", action="store_true", help="allow a REAL-MONEY account (needs --confirm-account)")
    ap.add_argument("--confirm-account", help="the real account number, typed out, as a second confirmation")
    ap.add_argument("--risk", choices=list(RISK_PRESETS),
                    help="risk level preset (default: the one saved from the dashboard, else normal)")
    ap.add_argument("--max-drawdown", type=float, default=None,
                    help="kill switch: close everything if equity falls this far below its peak (default from --risk)")
    ap.add_argument("--no-stops", action="store_true", help="don't place the broker-side crash-guard stops")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--close-all", action="store_true")
    ap.add_argument("--mix", type=float, default=0.25, help="share of risk in trend (default 25%%)")
    ap.add_argument("--profile", choices=list(pf.PROFILES),
                    help="classic or balanced (stocks + bonds). Default: the one saved from the panel, else classic")
    ap.add_argument("--target-vol", type=float, default=None,
                    help="annual volatility target (default from --risk; halved on a real account)")
    ap.add_argument("--max-gross", type=float, default=4.0)
    ap.add_argument("--max-weight", type=float, default=0.25)
    ap.add_argument("--band", type=float, default=0.10,
                    help="skip trades smaller than this fraction of the target position (default 10%%)")
    ap.add_argument("--max-margin", type=float, default=0.50,
                    help="never let used margin exceed this share of equity (default 50%%)")
    ap.add_argument("--capital", type=float, default=None,
                    help="size positions as if the account held this much (e.g. 1000), to see or trade a smaller amount")
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--bars", type=int, default=1500, help="daily bars per market (1500 ≈ 6 years is plenty)")
    a = ap.parse_args()
    dry = not a.live

    if a.status or a.close_all:
        mt5, acc, _ = connect(a.real, a.confirm_account)
        try:
            close_all(mt5, dry) if a.close_all else status(mt5, acc)
            if a.status:
                import dashboard
                print(f"\nDashboard: {dashboard.build(mt5)}  (open it in your browser)")
            if a.close_all and not dry:
                log_account(mt5, "close-all")
        finally:
            mt5.shutdown()
        return

    # Check the account BEFORE the slow data download, so a refusal is immediate.
    mt5, acc, is_demo = connect(a.real, a.confirm_account)
    mt5.shutdown()
    risk = a.risk or load_settings().get("risk", "normal")
    preset = RISK_PRESETS.get(risk, RISK_PRESETS["normal"])
    if a.target_vol is None:
        a.target_vol = preset["target_vol"] * (1 if is_demo else 0.5)
    if a.max_drawdown is None:
        a.max_drawdown = preset["max_drawdown"]
    print(f"Risk level: {risk} (target volatility {a.target_vol:.0%}, safety switch at -{a.max_drawdown:.0%})")
    w, sig = targets(a)
    prev_run = {}
    if os.path.exists(os.path.join(OUT, "last_run.json")):
        with open(os.path.join(OUT, "last_run.json"), encoding="utf-8") as f:
            prev_run = {d["symbol"]: d for d in json.load(f).get("decisions", [])}
    decisions = []
    mt5, acc, is_demo = connect(a.real, a.confirm_account)
    try:
        if kill_switch(mt5, acc, a.max_drawdown, dry):
            return
        equity = acc.equity
        cap_amt = a.capital or load_settings().get("capital")
        if cap_amt:
            equity = min(float(cap_amt), acc.equity)
            print(f"\nSizing positions for {equity:,.2f} {acc.currency} of capital (the account holds {acc.equity:,.2f}).")
        print(f"\nAccount {acc.login} [{'DEMO' if is_demo else 'REAL MONEY'}]  equity {equity:,.2f} {acc.currency}  "
              f"target vol {a.target_vol:.0%}  {'LIVE ORDERS' if not dry else 'DRY RUN: nothing will be sent'}\n")
        plan, lot_vals = [], {}
        for sym, weight in w.items():
            info = mt5.symbol_info(sym)
            tick = mt5.symbol_info_tick(sym)
            if info is None or tick is None or not tick.bid:
                plan.append((sym, weight, None, "no price / not tradable")); continue
            lv = lot_value(mt5, info, (tick.bid + tick.ask) / 2)
            plan.append((sym, weight, round_lots(weight * equity / lv, info), info))
            lot_vals[sym] = lv

        # Fit the whole plan inside the margin limit by shrinking every position by the same
        # factor, so the portfolio keeps its balance instead of the last markets being skipped.
        need = 0.0
        for sym, weight, tgt, info in plan:
            if tgt and not isinstance(info, str) and info is not None:
                t = mt5.symbol_info_tick(sym)
                try:
                    m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY if tgt > 0 else mt5.ORDER_TYPE_SELL,
                                              sym, abs(tgt), t.ask if tgt > 0 else t.bid)
                    need += m or 0.0
                except Exception:
                    pass
        cap = a.max_margin * equity
        if need > cap > 0:
            f = 0.95 * cap / need
            print(f"  Margin check: the full plan needs {need:,.0f} margin, above the {a.max_margin:.0%} limit "
                  f"({cap:,.0f}). Scaling every position to {f:.0%} of target so it fits.\n")
            plan = [(sym, weight * f, round_lots(weight * f * equity / lot_vals[sym], info), info)
                    if sym in lot_vals else (sym, weight, tgt, info) for sym, weight, tgt, info in plan]
        for sym, note in LOT_WARNINGS.items():
            print(f"  ⚠ {sym}: {note}.")
        tiny = [sym for sym, weight, tgt, info in plan
                if not isinstance(info, str) and info is not None and abs(weight) > 0.005 and tgt == 0]
        if tiny:
            print(f"  ℹ With {equity:,.0f} of capital, these positions are smaller than the broker's minimum trade "
                  f"size, so they're skipped: {', '.join(tiny)}\n")

        # Do reductions first so they free margin for new positions.
        def order_key(item):
            sym, _, tgt, info = item
            cur = net_lots(bot_positions(mt5, sym), mt5) if info is not None and not isinstance(info, str) else 0
            return 0 if tgt is not None and abs(tgt) < abs(cur) else 1
        plan.sort(key=order_key)

        def record(sym, weight, cur, tgt, action, detail=""):
            d = {"symbol": sym, "weight": float(weight), "lots_before": float(cur),
                 "lots_target": float(tgt or 0), "action": action, "detail": detail, **sig.get(sym, {})}
            d["why"] = explain(sym, action, cur, tgt or 0, sig.get(sym, {}), prev_run.get(sym, {}))
            decisions.append(d)

        for sym, weight, tgt, info in plan:
            if isinstance(info, str) or info is None:
                print(f"  {sym:10s} skip: {info}")
                record(sym, weight, 0, 0, "skipped", str(info)); continue
            cur = net_lots(bot_positions(mt5, sym), mt5)
            print(f"  {sym:10s} weight {weight:+6.1%}  target {tgt:+8g} lots  (now {cur:+g})"
                  f"   [1 lot ≈ {lot_vals[sym]:,.0f} {acc.currency}]")
            if not tradable(mt5, sym):
                print("    skip: trading not allowed for this symbol right now")
                record(sym, weight, cur, tgt, "skipped", "Trading this market isn't allowed right now."); continue
            hit = stopped_recently(mt5, sym) if abs(tgt) > abs(cur) and not a.no_stops else None
            if hit:
                print(f"    skip: safety stop hit on {hit:%d %b}; waiting {COOLDOWN_DAYS} days before buying back in")
                record(sym, weight, cur, tgt, "skipped",
                       f"Its safety stop was hit on {hit:%a %d %b}; waiting {COOLDOWN_DAYS} days before buying back in."); continue
            if abs(tgt) > abs(cur) and not dry:   # adding exposure: check margin first
                acc_now = mt5.account_info()
                if acc_now.margin > a.max_margin * acc_now.equity:
                    print(f"    skip: margin already {acc_now.margin / acc_now.equity:.0%} of equity "
                          f"(limit {a.max_margin:.0%})")
                    record(sym, weight, cur, tgt, "skipped", "Margin limit reached, so no new positions."); continue
            n_before = len(ACTIONS)
            result = rebalance_symbol(mt5, sym, tgt, info, dry, a.band)
            if result != "traded":
                print(f"    {result}")
            sent = ACTIONS[n_before:]
            if not sent:
                action, detail = "held", ""
            elif any(not x["ok"] for x in sent):
                bad = next(x for x in sent if not x["ok"])["msg"]
                action = "failed"
                detail = ("The market was closed; it will be traded at the next check." if "closed" in bad.lower()
                          else f"The broker rejected the order: {bad}")
            elif dry:
                action, detail = "would trade", f"{cur:+g} → {tgt:+g} lots"
            elif cur == 0:
                action, detail = "opened", ""
            elif tgt == 0:
                action, detail = "closed", ""
            elif np.sign(tgt) != np.sign(cur):
                action, detail = "flipped", ""
            else:
                action, detail = ("increased" if abs(tgt) > abs(cur) else "trimmed"), ""
            record(sym, weight, cur, tgt, action, detail)
            if not dry:
                time.sleep(0.2)

        if not a.no_stops:
            protect_positions(mt5, sig, dry)

        # Positions the bot holds in markets that are no longer in the universe: close them.
        for p in bot_positions(mt5):
            if p.symbol not in w.index:
                send(mt5, p.symbol, (-1 if p.type == mt5.POSITION_TYPE_BUY else 1) * p.volume,
                     mt5.symbol_info(p.symbol), dry, position=p.ticket, reason="not in universe")

        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, "last_run.json"), "w", encoding="utf-8") as f:
            json.dump({"time": datetime.now().isoformat(timespec="seconds"), "dry": dry,
                       "decisions": decisions}, f, indent=1, default=float)
        # Daily signal history (one row per market per run) for the shareable signal report.
        hist = pd.DataFrame([{"time": datetime.now().isoformat(timespec="seconds"), "symbol": d["symbol"],
                              "votes_up": d.get("votes_up"), "vol": d.get("vol"), "weight": d["weight"],
                              "near_flip": d.get("near_flip", "")} for d in decisions])
        hp = os.path.join(OUT, "signal_history.csv")
        hist.to_csv(hp, mode="a", header=not os.path.exists(hp), index=False)
        if not dry:
            log_account(mt5, "rebalance")
            changed = [d for d in decisions if d["action"] in ("opened", "increased", "trimmed", "closed", "flipped")]
            failed = [d for d in decisions if d["action"] == "failed" and "closed" not in d.get("detail", "").lower()]
            acc_now = mt5.account_info()
            msg = (f"Account ${acc_now.equity:,.2f}. {len(changed)} change{'s' if len(changed) != 1 else ''}"
                   + (": " + ", ".join(f"{d['action']} {d['symbol']}" for d in changed[:6]) if changed else "") + ".")
            if failed:
                alerts.send("Trading bot: some orders failed",
                            msg + " Problems: " + "; ".join(f"{d['symbol']}: {d['detail']}" for d in failed[:5]),
                            priority="high", tags="warning")
            elif load_settings().get("daily_summary", True):
                alerts.send("Trading bot: daily run done", msg, priority="low", tags="white_check_mark")
        try:
            import dashboard
            print(f"Dashboard updated: {dashboard.build(mt5)}")
        except Exception as e:  # the dashboard must never stop the trading run
            print(f"(dashboard not updated: {e})")
        if not dry:
            acc = mt5.account_info()
            print(f"\nDone. Equity {acc.equity:,.2f}  margin used {acc.margin:,.2f} "
                  f"({acc.margin / acc.equity:.0%} of equity). Log: output\\trade_log.csv")
        else:
            print("\nDry run finished. Run with --live to send these orders to the demo account.")
    finally:
        mt5.shutdown()


def run():
    """main() plus alerts: tell the phone if a live run fails or finishes."""
    import sys
    live = "--live" in sys.argv and "--close-all" not in sys.argv
    try:
        main()
    except SystemExit as e:
        if live and e.code not in (None, 0):
            alerts.send("Trading bot: run stopped", str(e.code)[:400], priority="high", tags="warning")
        raise
    except Exception as e:
        if live:
            alerts.send("Trading bot: run failed", f"{type(e).__name__}: {e}"[:400], priority="high", tags="x")
        raise


if __name__ == "__main__":
    run()

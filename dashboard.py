"""Plain-English dashboard for the trading bot.

    python dashboard.py            # refresh output/dashboard.html and open it in your browser

execute.py also refreshes it automatically after every run. It reads (never trades):
the MT5 account and the bot's open positions, plus output/last_run.json,
output/account_log.csv and output/trade_log.csv.
"""
import html, json, os, webbrowser
from datetime import datetime
import pandas as pd

OUT = "output"
MAGIC = 260826

NAMES = {
    "EURUSD": "Euro vs US dollar", "GBPUSD": "British pound vs US dollar", "USDJPY": "US dollar vs Japanese yen",
    "USDCHF": "US dollar vs Swiss franc", "AUDUSD": "Australian dollar vs US dollar",
    "NZDUSD": "New Zealand dollar vs US dollar", "USDCAD": "US dollar vs Canadian dollar",
    "EURJPY": "Euro vs Japanese yen", "GBPJPY": "British pound vs Japanese yen",
    "AUDJPY": "Australian dollar vs Japanese yen", "EURGBP": "Euro vs British pound", "EURCHF": "Euro vs Swiss franc",
    "XAUUSD": "Gold", "XAGUSD": "Silver", "XPTUSD": "Platinum", "XPDUSD": "Palladium",
    "US500": "US stocks (S&P 500)", "USTEC": "US tech stocks (Nasdaq 100)", "US30": "US stocks (Dow Jones)",
    "US2000": "US small companies (Russell 2000)", "DE40": "German stocks (DAX)", "UK100": "UK stocks (FTSE 100)",
    "FRA40": "French stocks (CAC 40)", "EUSTX50": "European stocks (Euro Stoxx 50)", "JPN225": "Japanese stocks (Nikkei)",
    "AUS200": "Australian stocks (ASX 200)", "HK50": "Hong Kong stocks (Hang Seng)", "CHINA50": "Chinese stocks (China A50)",
    "TLT": "US long-term government bonds", "IEF": "US 7–10 year government bonds",
    "TIP": "US inflation-protected bonds", "LQD": "US corporate bonds", "USO": "Crude oil",
    "DBC": "Basket of commodities", "CPER": "Copper", "CORN": "Corn", "WEAT": "Wheat",
    "UNG": "Natural gas", "DBA": "Farm commodities",
}


def name(sym):
    import symbols   # broker names like "GER40.cash" -> "DE40" -> "German stocks (DAX)"
    return NAMES.get(sym) or NAMES.get(symbols.standard(sym), sym)


def money(x, cur="USD", sign=False):
    s = f"{abs(x):,.2f}"
    pre = ("+" if x > 0 else "−" if x < 0 else "") if sign else ("−" if x < 0 else "")
    return f"{pre}${s}" if cur == "USD" else f"{pre}{s} {cur}"


def votes_html(n):
    if n is None:
        return ""
    return f"<span class='votes' title='{n} of 4 trend signals point up'>" + \
        "".join("<i class='up'></i>" if i < n else "<i></i>" for i in range(4)) + "</span>"


def _read_csv(path):
    try:
        return pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


STATIC_NOTE = ("<div class='card note'>This is a saved snapshot. For buttons (risk level, preview, trade now, "
               "auto-trading), double-click <b>Start bot.bat</b> or run <code>python app.py</code>.</div>")


def build(mt5=None, open_browser=False):
    """Write the static snapshot output/dashboard.html (no buttons)."""
    page = render(mt5, STATIC_NOTE)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    if open_browser:
        webbrowser.open("file://" + os.path.abspath(path))
    return path


def _account_log(login):
    acct = _read_csv(os.path.join(OUT, "account_log.csv"))
    if not acct.empty and "login" in acct:
        acct = acct[acct.login.astype(str) == str(login)]
    return acct


def _headline(eq, pnls, acct):
    """The four headline numbers, from current equity, per-market open P/L and the account log."""
    start_eq = float(acct.equity.iloc[0]) if not acct.empty else eq
    today = datetime.now().date()
    prev_days = acct[pd.to_datetime(acct.time).dt.date < today] if not acct.empty else acct
    prev_eq = float(prev_days.equity.iloc[-1]) if not prev_days.empty else None
    return {"eq": eq, "start_eq": start_eq, "since": eq - start_eq,
            "day_chg": eq - prev_eq if prev_eq is not None else None,
            "open_pnl": float(pnls.sum()) if len(pnls) else 0.0,
            "n_up": int((pnls > 0).sum()), "n_dn": int((pnls < 0).sum())}


def _tone(x):
    return "pos" if (x or 0) > 0 else "neg" if (x or 0) < 0 else ""


def live(mt5=None):
    """Just the numbers that move with prices, for the control panel's once-a-minute refresh."""
    import data
    own = mt5 is None
    if own:
        mt5 = data.mt5_connect()
    try:
        acc = mt5.account_info()
        pnl = {}
        for p in (mt5.positions_get() or []):
            if p.magic == MAGIC:
                pnl[p.symbol] = pnl.get(p.symbol, 0.0) + p.profit
    finally:
        if own:
            mt5.shutdown()
    cur = acc.currency
    h = _headline(acc.equity, pd.Series(pnl, dtype=float), _account_log(acc.login))
    return {
        "time": datetime.now().strftime("%a %d %b %Y, %H:%M:%S"),
        "equity": money(h["eq"], cur),
        "since": money(h["since"], cur, sign=True), "since_cls": _tone(h["since"]),
        "since_pct": f"{h['since'] / h['start_eq']:+.2%}" if h["start_eq"] else "",
        "day": money(h["day_chg"], cur, sign=True) if h["day_chg"] is not None else "n/a yet",
        "day_cls": _tone(h["day_chg"]),
        "open_pnl": money(h["open_pnl"], cur, sign=True), "open_cls": _tone(h["open_pnl"]),
        "n_up": h["n_up"], "n_dn": h["n_dn"],
        "positions": {s: {"pnl": money(v, cur, sign=True), "cls": _tone(v)} for s, v in pnl.items()},
    }


def render(mt5=None, controls=""):
    """Return the dashboard page as HTML. `controls` is inserted near the top (used by app.py)."""
    import data
    own = mt5 is None
    if own:
        mt5 = data.mt5_connect()
    try:
        acc = mt5.account_info()
        positions = [p for p in (mt5.positions_get() or []) if p.magic == MAGIC]
        rows = []
        for p in positions:
            info = mt5.symbol_info(p.symbol)
            try:
                from execute import lot_value
                lot_val = lot_value(mt5, info, p.price_current)
            except Exception:
                lot_val = info.trade_contract_size * p.price_current
            rows.append({"symbol": p.symbol, "long": p.type == mt5.POSITION_TYPE_BUY, "lots": p.volume,
                         "value": p.volume * lot_val, "pnl": p.profit, "swap": p.swap,
                         "open": p.price_open, "now": p.price_current, "time": p.time,
                         "sl": getattr(p, "sl", 0.0) or 0.0})
        deals = []
        try:   # the bot's own deals (book ch. 7.9.15: history_deals_get), for real costs
            got = mt5.history_deals_get(datetime(2000, 1, 1), datetime.now()) or []
            deals = [d for d in got if getattr(d, "magic", None) == MAGIC]
        except Exception:
            pass
    finally:
        if own:
            mt5.shutdown()

    pos = pd.DataFrame(rows)
    if not pos.empty:   # a market can have several positions on a hedging account: combine them
        pos["signed_value"] = pos.value.where(pos.long, -pos.value)
        pos = (pos.groupby("symbol").agg(signed_value=("signed_value", "sum"), pnl=("pnl", "sum"),
                                         swap=("swap", "sum"), lots=("lots", "sum"), first=("time", "min"),
                                         sl=("sl", "max"), now=("now", "last"))
                  .reset_index())
        pos["long"] = pos.signed_value > 0
    run = {}
    if os.path.exists(os.path.join(OUT, "last_run.json")):
        with open(os.path.join(OUT, "last_run.json"), encoding="utf-8") as f:
            run = json.load(f)
    dec = {d["symbol"]: d for d in run.get("decisions", [])}
    acct = _account_log(acc.login)
    halted = open(os.path.join(OUT, "HALTED")).read().strip() if os.path.exists(os.path.join(OUT, "HALTED")) else ""

    # ---- what trading has really cost (book ch. 7.9.15)
    commission = sum(getattr(d, "commission", 0.0) + getattr(d, "fee", 0.0) for d in deals)
    swap_closed = sum(getattr(d, "swap", 0.0) for d in deals)
    swap_open = float(sum(r["swap"] for r in rows))
    tl_all = _read_csv(os.path.join(OUT, "trade_log.csv"))
    spread = float(pd.to_numeric(tl_all.get("spread_cost", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) \
        if not tl_all.empty else 0.0
    total_cost = -commission - swap_closed - swap_open + spread   # positive = money paid
    first_t = pd.to_datetime(acct.time).min() if not acct.empty else None
    days_live = max(1, (datetime.now() - first_t).days) if first_t is not None else None

    # ---- headline numbers
    h = _headline(acc.equity, pos.pnl if not pos.empty else pd.Series(dtype=float), acct)
    eq, start_eq, since, day_chg = h["eq"], h["start_eq"], h["since"], h["day_chg"]
    open_pnl, n_up, n_dn = h["open_pnl"], h["n_up"], h["n_dn"]
    demo = acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    cur = acc.currency

    changes = [d for d in run.get("decisions", []) if d.get("action") not in ("held", None)]
    summary = (f"The bot holds <b>{len(pos)}</b> positions: <b class='pos'><span id='k_up'>{n_up}</span> up</b> and "
               f"<b class='neg'><span id='k_dn'>{n_dn}</span> down</b> right now. ")
    if run:
        when = datetime.fromisoformat(run["time"]).strftime("%a %d %b, %H:%M")
        summary += (f"Its last check was {when}{' (a dry run: nothing was traded)' if run.get('dry') else ''}, "
                    f"when it made <b>{len(changes)}</b> change{'s' if len(changes) != 1 else ''}.")

    # ---- today's changes, in words
    verbs = {"opened": "Bought into", "increased": "Added to", "trimmed": "Trimmed", "closed": "Closed",
             "flipped": "Switched direction on", "failed": "Tried to trade", "skipped": "Skipped",
             "would trade": "Would trade"}
    ch_rows = "".join(
        f"<tr><td><b>{verbs.get(d['action'], d['action'].title())}</b> {html.escape(name(d['symbol']))}"
        f"<div class='sub'>{d['symbol']}</div></td><td>{html.escape(d.get('why', ''))}"
        f"{'<div class=sub>' + html.escape(d['detail']) + '</div>' if d.get('detail') else ''}</td></tr>"
        for d in changes) or "<tr><td colspan=2 class='muted'>No changes at the last check. Everything was already where it should be.</td></tr>"

    # ---- positions, in words
    def pos_row(r):
        d = dec.get(r.symbol, {})
        side = ("<span class='pill long'>BUY</span>", "profits if it rises") if r.long else \
               ("<span class='pill short'>SELL</span>", "profits if it falls")
        tone = "pos" if r.pnl > 0 else "neg" if r.pnl < 0 else ""
        held = max(0, (datetime.now() - datetime.fromtimestamp(r["first"])).days)   # MT5 times run on the server clock
        flag = "<div class='warn-t'>Trend could change soon</div>" if d.get("near_flip") else ""
        if r.get("sl"):
            gap = abs(r.sl / r.now - 1) if r.now else 0
            flag += f"<div class='sub'>Safety stop at {r.sl:g} ({gap:.1%} away)</div>"
        return (f"<tr><td><b>{html.escape(name(r.symbol))}</b><div class='sub'>{r.symbol} · held {held} day{'s' if held != 1 else ''}</div></td>"
                f"<td>{side[0]}<div class='sub'>{side[1]}</div></td>"
                f"<td>{money(abs(r.signed_value), cur)}<div class='sub'>{r.lots:g} lots</div></td>"
                f"<td class='{tone}' data-pnl='{html.escape(r.symbol)}'>{money(r.pnl, cur, sign=True)}</td>"
                f"<td>{votes_html(d.get('votes_up'))}{flag}</td></tr>")
    pos_rows = "".join(pos_row(r) for _, r in pos.sort_values("pnl", ascending=False).iterrows()) if not pos.empty \
        else "<tr><td colspan=5 class='muted'>No open positions yet.</td></tr>"

    # ---- watch list: markets whose trend is close to changing
    watch = [d for d in run.get("decisions", []) if d.get("near_flip")]
    watch_rows = "".join(f"<li><b>{html.escape(name(d['symbol']))}</b>: {html.escape(d['near_flip'])}</li>"
                         for d in watch) or "<li class='muted'>Nothing close to changing direction.</li>"

    # ---- recent orders
    tl = _read_csv(os.path.join(OUT, "trade_log.csv"))
    rec = ""
    if not tl.empty:
        for _, t in tl.tail(15).iloc[::-1].iterrows():
            ok = "✔" if str(t.ok) == "True" else "✘"
            act = {"BUY": "Bought", "SELL": "Sold short", "CLOSE SELL": "Sold (reducing a buy)",
                   "CLOSE BUY": "Bought back (reducing a sell)"}.get(str(t.action), str(t.action))
            rec += (f"<tr><td>{str(t.time).replace('T', ' ')[:16]}</td><td>{ok} {html.escape(act)} "
                    f"{t.lots:g} {html.escape(name(t.symbol))}</td><td class='sub'>{html.escape(str(t.result))}</td></tr>")
    rec = rec or "<tr><td colspan=3 class='muted'>No orders yet.</td></tr>"

    # ---- equity chart
    pts = [] if acct.empty else [{"t": str(r.time)[:16].replace("T", " "), "v": round(float(r.equity), 2)}
                                 for _, r in acct.iterrows()]
    pts.append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"), "v": round(eq, 2)})

    def cost_line(label, amount, note):
        tone = "neg" if amount > 0.005 else "pos" if amount < -0.005 else ""
        shown = money(-amount, cur, sign=True) if abs(amount) > 0.005 else money(0, cur)
        return f"<tr><td>{label}<div class='sub'>{note}</div></td><td class='{tone}'>{shown}</td></tr>"
    cost_rows = (cost_line("Spread", spread, "the gap between buy and sell prices, paid on every trade (estimated)")
                 + cost_line("Commissions and fees", -commission, "charged by the broker per trade")
                 + cost_line("Overnight swap", -(swap_closed + swap_open),
                             "daily interest for holding positions overnight (can be a credit)")
                 + f"<tr><td><b>Total</b></td><td class='{'neg' if total_cost > 0.005 else 'pos'}'><b>"
                   f"{money(-total_cost, cur, sign=True)}</b></td></tr>")
    if days_live and start_eq and days_live < 30:
        cost_note = (f"Total so far: {abs(total_cost) / start_eq:.2%} of the starting balance. A yearly figure appears "
                     f"after 30 days. Before that it would be misleading, because opening the positions costs much "
                     f"more than holding them. For comparison, the backtest's expected gain was about 2–3% a year.")
    elif days_live and start_eq:
        yearly = total_cost / start_eq * 365 / days_live
        cost_note = (f"Over {days_live} day{'s' if days_live != 1 else ''}, that's about {abs(yearly):.2%} of the account "
                     f"per year {'in costs' if total_cost >= 0 else 'in net credits'}. The backtest's expected gain was only about 2–3% a year, "
                     f"so costs much above 1% a year would eat most of it. Early on this number jumps around, because "
                     f"opening the positions costs more than holding them.")
    else:
        cost_note = "Costs appear here after the first trades."

    banner = ""
    if halted:
        banner = (f"<div class='alert'><b>Trading is stopped by the safety switch.</b> {html.escape(halted)}. "
                  f"Delete the file output\\HALTED to allow trading again.</div>")
    page = PAGE.format(
        kind="Practice account (demo)" if demo else "REAL MONEY account", kind_cls="demo" if demo else "real",
        login=acc.login, now=datetime.now().strftime("%a %d %b %Y, %H:%M"), banner=banner + controls,
        equity=money(eq, cur), since=money(since, cur, sign=True), since_pct=f"{since / start_eq:+.2%}" if start_eq else "",
        since_cls="pos" if since > 0 else "neg" if since < 0 else "",
        day=money(day_chg, cur, sign=True) if day_chg is not None else "n/a yet",
        day_cls="pos" if (day_chg or 0) > 0 else "neg" if (day_chg or 0) < 0 else "",
        open_pnl=money(open_pnl, cur, sign=True), open_cls="pos" if open_pnl > 0 else "neg" if open_pnl < 0 else "",
        start=money(start_eq, cur), summary=summary, ch_rows=ch_rows, pos_rows=pos_rows, watch_rows=watch_rows,
        rec=rec, points=json.dumps(pts), npts=len(pts), cost_rows=cost_rows, cost_note=cost_note)
    return page


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#0b0b0b;--ink2:#5d5c58;--line:#e6e5e0;--accent:#2a78d6;--good:#0b7a35;--bad:#c4302b;--goodbg:#0b7a3514;--badbg:#c4302b12;--warn:#a46a00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1e;--ink:#f3f2ee;--ink2:#b5b4ad;--line:#33332f;--accent:#3987e5;--good:#4cc27a;--bad:#ec6b66;--goodbg:#4cc27a1c;--badbg:#ec6b661c;--warn:#e0a83a}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:auto;padding:22px 16px 40px}}
header{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px;margin-bottom:14px}}
h1{{font-size:22px;margin:0}} h2{{font-size:16.5px;margin:0 0 10px}} .muted,.sub{{color:var(--ink2)}} .sub{{font-size:12.5px}}
.badge{{font-size:12px;font-weight:700;padding:3px 9px;border-radius:99px;margin-left:8px;vertical-align:middle}}
.badge.demo{{background:var(--goodbg);color:var(--good)}} .badge.real{{background:var(--badbg);color:var(--bad)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px;overflow-x:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}} @media(max-width:760px){{.kpis{{grid-template-columns:1fr 1fr}}}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.kpi .l{{font-size:12.5px;color:var(--ink2)}} .kpi .v{{font-size:24px;font-weight:700;margin-top:2px}} .kpi .s{{font-size:12.5px;color:var(--ink2)}}
.pos{{color:var(--good)}} .neg{{color:var(--bad)}}
.summary{{font-size:16.5px;margin:0 0 16px}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:12.5px;color:var(--ink2);font-weight:600}} tr:last-child td{{border-bottom:0}}
.pill{{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px;color:#fff}} .long{{background:var(--good)}} .short{{background:var(--bad)}}
.votes i{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:3px;background:var(--line)}} .votes i.up{{background:var(--accent)}}
.warn-t{{color:var(--warn);font-size:12.5px;margin-top:3px}}
.alert{{background:var(--badbg);border:1px solid var(--bad);border-radius:10px;padding:12px 14px;margin-bottom:16px}}
ul{{margin:0;padding-left:18px}} li{{margin:4px 0}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} @media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
details summary{{cursor:pointer;font-weight:600}} dl{{margin:10px 0 0}} dt{{font-weight:600;margin-top:8px}} dd{{margin:2px 0 0;color:var(--ink2)}}
canvas{{max-height:260px}} .note{{font-size:14px;color:var(--ink2)}} code{{font-size:13px}}
</style></head><body><main>
<header><h1>Trading bot <span class="badge {kind_cls}">{kind}</span></h1><span class="sub">Account {login} · updated <span id="k_upd">{now}</span></span></header>
{banner}
<div class="kpis">
 <div class="kpi"><div class="l">Account value now</div><div class="v" id="k_eq">{equity}</div><div class="s">started at {start}</div></div>
 <div class="kpi"><div class="l">Gain / loss since the bot started</div><div class="v {since_cls}" id="k_since">{since}</div><div class="s" id="k_since_pct">{since_pct}</div></div>
 <div class="kpi"><div class="l">Change since yesterday</div><div class="v {day_cls}" id="k_day">{day}</div><div class="s">vs the last check on an earlier day</div></div>
 <div class="kpi"><div class="l">Open trades right now</div><div class="v {open_cls}" id="k_open">{open_pnl}</div><div class="s">not locked in until closed</div></div>
</div>
<p class="summary">{summary}</p>
<div class="card"><h2>What it changed at the last check, and why</h2><table>{ch_rows}</table></div>
<div class="card"><h2>What it's holding</h2>
<table><tr><th>Market</th><th>Direction</th><th>Size</th><th>Profit / loss</th><th>Trend signals</th></tr>{pos_rows}</table>
<p class="sub" style="margin:10px 0 0">Trend signals: four checks, from about one week to one year, of whether the price is rising. Blue = rising. 4 blue is a strong uptrend, 0 blue a strong downtrend.</p></div>
<div class="grid">
 <div class="card"><h2>Account value over time</h2><canvas id="eq"></canvas><p class="sub" id="eqnote"></p></div>
 <div class="card"><h2>Markets to watch</h2><p class="sub" style="margin-top:0">Their trend is close to changing, so the bot may buy or sell them soon.</p><ul>{watch_rows}</ul></div>
</div>
<div class="card"><h2>What trading has cost so far</h2><table>{cost_rows}</table><p class="sub" style="margin:10px 0 0">{cost_note}</p></div>
<div class="card"><h2>Recent orders</h2><table><tr><th>When</th><th>Order</th><th>Result</th></tr>{rec}</table></div>
<div class="card"><details><summary>What do these words mean?</summary><dl>
<dt>Buy / Sell</dt><dd>Buy profits if the price rises; Sell profits if it falls.</dd>
<dt>Size</dt><dd>How much money's worth of the market the position controls. With leverage this can be more than the money it ties up.</dd>
<dt>Profit / loss on open trades</dt><dd>Changes every second with the price. It only becomes real ("locked in") when the trade is closed.</dd>
<dt>Lots</dt><dd>The broker's unit of trade size. 1 lot of a currency pair is usually 100,000 units of the first currency.</dd>
<dt>Swap</dt><dd>A small daily interest charge or credit for holding a position overnight. It is included in profit / loss.</dd>
<dt>Safety switch</dt><dd>If the account falls 15% below its highest value, the bot closes everything and stops until you restart it.</dd>
</dl></details></div>
</main><script>
const P={points};
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
if (P.length < 3) {{ document.getElementById('eqnote').textContent='The chart fills in after a few daily checks.'; }}
if (window.Chart) {{
 Chart.defaults.color=css('--ink2'); Chart.defaults.borderColor=css('--line'); Chart.defaults.font.family='system-ui';
 new Chart(document.getElementById('eq'),{{type:'line',data:{{labels:P.map(p=>p.t),datasets:[{{label:'Account value',data:P.map(p=>p.v),
  borderColor:css('--accent'),backgroundColor:css('--accent'),borderWidth:2,pointRadius:P.length<30?3:0,tension:.2}}]}},
  options:{{responsive:true,animation:false,plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>'$'+c.parsed.y.toLocaleString(undefined,{{minimumFractionDigits:2}})}}}}}},
  scales:{{x:{{ticks:{{maxTicksLimit:6}},grid:{{display:false}}}},y:{{ticks:{{callback:v=>'$'+v.toLocaleString()}}}}}}}}}});
}}
</script></body></html>"""


if __name__ == "__main__":
    p = build(open_browser=True)
    print(f"Dashboard: {p}")

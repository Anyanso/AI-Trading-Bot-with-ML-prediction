"""Which version of the bot is worth running? A side-by-side test against the bank and the stock market.

    python compare.py               # real history from Yahoo Finance (needs internet, ~2 minutes)
    python compare.py --simulate    # fake data, only to check the program runs

It tests, over the same years and with the same costs:
  1. Bot today: classic mix, no stock indices (what the MetaQuotes demo allows)
  2. Bot, classic mix with stock indices (what a broker with tradable indices allows)
  3. Bot, balanced profile: stocks + bonds + gold, plus the trend part      <- the new option
and the yardsticks:
  * The bank: 3-month US Treasury bill rate (about what a high-interest savings account pays)
  * Buy and hold the S&P 500 (SPY ETF, dividends included, no borrowing)
  * 60/40: 60% S&P 500, 40% US government bonds, no borrowing

Costs included for the bot: spreads (bps per trade) and the overnight financing a CFD broker
charges for holding positions with borrowed money (the bank rate plus a markup, 2.5% a year by
default). Prices include dividends and bond interest, as CFD brokers pass those on.

Output: output/compare.html (plain-English summary, chart, tables). Not financial advice.
"""
import argparse, json, os
import numpy as np
import pandas as pd

import data
import portfolio as pf

PPY = pf.PPY

# Yahoo Finance stand-ins for each MT5 market (ETFs track the same index or commodity).
YAHOO = {
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X", "USDCHF": "CHF=X", "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X", "USDCAD": "CAD=X", "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X", "AUDJPY": "AUDJPY=X",
    "EURGBP": "EURGBP=X", "EURCHF": "EURCHF=X",
    "XAUUSD": "GLD", "XAGUSD": "SLV", "XPTUSD": "PPLT", "XPDUSD": "PALL",
    "US500": "SPY", "USTEC": "QQQ", "US30": "DIA", "US2000": "IWM", "DE40": "EWG", "UK100": "EWU",
    "FRA40": "EWQ", "EUSTX50": "FEZ", "JPN225": "EWJ", "AUS200": "EWA", "HK50": "EWH", "CHINA50": "FXI",
    "TLT": "TLT", "IEF": "IEF", "TIP": "TIP", "LQD": "LQD",
    "USO": "USO", "UNG": "UNG", "DBA": "DBA", "CPER": "CPER", "DBC": "DBC", "CORN": "CORN", "WEAT": "WEAT",
}


# ---------------------------------------------------------------- data
def load(start, simulate=False):
    closes, missing = {}, []
    for i, (sym, tk) in enumerate(YAHOO.items()):
        try:
            if simulate:
                df = data.simulate(n_days=4500, seed=500 + i)
                if pf.CLASS_OF[sym] == "Indices":       # give fake stocks a little long-run drift
                    df["Close"] = df["Close"] * np.exp(np.arange(len(df)) * 0.0002)
            else:
                df = data.load_yahoo(tk, start=start)
            closes[sym] = df["Close"]
            print(f"  {sym:8s} ({tk:9s}) {len(df):5d} days from {df.index[0].date()}")
        except Exception as e:
            missing.append(sym)
            print(f"  {sym:8s} ({tk:9s}) not available: {str(e)[:60]}")
    c = pd.DataFrame(closes).sort_index()
    c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
    c = c[~c.index.duplicated(keep="last")]
    c = c[c.index.dayofweek < 5]
    # the bank: 13-week T-bill yield, in % a year
    try:
        if simulate:
            raise ValueError
        irx = data.load_yahoo("^IRX", start=start)["Close"]
        irx.index = pd.to_datetime(irx.index).tz_localize(None).normalize()
        cash = (irx / 100).reindex(c.index).ffill().bfill()
    except Exception:
        cash = pd.Series(0.03, index=c.index)
        if not simulate:
            print("  T-bill rate not available: assuming 3% a year for the bank.")
    return c, cash, missing


# ---------------------------------------------------------------- the strategies
FLAT_RATE = None   # set by --flat-rate for brokers that charge a fixed yearly rate instead of bank rate + markup


def financing(w, cash, markup):
    """Daily cost of holding CFD positions overnight, as a fraction of equity.
    Long stock/bond/commodity positions pay the bank rate + markup; shorts receive the bank
    rate − markup. Currency positions pay roughly the markup only (the interest difference
    between the two currencies is in the swap, and isn't in these prices either)."""
    fx = [c for c in w.columns if pf.CLASS_OF.get(c) == "Forex"]
    other = [c for c in w.columns if c not in fx]
    lw, sw = w[other].clip(lower=0).sum(axis=1), (-w[other]).clip(lower=0).sum(axis=1)
    if FLAT_RATE is not None:   # e.g. AvaTrade: the same fixed charge on longs and shorts
        yearly = (lw + sw) * FLAT_RATE + w[fx].abs().sum(axis=1) * markup
    else:
        yearly = lw * (cash + markup) - sw * (cash - markup) + w[fx].abs().sum(axis=1) * markup
    return (yearly / PPY).shift(1).fillna(0.0)


def bot(closes, cash, profile, mix, target_vol, cost_bps, markup, drop_indices=False):
    c = closes.drop(columns=[s for s in closes if pf.CLASS_OF[s] == "Indices"]) if drop_indices else closes
    kw = dict(target_vol=target_vol, max_gross=4.0, max_weight=0.25, cost_bps=cost_bps)
    tf = pf.build_portfolio(c, **kw)
    lo = pf.build_portfolio(c, long_only=True, class_budget=pf.PROFILES[profile]["long_budget"], **kw)
    bl = pf.blend(tf, lo, mix, **kw)
    w = bl["weights"]
    ret = bl["ret"] - financing(w, cash, markup).reindex(bl["ret"].index).fillna(0)
    return ret, w.loc[bl["start"]:]


def stats(r, cash):
    eq = (1 + r).cumprod()
    yrs = len(r) / PPY
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    bank = (1 + cash.reindex(r.index) / PPY).prod() ** (1 / yrs) - 1
    yearly = (1 + r).groupby(r.index.year).prod() - 1
    bank_y = (1 + cash.reindex(r.index) / PPY).groupby(r.index.year).prod() - 1
    full_years = r.groupby(r.index.year).size() > 200
    beat = (yearly > bank_y)[full_years]
    return {"cagr": cagr, "vs_bank": cagr - bank, "vol": r.std() * np.sqrt(PPY),
            "max_dd": (eq / eq.cummax() - 1).min(), "worst_year": yearly[full_years].min(),
            "best_year": yearly[full_years].max(), "beat_bank": beat.mean() if len(beat) else np.nan,
            "per_month_1000": 1000 * ((1 + cagr) ** (1 / 12) - 1), "growth_1000": 1000 * eq.iloc[-1]}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--mix", type=float, default=0.25, help="share of risk in trend (bot default 25%%)")
    ap.add_argument("--target-vol", type=float, default=0.10, help="0.10 = the Normal risk level")
    ap.add_argument("--cost-bps", type=float, default=3.0, help="spread + slippage per trade, in bps")
    ap.add_argument("--markup", type=float, default=0.025,
                    help="broker's overnight financing markup above the bank rate (0.025 = 2.5%% a year)")
    ap.add_argument("--flat-rate", type=float, default=None,
                    help="for brokers charging a fixed yearly financing rate on index CFDs instead of bank rate + "
                         "markup (e.g. 0.021 = 2.1%% a year, which is 0.0058%% a day)")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--out", default="output")
    a = ap.parse_args()
    global FLAT_RATE
    FLAT_RATE = a.flat_rate

    print("Downloading price history…")
    closes, cash, missing = load(a.start, a.simulate)
    rets = closes.ffill(limit=5).pct_change(fill_method=None)

    print("\nRunning the strategies…")
    kw = dict(mix=a.mix, target_vol=a.target_vol, cost_bps=a.cost_bps, markup=a.markup)
    runs = {
        "today":    bot(closes, cash, "classic", drop_indices=True, **kw),
        "classic":  bot(closes, cash, "classic", **kw),
        "balanced": bot(closes, cash, "balanced", **kw),
    }
    R = {k: v[0] for k, v in runs.items()}
    R["bank"] = cash / PPY
    R["spy"] = rets["US500"]
    R["6040"] = 0.6 * rets["US500"] + 0.4 * rets["IEF"]
    # common period: from when every strategy and yardstick is running
    start = max(r.dropna().index[0] for r in R.values())
    start = max(start, max(runs[k][1].abs().sum(axis=1).gt(0).idxmax() for k in runs))
    idx = R["bank"].loc[start:].index
    R = {k: r.reindex(idx).fillna(0.0) for k, r in R.items()}
    S = {k: stats(r, cash) for k, r in R.items()}

    # the balanced profile at each risk level (same costs)
    levels = {}
    for name, tv in (("Cautious", 0.05), ("Normal", 0.10), ("Aggressive", 0.15)):
        r = bot(closes, cash, "balanced", mix=a.mix, target_vol=tv, cost_bps=a.cost_bps, markup=a.markup)[0]
        levels[name] = stats(r.reindex(idx).fillna(0.0), cash)

    # average exposure of the balanced bot: how much it borrows
    wb = runs["balanced"][1].reindex(idx).fillna(0.0)
    by_class = wb.abs().T.groupby(pf.CLASS_OF).sum().T.mean()
    gross = wb.abs().sum(axis=1).mean()

    names = {"today": "Bot today (no stock indices)", "classic": "Bot, classic mix with stock indices",
             "balanced": "Bot, balanced profile (stocks + bonds)", "bank": "The bank (T-bills)",
             "spy": "Buy and hold S&P 500 (no borrowing)", "6040": "60/40 stocks/bonds (no borrowing)"}
    print(f"\n{idx[0].date()} → {idx[-1].date()}  ({len(idx) / PPY:.1f} years)  costs {a.cost_bps} bps/trade, "
          + (f"financing = flat {a.flat_rate:.1%} a year" if a.flat_rate is not None else f"financing = bank rate + {a.markup:.1%}"))
    print(f"  {'':42s} {'a year':>7s} {'vs bank':>8s} {'swings':>7s} {'worst drop':>11s} {'worst yr':>9s} {'yrs > bank':>10s}")
    for k in names:
        s = S[k]
        print(f"  {names[k]:42s} {s['cagr']:+7.1%} {s['vs_bank']:+8.1%} {s['vol']:7.1%} {s['max_dd']:11.1%} "
              f"{s['worst_year']:+9.1%} {s['beat_bank']:10.0%}")
    print(f"\n  Balanced bot holds on average {gross:.1f}x its equity in positions "
          + ", ".join(f"{k} {v:.2f}x" for k, v in by_class.items() if v > 0.005))
    write_html(a, idx, R, S, names, levels, by_class, gross, missing)
    print(f"\nReport: {os.path.join(a.out, 'compare.html')}")


def verdict(S):
    b, t, spy = S["balanced"], S["today"], S["spy"]
    lines = []
    if b["vs_bank"] > 0.01:
        lines.append(f"The balanced profile beat the bank by about <b>{b['vs_bank']:.1%} a year</b> after costs, "
                     f"and beat the bot as it runs today by {b['cagr'] - t['cagr']:+.1%} a year.")
    elif b["vs_bank"] > 0:
        lines.append(f"The balanced profile beat the bank, but only by {b['vs_bank']:.1%} a year after costs. "
                     f"That's too thin to rely on.")
    else:
        lines.append(f"Even the balanced profile did <b>not</b> beat the bank after costs ({b['vs_bank']:+.1%} a year). "
                     f"Financing costs are likely eating the gains.")
    lines.append(f"Its worst drop was {b['max_dd']:.0%}, against {spy['max_dd']:.0%} for simply holding the S&P 500, "
                 f"which returned {spy['cagr']:.1%} a year with no borrowing at all.")
    if spy["cagr"] > b["cagr"]:
        lines.append("Holding a plain stock ETF made more money over this period, at the cost of much deeper drops. "
                     "The bot's job is a smoother ride, not the highest return.")
    return " ".join(lines)


def write_html(a, idx, R, S, names, levels, by_class, gross, missing):
    p = lambda x: f"{x:+.1%}" if pd.notna(x) else "–"
    usd = lambda x: ("−" if x < 0 else "") + f"${abs(x):,.2f}"
    tone = lambda v: "pos" if pd.notna(v) and v > 0 else "neg" if pd.notna(v) and v < 0 else ""
    order = ["balanced", "classic", "today", "spy", "6040", "bank"]
    rows = "".join(
        f"<tr{' class=hl' if k == 'balanced' else ''}><td>{names[k]}</td><td class='{tone(S[k]['cagr'])}'>{p(S[k]['cagr'])}</td>"
        f"<td class='{tone(S[k]['vs_bank'])}'>{p(S[k]['vs_bank']) if k != 'bank' else '–'}</td>"
        f"<td>{usd(S[k]['per_month_1000'])}</td><td>{S[k]['vol']:.0%}</td><td>{S[k]['max_dd']:.0%}</td>"
        f"<td>{p(S[k]['worst_year'])}</td><td>{f"{S[k]['beat_bank']:.0%}" if k != 'bank' else '–'}</td><td>${S[k]['growth_1000']:,.0f}</td></tr>"
        for k in order)
    lvl_rows = "".join(
        f"<tr><td>{n}</td><td class='{tone(s['cagr'])}'>{p(s['cagr'])}</td><td class='{tone(s['vs_bank'])}'>{p(s['vs_bank'])}</td>"
        f"<td>{usd(s['per_month_1000'])}</td><td>{s['max_dd']:.0%}</td><td>{p(s['worst_year'])}</td></tr>"
        for n, s in levels.items())
    cls_name = {"Indices": "Stock indices", "Bonds": "Bonds", "Metals": "Gold/silver", "Commodities": "Commodities",
                "Forex": "Currencies (trend part only)"}
    exp_rows = "".join(f"<tr><td>{cls_name.get(k, k)}</td><td>{v:.2f}×</td></tr>" for k, v in by_class.items() if v > 0.005)
    step = max(1, len(idx) // 700)
    chart = {"dates": [d.strftime("%Y-%m-%d") for d in idx[::step]],
             "series": [{"name": names[k], "data": (1000 * (1 + R[k]).cumprod()).values[::step].round(0).tolist(),
                         "key": k} for k in order]}
    sim = "<p class='warn'>⚠ Simulated data: this only checks the program runs. The numbers mean nothing.</p>" if a.simulate else ""
    miss = f"<p class='note'>Not available from Yahoo, left out: {', '.join(missing)}</p>" if missing else ""
    html = PAGE.format(start=idx[0].date(), end=idx[-1].date(), yrs=f"{len(idx) / PPY:.1f}", rows=rows,
                       lvl_rows=lvl_rows, exp_rows=exp_rows, gross=f"{gross:.1f}", cost=a.cost_bps,
                       fin=(f"a flat {a.flat_rate:.1%} a year" if a.flat_rate is not None else f"the bank rate + {a.markup:.1%}"), tv=f"{a.target_vol:.0%}", mix=f"{a.mix:.0%}",
                       verdict=verdict(S), sim=sim, miss=miss, chart=json.dumps(chart))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "compare.html"), "w", encoding="utf-8") as f:
        f.write(html)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot vs Bank vs Stocks</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#0b0b0b;--ink2:#5d5c58;--line:#e6e5e0;--good:#0b7a35;--bad:#c4302b;--hl:#2a78d614;
--c1:#eb6834;--c2:#2a78d6;--c3:#8a8984;--c4:#0b7a35;--c5:#9b59b6;--c6:#b8b6ae}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1e;--ink:#f3f2ee;--ink2:#b5b4ad;--line:#33332f;--good:#4cc27a;--bad:#ec6b66;--hl:#3987e522;
--c1:#f07a4a;--c2:#3987e5;--c3:#9b9a94;--c4:#4cc27a;--c5:#b57bd0;--c6:#6f6e69}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.55 system-ui,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:auto;padding:24px 16px 40px}} h1{{font-size:23px;margin:0 0 4px}} h2{{font-size:16.5px;margin:0 0 10px}}
.sub,.note{{color:var(--ink2)}} .note{{font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px;overflow-x:auto}}
.verdict{{font-size:16.5px}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{text-align:right;padding:8px;border-bottom:1px solid var(--line);white-space:nowrap}}
th:first-child,td:first-child{{text-align:left;white-space:normal}} th{{color:var(--ink2);font-weight:600;font-size:12.5px}}
td.pos{{color:var(--good)}} td.neg{{color:var(--bad)}} tr.hl td{{background:var(--hl);font-weight:600}}
.warn{{background:#eda10022;border:1px solid #eda100;border-radius:8px;padding:10px 12px}} canvas{{max-height:340px}}
.grid{{display:grid;grid-template-columns:2fr 1fr;gap:16px}} @media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>Which bot is worth running?</h1>
<p class="sub">{start} → {end} ({yrs} years) · Normal risk ({tv} yearly swings) · {mix} of risk in trend-following · costs: {cost} bps per trade plus overnight financing at {fin}. Not financial advice.</p>
{sim}
<div class="card verdict">{verdict}</div>
<div class="card"><h2>Results, after all costs</h2>
<table><tr><th></th><th>Per year</th><th>Above the bank</th><th>Per month on $1,000</th><th>Swings</th><th>Worst drop</th><th>Worst year</th><th>Years beating the bank</th><th>$1,000 became</th></tr>{rows}</table>
<p class="note"><b>Swings</b>: typical yearly up-and-down (volatility). <b>Worst drop</b>: biggest fall from a high point before recovering. <b>Per month on $1,000</b>: the average, but individual months vary much more than that. Past results don't predict future ones, and a backtest is always kinder than real trading.</p>
{miss}</div>
<div class="card"><h2>Growth of $1,000</h2><canvas id="eq"></canvas></div>
<div class="grid">
<div class="card"><h2>Balanced profile at each risk level</h2>
<table><tr><th>Risk level</th><th>Per year</th><th>Above the bank</th><th>Per month on $1,000</th><th>Worst drop</th><th>Worst year</th></tr>{lvl_rows}</table>
<p class="note">Higher risk scales gains and losses together, and borrowing costs grow with it, so the extra return shrinks as risk goes up.</p></div>
<div class="card"><h2>What the balanced bot holds (average)</h2>
<table><tr><th>Asset class</th><th>Position size</th></tr>{exp_rows}</table>
<p class="note">In total about {gross}× the account's value. That's leverage: it's why financing costs matter, and why a broker's overnight charges and margin rules should be checked before switching.</p></div>
</div>
</main><script>
const D={chart};
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const col={{balanced:'--c1',classic:'--c2',today:'--c3',spy:'--c4','6040':'--c5',bank:'--c6'}};
Chart.defaults.color=css('--ink2');Chart.defaults.borderColor=css('--line');Chart.defaults.font.family='system-ui';
new Chart(document.getElementById('eq'),{{type:'line',data:{{labels:D.dates,datasets:D.series.map(s=>({{label:s.name,data:s.data,
 borderColor:css(col[s.key]),backgroundColor:css(col[s.key]),borderWidth:s.key==='balanced'?3:1.6}}))}},
 options:{{responsive:true,animation:false,interaction:{{mode:'index',intersect:false}},
 plugins:{{legend:{{position:'top',align:'start',labels:{{boxWidth:12,boxHeight:2}}}},tooltip:{{callbacks:{{label:c=>c.dataset.label+': $'+c.parsed.y.toLocaleString()}}}}}},
 scales:{{x:{{ticks:{{maxTicksLimit:8}},grid:{{display:false}}}},y:{{type:'logarithmic',ticks:{{callback:v=>'$'+v.toLocaleString()}}}}}},
 elements:{{point:{{radius:0,hoverRadius:4}}}}}}}});
</script></body></html>"""

if __name__ == "__main__":
    main()

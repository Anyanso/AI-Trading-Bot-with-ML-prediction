"""Diversified trend-following portfolio ("managed futures" style).

The idea: one simple trend rule, applied the same way to many unrelated markets
(currencies, metals, stock indices, bonds, commodities), with every position
sized by that market's volatility so each one carries a similar amount of risk.
No single market needs to be predictable; the edge, if any, comes from catching
the occasional big trend somewhere while losses elsewhere stay small.

Nothing is tuned to your data: the trend speeds and risk settings are fixed,
standard choices. That makes the backtest much harder to fool.

    python portfolio.py                          # default multi-asset universe from MT5
    python portfolio.py --target-vol 0.10 --cost-bps 3
    python portfolio.py --symbols EURUSD XAUUSD US500 TLT
    python portfolio.py --simulate               # test without MT5

Output: output/portfolio.html, output/portfolio_positions.csv
"""
import argparse, json, os
import numpy as np
import pandas as pd

import data
from indicators import ema

PPY = 260  # trading days per year

UNIVERSE = {
    "Forex":       ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
                    "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF"],
    "Metals":      ["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"],
    "Indices":     ["US500", "USTEC", "US30", "US2000", "DE40", "UK100", "FRA40", "EUSTX50",
                    "JPN225", "AUS200", "HK50", "CHINA50"],
    "Bonds":       ["TLT", "IEF", "TIP", "LQD"],                 # US-listed bond ETFs (SHY dropped: too calm)
    "Commodities": ["USO", "UNG", "DBA", "CPER", "DBC", "CORN", "WEAT"],  # US-listed commodity ETFs
}
CLASS_OF = {s: c for c, syms in UNIVERSE.items() for s in syms}
SPEEDS = (8, 16, 32, 64)  # fast EMA lengths; slow = 4x fast

# Strategy profiles. Both use the same trend-following part (25% of risk by default) across
# every tradable market. They differ in the steady "long-only" part (the other 75%):
#   classic  – equal risk in every market, currencies included (the original design). Currencies
#              have no built-in long-term return, so this part earns little.
#   balanced – a proper balanced portfolio: half the risk in stock indices (the main long-term
#              source of return), 35% in bonds, 15% in gold and commodities. No currencies.
#              Fixed, round numbers chosen up front, not tuned on any data.
PROFILES = {
    "classic":  {"label": "Classic (all markets, equal risk)", "long_budget": None},
    "balanced": {"label": "Balanced (stocks + bonds)",
                 "long_budget": {"Indices": 0.50, "Bonds": 0.35, "Metals": 0.075, "Commodities": 0.075}},
}
LONG_EXCLUDE = {"XPTUSD", "XPDUSD", "PPLT", "PALL"}   # balanced long part: "Metals" = gold and silver only
STD = {}   # broker symbol name -> standard name (filled in by execute.py, e.g. "GER40.cash" -> "DE40")


# ---------------------------------------------------------------- signal & sizing
def trend_forecast(close: pd.Series) -> pd.Series:
    """Four trend speeds 'vote' long (+1) or short (-1); the forecast is their average,
    so it ranges from -1 (all say down) through 0 (split) to +1 (all say up)."""
    votes = [np.sign(ema(close, f) - ema(close, 4 * f)) for f in SPEEDS]
    fc = sum(votes) / len(votes)
    return fc.where(close.notna())


def _finalize(w_raw, rets, target_vol, max_gross, max_weight, cost_bps):
    """Scale raw weights so the book's realised volatility hits the target (past data
    only), cap single positions and total exposure, then charge trading costs."""
    raw_ret = (w_raw.shift(1) * rets.fillna(0)).sum(axis=1)
    realised = raw_ret.rolling(126, min_periods=60).std() * np.sqrt(PPY)
    scale = (target_vol / realised).replace([np.inf, -np.inf], np.nan)
    gross_raw = w_raw.abs().sum(axis=1)
    scale = np.minimum(scale, max_gross / gross_raw.replace(0, np.nan)).fillna(0.0)
    w = w_raw.mul(scale, axis=0).clip(-max_weight, max_weight)  # no single market above the cap

    turnover = w.diff().abs().fillna(w.abs())
    contrib = w.shift(1).fillna(0) * rets.fillna(0)  # each market's P&L, as % of equity
    costs = turnover.shift(1).fillna(0) * cost_bps / 10_000
    port = contrib.sum(axis=1) - costs.sum(axis=1)
    start = scale.gt(0).idxmax()                     # first day the book is invested
    return dict(weights=w, contrib=(contrib - costs).loc[start:], ret=port.loc[start:],
                gross=w.abs().sum(axis=1).loc[start:], start=start)


def build_portfolio(closes: pd.DataFrame, target_vol=0.10, max_gross=4.0, cost_bps=3.0,
                    long_only=False, max_weight=0.25, warmup=4 * max(SPEEDS), class_budget=None,
                    class_of=None):
    """Volatility-targeted portfolio: trend-following, or always-long.

    Without class_budget every active market gets the same risk. With class_budget
    (e.g. {"Indices": 0.5, "Bonds": 0.35, ...}) each asset class gets that share of the risk,
    split equally among its active markets; markets in other classes get none. If a class
    has no active markets (e.g. stock indices not tradable on this account), the remaining
    classes share its budget.

    Weights are fractions of account equity (0.5 = a position worth 50% of equity).
    Every weight at date t uses only data up to t and is applied to the return of t+1.
    """
    class_of = class_of or CLASS_OF
    px = closes.ffill(limit=5)                       # carry over short market holidays
    rets = px.pct_change(fill_method=None)
    live = closes.notna().rolling(6, min_periods=1).max().astype(bool)  # traded recently
    seasoned = closes.notna().cumsum() > warmup      # enough history for the slowest signal

    fc = pd.DataFrame({s: trend_forecast(px[s]) for s in px}) if not long_only \
        else pd.DataFrame(1.0, index=px.index, columns=px.columns)
    vol = rets.ewm(span=60, min_periods=40).std() * np.sqrt(PPY)  # annualised, per market
    vol = vol.clip(lower=0.04)                       # floor: no giant positions in very calm markets

    active = live & seasoned & fc.notna() & vol.notna()
    unit = (fc / vol).where(active)                  # 1.0 = a position with 100% annual vol
    if class_budget is None:
        n = active.sum(axis=1).replace(0, np.nan)
        w_raw = unit.div(n, axis=0).fillna(0.0)      # equal risk budget per active market
    else:
        cls = pd.Series({c: class_of.get(c, "Other") for c in px.columns})
        if long_only:
            active = active & ~pd.Series({c: STD.get(c, c) in LONG_EXCLUDE for c in px.columns})
        budget = pd.DataFrame(0.0, index=px.index, columns=px.columns)
        n_cls = {k: active.loc[:, cls == k].sum(axis=1) for k in class_budget}
        live_budget = sum(class_budget[k] * (n_cls[k] > 0) for k in class_budget).replace(0, np.nan)
        for k, b in class_budget.items():
            cols = cls.index[cls == k]
            share = (b / n_cls[k].replace(0, np.nan) / live_budget).fillna(0.0)
            budget.loc[:, cols] = np.outer(share.values, np.ones(len(cols)))
        w_raw = (unit * budget).where(active).fillna(0.0)
    out = _finalize(w_raw, rets, target_vol, max_gross, max_weight, cost_bps)
    out.update(forecast=fc, vol=vol, rets=rets)
    return out


def blend(tf, lo, mix, target_vol=0.10, max_gross=4.0, max_weight=0.25, cost_bps=3.0):
    """Mix the trend book and the long-only book (each already at the target risk),
    then re-scale the combination back to the target. mix = share in trend (0..1).
    Trades are netted: if trend is short and long-only is long, they partly cancel."""
    cols = tf["weights"].columns
    w_raw = (mix * tf["weights"] + (1 - mix) * lo["weights"].reindex(columns=cols)).fillna(0.0)
    return _finalize(w_raw, tf["rets"], target_vol, max_gross, max_weight, cost_bps)


def stats(r: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    yrs = len(r) / PPY
    sd = r.std()
    return {"total": eq.iloc[-1] - 1, "cagr": eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan,
            "vol": sd * np.sqrt(PPY), "sharpe": r.mean() / sd * np.sqrt(PPY) if sd > 0 else np.nan,
            "max_dd": (eq / eq.cummax() - 1).min()}


# ---------------------------------------------------------------- data
FRAMES = {}   # full OHLCV data of the last load, for the prediction journal


def load_universe(symbols, bars, simulate=False, min_bars=600):
    closes, skipped = {}, []
    for i, s in enumerate(symbols):
        try:
            df = data.simulate(n_days=bars, seed=1000 + i) if simulate else data.load_mt5(s, "D1", bars)
        except SystemExit as e:
            skipped.append((s, str(e).split("\n")[0])); continue
        if len(df) < min_bars:
            skipped.append((s, f"only {len(df)} daily bars")); continue
        closes[s] = df["Close"]
        FRAMES[s] = df
        print(f"  {s:8s} {CLASS_OF.get(s, 'Other'):12s} {len(df):5d} bars from {df.index[0].date()}")
    if not closes:
        raise SystemExit("No markets loaded.")
    c = pd.DataFrame(closes).sort_index()
    c.index = c.index.normalize()
    return c[~c.index.duplicated(keep="last")], skipped


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", help="override the default universe")
    ap.add_argument("--bars", type=int, default=5000, help="daily bars to download per market")
    ap.add_argument("--mix", type=float, default=0.5, help="share of risk in trend for the blend (0.5 = half and half)")
    ap.add_argument("--target-vol", type=float, default=0.10, help="target annual volatility (0.10 = 10%%)")
    ap.add_argument("--max-gross", type=float, default=4.0, help="cap on total exposure (4 = 4x equity)")
    ap.add_argument("--max-weight", type=float, default=0.25, help="cap on any one position (0.25 = 25%% of equity)")
    ap.add_argument("--cost-bps", type=float, default=3.0)
    ap.add_argument("--equity", type=float, default=100_000, help="account size for the positions table")
    ap.add_argument("--test-frac", type=float, default=0.4)
    ap.add_argument("--profile", choices=list(PROFILES), default="classic",
                    help="long-only part: classic (equal risk, all markets) or balanced (stocks + bonds)")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--out", default="output")
    a = ap.parse_args()

    symbols = a.symbols or [s for syms in UNIVERSE.values() for s in syms]
    print(f"Loading {len(symbols)} markets…")
    closes, skipped = load_universe(symbols, a.bars, a.simulate)
    for s, why in skipped:
        print(f"  {s:8s} skipped: {why}")

    kw = dict(target_vol=a.target_vol, max_gross=a.max_gross, max_weight=a.max_weight, cost_bps=a.cost_bps)
    tf = build_portfolio(closes, **kw)
    lo = build_portfolio(closes, long_only=True, class_budget=PROFILES[a.profile]["long_budget"], **kw)
    bl = blend(tf, lo, a.mix, **kw)
    idx = tf["ret"].index.intersection(lo["ret"].index).intersection(bl["ret"].index)
    R = {"Trend": tf["ret"].loc[idx], "Long-only": lo["ret"].loc[idx], "Blend": bl["ret"].loc[idx]}
    split = idx[int(len(idx) * (1 - a.test_frac))]
    S = {k: (stats(r), stats(r.loc[split:])) for k, r in R.items()}
    corr = R["Trend"].corr(R["Long-only"])

    print(f"\n{idx[0].date()} → {idx[-1].date()}   (recent = since {split.date()})")
    for k, (full, rec) in S.items():
        name = f"Blend {a.mix:.0%} trend" if k == "Blend" else k
        print(f"  {name:18s} Sharpe {full['sharpe']:+.2f} (recent {rec['sharpe']:+.2f})  "
              f"CAGR {full['cagr']:+.1%}  max DD {full['max_dd']:.1%}")
    print(f"  Correlation between trend and long-only daily returns: {corr:+.2f}")

    # How sensitive is the result to the mix? (Shown for insight; don't cherry-pick the best.)
    sweep = []
    for m in (0.0, 0.25, 0.5, 0.75, 1.0):
        r = blend(tf, lo, m, **kw)["ret"].loc[idx]
        st, rs = stats(r), stats(r.loc[split:])
        sweep.append({"mix": m, "sharpe": st["sharpe"], "recent": rs["sharpe"], "cagr": st["cagr"], "max_dd": st["max_dd"]})
    sweep = pd.DataFrame(sweep)

    # Per-market and per-class results of the blend
    c = bl["contrib"].loc[idx]
    per_mkt = pd.DataFrame({
        "class": [CLASS_OF.get(m, "Other") for m in c],
        "pnl": c.sum(),
        "sharpe": c.mean() / c.std().replace(0, np.nan) * np.sqrt(PPY),
    }, index=c.columns).sort_values("pnl", ascending=False)
    per_cls = c.T.groupby(per_mkt["class"]).sum().T
    cls_tbl = pd.DataFrame({"pnl": per_cls.sum(),
                            "sharpe": per_cls.mean() / per_cls.std() * np.sqrt(PPY)}).sort_values("pnl", ascending=False)
    yearly = pd.DataFrame({k: (1 + r).groupby(idx.year).prod() - 1 for k, r in R.items()})

    # Today's target positions for the blend: what an order module would trade toward
    w_now = bl["weights"].iloc[-1]
    pos = pd.DataFrame({
        "class": [CLASS_OF.get(m, "Other") for m in w_now.index],
        "forecast": tf["forecast"].iloc[-1].reindex(w_now.index),
        "trend_part": a.mix * tf["weights"].iloc[-1].reindex(w_now.index),
        "long_part": (1 - a.mix) * lo["weights"].iloc[-1].reindex(w_now.index),
        "weight": w_now, "notional": w_now * a.equity,
        "direction": np.sign(w_now).map({1: "LONG", -1: "SHORT", 0: "FLAT"}),
        "last_price": closes.ffill().iloc[-1].reindex(w_now.index),
    }).sort_values("weight", key=abs, ascending=False)
    os.makedirs(a.out, exist_ok=True)
    pos.to_csv(os.path.join(a.out, "portfolio_positions.csv"))

    write_html(a, closes, skipped, idx, split, R, S, corr, sweep, per_mkt, cls_tbl, yearly, pos,
               bl["gross"].loc[idx])
    print(f"Report: {os.path.join(a.out, 'portfolio.html')}")


def write_html(a, closes, skipped, idx, split, R, S, corr, sweep, per_mkt, cls_tbl, yearly, pos, gross):
    step = max(1, len(idx) // 700)
    eq = {k: (1 + r).cumprod() for k, r in R.items()}
    chart = {"dates": [d.strftime("%Y-%m-%d") for d in idx[::step]],
             "eq": {k: (e.values[::step] - 1).round(4).tolist() for k, e in eq.items()},
             "dd": {k: (e / e.cummax() - 1).values[::step].round(4).tolist() for k, e in eq.items()},
             "blend_name": f"Blend ({a.mix:.0%} trend)"}
    p = lambda x: f"{x:+.1%}" if pd.notna(x) else "–"
    f2 = lambda x: f"{x:+.2f}" if pd.notna(x) else "–"
    tone = lambda v: "pos" if pd.notna(v) and v > 0 else "neg" if pd.notna(v) and v < 0 else ""
    label = {"Blend": f"<b>Blend ({a.mix:.0%} trend / {1 - a.mix:.0%} long-only)</b>", "Trend": "Trend only",
             "Long-only": "Long-only (same markets, same risk)"}

    def kpi(name, s):
        return (f"<tr><td>{name}</td><td>{f2(s['sharpe'])}</td><td>{p(s['cagr'])}</td>"
                f"<td>{s['vol']:.1%}</td><td>{s['max_dd']:.1%}</td><td>{p(s['total'])}</td></tr>")
    order = ["Blend", "Trend", "Long-only"]
    kpis = "".join(kpi(label[k] + ": full period", S[k][0]) for k in order) + \
        "<tr><td colspan=6 class='sep'>Recent stretch only, since " + str(split.date()) + "</td></tr>" + \
        "".join(kpi(label[k], S[k][1]) for k in order)
    sweep_rows = "".join(
        f"<tr{' class=hl' if abs(r.mix - a.mix) < 1e-9 else ''}><td>{r.mix:.0%} trend / {1 - r.mix:.0%} long</td>"
        f"<td>{f2(r.sharpe)}</td><td>{f2(r.recent)}</td><td>{p(r.cagr)}</td><td>{r.max_dd:.1%}</td></tr>"
        for r in sweep.itertuples())
    cls_rows = "".join(f"<tr><td>{k}</td><td class='{tone(r.pnl)}'>{p(r.pnl)}</td><td>{f2(r.sharpe)}</td></tr>"
                       for k, r in cls_tbl.iterrows())
    mkt_rows = "".join(f"<tr><td>{k}</td><td>{r['class']}</td><td class='{tone(r.pnl)}'>{p(r.pnl)}</td>"
                       f"<td>{f2(r.sharpe)}</td></tr>" for k, r in per_mkt.iterrows())
    yr_rows = "".join(f"<tr><td>{y}</td>" + "".join(f"<td class='{tone(r[k])}'>{p(r[k])}</td>" for k in order) + "</tr>"
                      for y, r in yearly.iterrows())
    pill = {"LONG": "in", "SHORT": "short", "FLAT": "out"}
    pos_rows = "".join(
        f"<tr><td>{k}</td><td>{r['class']}</td><td><span class='pill {pill[r.direction]}'>{r.direction}</span></td>"
        f"<td>{r.forecast:+.2f}</td><td>{r.trend_part:+.1%}</td><td>{r.long_part:+.1%}</td>"
        f"<td><b>{r.weight:+.1%}</b></td><td>{r.notional:,.0f}</td></tr>" for k, r in pos.iterrows())
    skip = ("<p class='note'>Not available on this account, or too little history: "
            + ", ".join(s for s, _ in skipped) + "</p>") if skipped else ""
    sim = "<p class='warn'>⚠ Simulated data: this only tests the code.</p>" if a.simulate else ""

    html = PAGE.format(
        n=closes.shape[1], start=idx[0].date(), end=idx[-1].date(), tv=a.target_vol, cost=a.cost_bps,
        mg=a.max_gross, mw=a.max_weight, mix=a.mix, sim=sim, skip=skip, kpis=kpis, corr=f"{corr:+.2f}",
        sweep_rows=sweep_rows, cls_rows=cls_rows, mkt_rows=mkt_rows, yr_rows=yr_rows, pos_rows=pos_rows,
        equity=f"{a.equity:,.0f}", gross_now=f"{gross.iloc[-1]:.2f}", gross_max=f"{gross.max():.2f}",
        chart=json.dumps(chart))
    with open(os.path.join(a.out, "portfolio.html"), "w", encoding="utf-8") as f:
        f.write(html)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trend Blend Portfolio</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{{--bg:#fcfcfb;--card:#fff;--ink:#0b0b0b;--ink2:#52514e;--line:#e6e5e0;--s1:#8a8984;--s2:#2a78d6;--s3:#eb6834;--good:#008300;--bad:#c4302b;--hl:#2a78d614}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--card:#242422;--ink:#fff;--ink2:#c3c2b7;--line:#3a3a37;--s1:#9b9a94;--s2:#3987e5;--s3:#d95926;--good:#3fae3f;--bad:#e66767;--hl:#3987e522}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}} main{{max-width:1000px;margin:auto;padding:24px 16px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:17px;margin:0 0 8px}} .sub{{color:var(--ink2);margin:0 0 18px}} .note{{color:var(--ink2);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:18px;overflow-x:auto}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} @media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}} th,td{{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} th{{color:var(--ink2);font-weight:600}} td.pos{{color:var(--good)}} td.neg{{color:var(--bad)}}
.pill{{display:inline-block;width:50px;text-align:center;border-radius:4px;font-size:11px;font-weight:700;padding:2px 0;color:#fff}}
.in{{background:var(--good)}} .out{{background:var(--ink2)}} .short{{background:var(--bad)}}
.sep{{text-align:left!important;color:var(--ink2);font-size:12.5px;padding-top:14px}} tr.hl td{{background:var(--hl)}}
.warn{{background:#eda10022;border:1px solid #eda100;border-radius:8px;padding:10px 12px}} canvas{{max-height:320px}}
</style></head><body><main>
<h1>Trend + long-only blend</h1>
<p class="sub">{n} markets · {start} → {end} · target volatility {tv:.0%} · costs {cost} bps per trade · max {mw:.0%} of equity per market, {mg}× in total. Not financial advice.</p>
{sim}
<div class="card"><h2>Results</h2>
<table><tr><th></th><th>Sharpe</th><th>CAGR</th><th>Volatility</th><th>Max DD</th><th>Total</th></tr>{kpis}</table>
<p class="note">The blend gives {mix:.0%} of its risk to the trend book and the rest to the long-only book, then scales the combination back to the same {tv:.0%} volatility. No parameter was tuned on this data. Correlation between the trend and long-only books: <b>{corr}</b>. The lower this is, the more the two smooth each other out.</p>
{skip}</div>
<div class="card"><h2>How much trend? Every mix, same {tv:.0%} risk</h2>
<table><tr><th>Mix</th><th>Sharpe</th><th>Sharpe, recent</th><th>CAGR</th><th>Max DD</th></tr>{sweep_rows}</table>
<p class="note">For insight only. Picking the single best row would be tuning on the past. Look for a broad range of mixes that all beat 0% and 100%.</p></div>
<div class="card"><h2>Growth of $1 (return)</h2><canvas id="eq"></canvas></div>
<div class="card"><h2>Drawdown from peak</h2><canvas id="dd"></canvas></div>
<div class="grid">
 <div class="card"><h2>By asset class</h2><table><tr><th>Class</th><th>P&amp;L</th><th>Sharpe</th></tr>{cls_rows}</table>
 <p class="note">Blend P&amp;L by class: each class's contribution, as % of equity, summed over the period.</p></div>
 <div class="card"><h2>By year</h2><table><tr><th>Year</th><th>Blend</th><th>Trend</th><th>Long-only</th></tr>{yr_rows}</table></div>
</div>
<div class="card"><h2>By market (blend)</h2><table><tr><th>Market</th><th>Class</th><th>P&amp;L</th><th>Sharpe</th></tr>{mkt_rows}</table></div>
<div class="card"><h2>Blend target positions now (for {equity} equity)</h2>
<p class="note">Forecast: −1 = all four trend speeds point down, +1 = all point up. Trend part and long part are each book's share before the final re-scaling; Weight is the actual target, as % of equity. Total exposure now {gross_now}× equity (max in backtest {gross_max}×).</p>
<table><tr><th>Market</th><th>Class</th><th>Side</th><th>Forecast</th><th>Trend part</th><th>Long part</th><th>Weight</th><th>Notional</th></tr>{pos_rows}</table></div>
</main><script>
const D={chart};
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
Chart.defaults.color=css('--ink2');Chart.defaults.borderColor=css('--line');Chart.defaults.font.family='system-ui';
const pf=v=>(v*100).toFixed(0)+'%';
const opts={{responsive:true,animation:false,interaction:{{mode:'index',intersect:false}},
 plugins:{{legend:{{position:'top',align:'start',labels:{{boxWidth:12,boxHeight:2}}}},tooltip:{{callbacks:{{label:c=>c.dataset.label+': '+(c.parsed.y*100).toFixed(1)+'%'}}}}}},
 scales:{{x:{{ticks:{{maxTicksLimit:8}},grid:{{display:false}}}},y:{{ticks:{{callback:pf}}}}}},elements:{{point:{{radius:0,hoverRadius:4}},line:{{borderWidth:2}}}}}};
const ds=k=>[{{label:D.blend_name,data:D[k]['Blend'],borderColor:css('--s3'),backgroundColor:css('--s3'),borderWidth:2.5}},
 {{label:'Trend only',data:D[k]['Trend'],borderColor:css('--s2'),backgroundColor:css('--s2')}},
 {{label:'Long-only',data:D[k]['Long-only'],borderColor:css('--s1'),backgroundColor:css('--s1')}}];
new Chart(eq,{{type:'line',data:{{labels:D.dates,datasets:ds('eq')}},options:opts}});
new Chart(dd,{{type:'line',data:{{labels:D.dates,datasets:ds('dd')}},options:opts}});
</script></body></html>"""

if __name__ == "__main__":
    main()

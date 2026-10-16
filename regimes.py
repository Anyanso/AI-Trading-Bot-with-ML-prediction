"""Market regimes: unsupervised learning of the market's hidden "moods".

Nobody tells this model what a crisis or a calm market looks like. It looks at four
market-wide measurements every day and groups similar days together (a Gaussian mixture
model, a standard unsupervised clustering method):

  * how jumpy markets are compared with their own normal (volatility),
  * how much everything moves together (average correlation between markets),
  * how strongly markets are trending (average strength of the trend signals),
  * which way they trend on balance (more up-trends or down-trends).

Each group is then given a plain name from its average measurements, such as "Calm, rising"
or "Stressed: jumpy, everything moving together". The bot logs today's regime on every run,
and the report shows how the bot's strategy has done in each regime. That tells us whether
some moods suit it and others don't.

    python regimes.py               # MT5 data (default markets)
    python regimes.py --yahoo       # Yahoo Finance data (no MT5 needed)
    python regimes.py --simulate    # fake data, to test the program

Output: output/regimes.html, output/regime_history.csv; live runs append output/regime_log.csv.
"""
import argparse, json, os
from datetime import datetime
import numpy as np
import pandas as pd

import portfolio as pf

OUT = "output"
LOG = os.path.join(OUT, "regime_log.csv")
N_REGIMES = 4
FEATURES = ["volatility", "correlation", "trend_strength", "trend_direction"]


# ---------------------------------------------------------------- features
def market_features(closes: pd.DataFrame) -> pd.DataFrame:
    """Four market-wide numbers per day, each computed from past data only."""
    px = closes.ffill(limit=5)
    rets = px.pct_change(fill_method=None)
    vol20 = rets.rolling(20, min_periods=15).std()
    norm = vol20.rolling(250, min_periods=120).median()
    volatility = np.log(vol20 / norm).median(axis=1)            # >0: jumpier than usual

    r = rets.values
    corr = np.full(len(rets), np.nan)
    for i in range(60, len(rets)):
        w = r[i - 60:i]
        ok = ~np.isnan(w).any(axis=0)
        ok &= np.nanstd(w, axis=0) > 0
        if ok.sum() >= 4:
            with np.errstate(invalid="ignore", divide="ignore"):
                c = np.corrcoef(w[:, ok], rowvar=False)
            # average absolute correlation: currency pairs can be inverted, so sign is arbitrary
            corr[i] = np.nanmean(np.abs(c[np.triu_indices_from(c, 1)]))
    fc = pd.DataFrame({s: pf.trend_forecast(px[s]) for s in px})
    f = pd.DataFrame({"volatility": volatility, "correlation": pd.Series(corr, index=rets.index),
                      "trend_strength": fc.abs().mean(axis=1), "trend_direction": fc.mean(axis=1)})
    return f.dropna()


# ---------------------------------------------------------------- model
def fit(features: pd.DataFrame, k=N_REGIMES, seed=0):
    """Fit the Gaussian mixture and name each regime from its average features."""
    from sklearn.mixture import GaussianMixture
    mu, sd = features.mean(), features.std().replace(0, 1)
    z = (features - mu) / sd
    gm = GaussianMixture(n_components=k, covariance_type="full", n_init=5, random_state=seed).fit(z.values)
    labels = pd.Series(gm.predict(z.values), index=features.index)
    centers = pd.DataFrame(gm.means_, columns=features.columns)        # in z-units
    names = _name(centers, features.groupby(labels).mean())
    return {"gm": gm, "mu": mu, "sd": sd, "names": names, "labels": labels.map(names),
            "proba": pd.DataFrame(gm.predict_proba(z.values), index=features.index).rename(columns=names)}


def _name(centers, raw):
    """Plain names from cluster averages. The jumpiest, most correlated cluster is 'Stressed',
    the calmest is 'Calm', the rest are 'Trending' or 'Choppy' by trend strength."""
    k = len(centers)
    stress = centers["volatility"] + centers["correlation"]
    order = list(stress.sort_values().index)
    base = {order[-1]: "Stressed: jumpy, everything moving together", order[0]: "Calm"}
    rest = [c for c in order if c not in base]
    rest.sort(key=lambda c: centers.loc[c, "trend_strength"])
    for i, c in enumerate(rest):   # the most strongly trending of the middle clusters is "Trending"
        top = i == len(rest) - 1 and (len(rest) > 1 or centers.loc[c, "trend_strength"] > 0)
        base[c] = "Trending" if top else "Choppy, no clear trend"
    names = {}
    for c in range(k):
        n = base[c]
        if not n.startswith("Stressed") and not n.startswith("Choppy"):
            d = raw.loc[c, "trend_direction"] if c in raw.index else 0
            n += ", rising" if d > 0.15 else ", falling" if d < -0.15 else ", mixed"
        while n in names.values():
            n += " (b)"
        names[c] = n
    return names


def current(closes: pd.DataFrame):
    """Today's regime (fitted on all history up to today, so no look-ahead)."""
    f = market_features(closes)
    if len(f) < 250:
        return None
    m = fit(f)
    day = f.index[-1]
    return {"date": day, "regime": m["labels"].iloc[-1], "confidence": float(m["proba"].iloc[-1].max()),
            "days_in_regime": int(_run_length(m["labels"])), **{k: float(f[k].iloc[-1]) for k in FEATURES}}


def _run_length(labels):
    last, n = labels.iloc[-1], 0
    for v in labels.iloc[::-1]:
        if v != last:
            break
        n += 1
    return n


def log_today(closes, std=None):
    """Append today's regime to output/regime_log.csv (once per day). Never raises."""
    try:
        cur = current(closes)
        if cur is None:
            return None
        old = pd.read_csv(LOG) if os.path.exists(LOG) else pd.DataFrame()
        day = str(pd.Timestamp(cur["date"]).date())
        if not old.empty and day in set(old["date"].astype(str)):
            return cur
        row = {"date": day, **{k: v for k, v in cur.items() if k != "date"},
               "recorded_at": datetime.now().isoformat(timespec="seconds")}
        os.makedirs(OUT, exist_ok=True)
        pd.DataFrame([row]).to_csv(LOG, mode="a", header=not os.path.exists(LOG), index=False)
        print(f"Market regime today: {cur['regime']} ({cur['confidence']:.0%} confidence, "
              f"{cur['days_in_regime']} day{'s' if cur['days_in_regime'] != 1 else ''} in a row).")
        return cur
    except Exception as e:
        print(f"(regime not logged: {e})")
        return None


# ---------------------------------------------------------------- report
def analyse(closes, mix=0.25, target_vol=0.10, profile="classic"):
    f = market_features(closes)
    m = fit(f)
    kw = dict(target_vol=target_vol, max_gross=4.0, max_weight=0.25, cost_bps=3.0)
    tf = pf.build_portfolio(closes, **kw)
    lo = pf.build_portfolio(closes, long_only=True, class_budget=pf.PROFILES[profile]["long_budget"], **kw)
    ret = pf.blend(tf, lo, mix, **kw)["ret"]
    # the regime known at the END of day t applies to the return of day t+1 (no look-ahead)
    lab = m["labels"].shift(1).reindex(ret.index).dropna()
    r = ret.reindex(lab.index)
    rows = []
    for name in m["names"].values():
        x = r[lab == name]
        if len(x) < 5:
            continue
        rows.append({"regime": name, "share_of_days": len(x) / len(r), "days": len(x),
                     "bot_return_per_year": x.mean() * pf.PPY,
                     "bot_swings_per_year": x.std() * np.sqrt(pf.PPY),
                     "bot_sharpe": x.mean() / x.std() * np.sqrt(pf.PPY) if x.std() > 0 else np.nan,
                     "avg_run_days": _avg_run(m["labels"], name),
                     **{k: f.loc[m["labels"] == name, k].mean() for k in FEATURES}})
    table = pd.DataFrame(rows).sort_values("volatility")
    trans = pd.crosstab(m["labels"].shift(1), m["labels"], normalize="index")
    return f, m, table, trans, ret


def _avg_run(labels, name):
    runs, n = [], 0
    for v in labels:
        if v == name:
            n += 1
        elif n:
            runs.append(n); n = 0
    if n:
        runs.append(n)
    return float(np.mean(runs)) if runs else np.nan


def report(closes, profile="classic", out=OUT):
    """Write output/regimes.html from already-loaded prices (used by the bot after each run)."""
    from types import SimpleNamespace
    f, m, table, trans, _ = analyse(closes, profile=profile)
    os.makedirs(out, exist_ok=True)
    pd.DataFrame({"regime": m["labels"], **{k: f[k] for k in FEATURES}}).to_csv(os.path.join(out, "regime_history.csv"))
    write_html(SimpleNamespace(out=out, simulate=False), f, m, table, trans)


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--yahoo", action="store_true", help="use Yahoo Finance data instead of MT5")
    src.add_argument("--simulate", action="store_true")
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--profile", choices=list(pf.PROFILES), default="classic")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    if a.yahoo or a.simulate:
        import compare
        closes, _, _ = compare.load("2005-01-01", simulate=a.simulate)
        if a.simulate:
            closes = closes.iloc[:, :14]
    else:
        symbols = [s for syms in pf.UNIVERSE.values() for s in syms]
        closes, _ = pf.load_universe(symbols, a.bars)
    print("Finding regimes…")
    f, m, table, trans, ret = analyse(closes, profile=a.profile)
    hist = pd.DataFrame({"regime": m["labels"], **{k: f[k] for k in FEATURES}})
    os.makedirs(a.out, exist_ok=True)
    hist.to_csv(os.path.join(a.out, "regime_history.csv"))
    print(f"\n{f.index[0].date()} → {f.index[-1].date()}")
    for t in table.itertuples():
        print(f"  {t.regime:48s} {t.share_of_days:5.0%} of days   bot {t.bot_return_per_year:+6.1%}/yr  "
              f"Sharpe {t.bot_sharpe:+.2f}   lasts ~{t.avg_run_days:.0f} days")
    print(f"  Today: {m['labels'].iloc[-1]}")
    write_html(a, f, m, table, trans)
    print(f"Report: {os.path.join(a.out, 'regimes.html')}")


def write_html(a, f, m, table, trans):
    names = list(table["regime"])
    pal = ["#2a78d6", "#0b7a35", "#eda100", "#c4302b", "#8a8984", "#9b59b6"]
    color = {n: pal[i % len(pal)] for i, n in enumerate(names)}
    step = max(1, len(f) // 900)
    lab = m["labels"]
    chart = {"dates": [d.strftime("%Y-%m-%d") for d in f.index[::step]],
             "vol": f["volatility"].values[::step].round(3).tolist(),
             "regime": lab.values[::step].tolist(), "colors": color}
    p = lambda x: f"{x:+.1%}" if pd.notna(x) else "–"
    rows = "".join(
        f"<tr><td><span class='dot' style='background:{color[t.regime]}'></span>{t.regime}</td><td>{t.share_of_days:.0%}</td>"
        f"<td>{t.avg_run_days:.0f} days</td><td>{t.volatility:+.2f}</td><td>{t.correlation:.2f}</td>"
        f"<td>{t.trend_strength:.2f}</td><td>{t.trend_direction:+.2f}</td>"
        f"<td class='{'pos' if t.bot_return_per_year > 0 else 'neg'}'>{p(t.bot_return_per_year)}</td>"
        f"<td>{t.bot_sharpe:+.2f}</td></tr>" for t in table.itertuples())
    trows = "".join("<tr><td>" + r + "</td>" + "".join(f"<td>{trans.loc[r, c]:.0%}</td>" if c in trans.columns else "<td>–</td>"
                                                         for c in names) + "</tr>" for r in names if r in trans.index)
    today = lab.iloc[-1]
    conf = float(m["proba"].iloc[-1].max())
    sim = "<p class='warn'>⚠ Simulated data: this only tests the program.</p>" if getattr(a, "simulate", False) else ""
    html = PAGE.format(start=f.index[0].date(), end=f.index[-1].date(), rows=rows, trows=trows,
                       heads="".join(f"<th>{n}</th>" for n in names), today=today, conf=f"{conf:.0%}",
                       today_color=color.get(today, "#888"), run=_run_length(lab), sim=sim, chart=json.dumps(chart))
    with open(os.path.join(a.out, "regimes.html"), "w", encoding="utf-8") as fh:
        fh.write(html)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Regimes</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#0b0b0b;--ink2:#5d5c58;--line:#e6e5e0;--good:#0b7a35;--bad:#c4302b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1e;--ink:#f3f2ee;--ink2:#b5b4ad;--line:#33332f;--good:#4cc27a;--bad:#ec6b66}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.55 system-ui,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:auto;padding:24px 16px 40px}} h1{{font-size:23px;margin:0 0 4px}} h2{{font-size:16.5px;margin:0 0 10px}}
.sub,.note{{color:var(--ink2)}} .note{{font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{text-align:right;padding:8px;border-bottom:1px solid var(--line);white-space:nowrap}}
th:first-child,td:first-child{{text-align:left;white-space:normal}} th{{color:var(--ink2);font-weight:600;font-size:12.5px}}
td.pos{{color:var(--good)}} td.neg{{color:var(--bad)}} .dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}}
.today{{font-size:20px;font-weight:700}} .warn{{background:#eda10022;border:1px solid #eda100;border-radius:8px;padding:10px 12px}} canvas{{max-height:300px}}
</style></head><body><main>
<h1>Market regimes</h1>
<p class="sub">{start} → {end} · found by unsupervised learning (Gaussian mixture), no labels given · not financial advice</p>
{sim}
<div class="card"><h2>Today</h2><p class="today"><span class="dot" style="background:{today_color}"></span>{today}</p>
<p class="sub">Model confidence {conf}; {run} trading day(s) in this regime so far.</p></div>
<div class="card"><h2>The regimes it found, and how the bot did in each</h2>
<table><tr><th>Regime</th><th>Share of days</th><th>Typically lasts</th><th>Jumpiness</th><th>Moving together</th><th>Trend strength</th><th>Trend direction</th><th>Bot return / year</th><th>Bot Sharpe</th></tr>{rows}</table>
<p class="note"><b>Jumpiness</b>: 0 = normal for these markets, +0.5 ≈ 65% jumpier, −0.5 ≈ 40% calmer. <b>Moving together</b>: average correlation between markets (0 = independent, 1 = in lockstep). <b>Trend strength</b>: 0 = no trends, 1 = every market trending strongly. <b>Trend direction</b>: −1 all down … +1 all up. <b>Bot return</b> uses the regime known the day before, so it's a fair test, not hindsight. Regimes with only a few days give unreliable numbers.</p></div>
<div class="card"><h2>Regime over time</h2><canvas id="tl"></canvas><p class="note">Line: market jumpiness. Dot colour: the regime that day.</p></div>
<div class="card"><h2>How regimes change (from row to column, next day)</h2>
<table><tr><th>From \\ to</th>{heads}</tr>{trows}</table>
<p class="note">High numbers on the diagonal mean regimes are "sticky": once in a mood, markets tend to stay there for a while.</p></div>
</main><script>
const D={chart};
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
Chart.defaults.color=css('--ink2');Chart.defaults.borderColor=css('--line');
new Chart(document.getElementById('tl'),{{type:'line',data:{{labels:D.dates,datasets:[{{label:'Jumpiness',data:D.vol,
 borderColor:css('--ink2'),borderWidth:1,pointRadius:2.2,pointBackgroundColor:D.regime.map(r=>D.colors[r]),pointBorderWidth:0}}]}},
 options:{{responsive:true,animation:false,plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{afterLabel:c=>D.regime[c.dataIndex]}}}}}},
 scales:{{x:{{ticks:{{maxTicksLimit:8}},grid:{{display:false}}}}}}}}}});
</script></body></html>"""

if __name__ == "__main__":
    main()

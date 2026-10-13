"""Trading research bot: rule-based strategies + ML direction model.

Examples
  python run.py --ticker SPY                 # real data from Yahoo Finance
  python run.py --ticker BTC-USD --horizon 3
  python run.py --csv my_prices.csv
  python run.py --mt5 EURUSD --timeframe H4  # data from your MetaTrader 5 terminal (Windows)
  python run.py --simulate                   # fake data, to test the pipeline

Outputs (in ./output):
  report.html        results, charts, and today's signals
  signals_log.csv    one row per run: the latest signal from every model
"""
import argparse, itertools, json, os
from datetime import datetime
import numpy as np
import pandas as pd

import data
from strategies import STRATEGIES, PARAM_GRIDS
from ml_model import walk_forward_probabilities, ml_positions
from backtest import run_backtest, stats as _stats

PPY = 252  # bars per year, set from the data source in main()


def stats(bt):
    return _stats(bt, PPY)


def tune(df, name, tune_end, cost, short):
    """Pick the strategy's params by Sharpe on the TUNING period only."""
    grid = PARAM_GRIDS.get(name, {})
    keys = list(grid)
    best, best_sharpe = {}, -np.inf
    for combo in itertools.product(*grid.values()) if keys else [()]:
        params = dict(zip(keys, combo))
        target = STRATEGIES[name](df, short=short, **params)
        s = stats(run_backtest(df.loc[:tune_end], target, cost))["sharpe"]
        if np.nan_to_num(s, nan=-np.inf) > best_sharpe:
            best, best_sharpe = params, s
    return best


def build_parser():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ticker")
    src.add_argument("--csv")
    src.add_argument("--mt5", metavar="SYMBOL", help="symbol from MetaTrader 5, e.g. EURUSD")
    src.add_argument("--simulate", action="store_true")
    ap.add_argument("--timeframe", default="D1", help="MT5 candle size: M15, M30, H1, H4, D1, W1")
    ap.add_argument("--bars", type=int, default=5000, help="MT5: how many candles to download")
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--side", choices=["auto", "long", "both"], default="auto",
                    help="long = buy-only; both = long and short; auto = both for MT5/CSV/simulated, long for --ticker")
    ap.add_argument("--horizon", type=int, default=5, help="ML predicts direction N bars ahead")
    ap.add_argument("--enter", type=float, default=0.55, help="ML: go long above this P(up) (short below 1-enter)")
    ap.add_argument("--exit", type=float, default=0.45, help="ML: close a long below this P(up) (a short above 1-exit)")
    ap.add_argument("--cost-bps", type=float, default=10.0, help="fees+slippage per trade, basis points")
    ap.add_argument("--test-frac", type=float, default=0.4, help="share of history held out for testing")
    ap.add_argument("--out", default="output")
    return ap


def load(a):
    """Returns (df, label, bars_per_year) for the chosen data source."""
    if a.ticker:
        return data.load_yahoo(a.ticker, a.start), a.ticker, 252
    if a.mt5:
        tf = a.timeframe.upper()
        return data.load_mt5(a.mt5, tf, a.bars), f"{a.mt5} {tf}", data.BARS_PER_YEAR[tf]
    if a.csv:
        return data.load_csv(a.csv), os.path.basename(a.csv), 252
    return data.simulate(), "SIMULATED", 252


def evaluate(df, label, a, verbose=True):
    """Tune, test and score every strategy and the ML model on one dataset."""
    if len(df) < 1500:
        raise ValueError(f"{label}: need at least 1500 bars of history; got {len(df)}. "
                         "Use --bars to download more, or a smaller --timeframe.")
    short = a.side == "both" or (a.side == "auto" and not a.ticker)
    split = int(len(df) * (1 - a.test_frac))
    tune_end, test_start = df.index[split - 1], df.index[split]
    test = df.loc[test_start:]
    if verbose:
        print(f"{label}: {len(df)} bars | {'long + short' if short else 'long only'} | "
              f"tuning to {tune_end} | testing {test_start} → {df.index[-1]}")

    results, curves, latest = {}, {}, {}
    bh = run_backtest(test, pd.Series(1, index=df.index), a.cost_bps)
    results["Buy & hold"], curves["Buy & hold"] = stats(bh), bh

    # Rule-based strategies: tuned on the first part, judged on the unseen part
    for name in STRATEGIES:
        params = tune(df, name, tune_end, a.cost_bps, short)
        target = STRATEGIES[name](df, short=short, **params)
        bt = run_backtest(test, target, a.cost_bps)
        key = f"{name} {params}" if params else name
        results[key], curves[key] = stats(bt), bt
        latest[key] = int(target.iloc[-1])
        if verbose:
            print(f"  {key:55s} sharpe {results[key]['sharpe']:.2f}")

    # ML model: walk-forward, so every prediction is out-of-sample
    probs, ml_metrics = walk_forward_probabilities(df, horizon=a.horizon, fib=True)
    # Same model without the Fibonacci features: does Fibonacci actually help?
    probs_nofib, m_nofib = walk_forward_probabilities(df, horizon=a.horizon, fib=False)
    ml_metrics["auc_nofib"], ml_metrics["acc_nofib"] = m_nofib["auc"], m_nofib["accuracy"]
    nofib_key = f"ML without Fibonacci ({a.horizon} bars)"
    bt0 = run_backtest(test, ml_positions(probs_nofib, a.enter, a.exit, short), a.cost_bps)
    results[nofib_key], curves[nofib_key] = stats(bt0), bt0
    ml_target = ml_positions(probs, a.enter, a.exit, short)
    ml_key = f"ML direction ({a.horizon} bars)"
    bt = run_backtest(test, ml_target, a.cost_bps)
    results[ml_key], curves[ml_key] = stats(bt), bt
    latest[ml_key] = int(ml_target.iloc[-1])
    if verbose:
        print(f"  {ml_key:55s} sharpe {results[ml_key]['sharpe']:.2f} | "
              f"accuracy {ml_metrics['accuracy']:.1%} vs always-up {ml_metrics['base_rate_up']:.1%} | AUC {ml_metrics['auc']:.3f}"
              f" (without Fibonacci: {ml_metrics['auc_nofib']:.3f})")
    return dict(test=test, results=results, curves=curves, latest=latest,
                probs=probs, ml_metrics=ml_metrics, ml_key=ml_key, short=short)


def main():
    a = build_parser().parse_args()
    global PPY
    df, label, PPY = load(a)
    try:
        r = evaluate(df, label, a)
    except ValueError as e:
        raise SystemExit(str(e))
    os.makedirs(a.out, exist_ok=True)
    log_signals(a.out, label, df, r["latest"], r["probs"])
    write_report(a, label, df, r["test"], r["results"], r["curves"], r["latest"],
                 r["probs"], r["ml_metrics"], r["ml_key"], r["short"])
    print(f"Report: {os.path.join(a.out, 'report.html')}")


SIDE = {1: "LONG", 0: "CASH", -1: "SHORT"}


def log_signals(out, label, df, latest, probs):
    """Monitoring: append today's signals so changes can be tracked run to run."""
    path = os.path.join(out, "signals_log.csv")
    row = {"run_at": datetime.now().isoformat(timespec="seconds"), "asset": label,
           "data_date": df.index[-1].date().isoformat(), "close": round(df["Close"].iloc[-1], 4),
           "ml_prob_up": round(float(probs.iloc[-1]), 4) if pd.notna(probs.iloc[-1]) else None,
           **{k.split(" {")[0]: SIDE[v] for k, v in latest.items()}}
    prev = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    pd.concat([prev, pd.DataFrame([row])], ignore_index=True).to_csv(path, index=False)


def write_report(a, label, df, test, results, curves, latest, probs, ml_metrics, ml_key, short=False):
    rule_keys = [k for k in results if k != "Buy & hold" and not k.startswith("ML")]
    best_rule = max(rule_keys, key=lambda k: np.nan_to_num(results[k]["sharpe"], nan=-9))
    shown = ["Buy & hold", best_rule, ml_key]
    intraday = (test.index.hour != 0).any() or (test.index.minute != 0).any()
    dates = [d.strftime("%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d") for d in test.index]
    step = max(1, len(dates) // 600)  # thin points so the page stays light
    chart = {
        "dates": dates[::step],
        "equity": {k: (curves[k]["equity"].values[::step] - 1).round(4).tolist() for k in shown},
        "drawdown": {k: (curves[k]["equity"] / curves[k]["equity"].cummax() - 1).values[::step].round(4).tolist() for k in shown},
        "prob": probs.reindex(test.index).values[::step].round(4).tolist(),
        "names": shown, "enter": a.enter, "exit": a.exit,
    }
    pct = lambda x: f"{x:+.1%}" if pd.notna(x) else "–"
    rows = "".join(
        f"<tr><td>{k}</td><td>{pct(r['total_return'])}</td><td>{pct(r['cagr'])}</td>"
        f"<td>{r['sharpe']:.2f}</td><td>{r['max_drawdown']:.1%}</td><td>{r['time_invested']:.0%}</td><td>{r['time_short']:.0%}</td>"
        f"<td>{r['trades']}</td></tr>" for k, r in sorted(results.items(), key=lambda kv: -np.nan_to_num(kv[1]['sharpe'], nan=-9)))
    cls = {1: "in", 0: "out", -1: "short"}
    sig = "".join(f"<li><span class='pill {cls[v]}'>{SIDE[v]}</span>{k}</li>" for k, v in latest.items())
    p_now = probs.iloc[-1]
    sim_note = ("<p class='warn'>⚠ Simulated data. These numbers only prove the pipeline works; "
                "they say nothing about real markets.</p>") if label == "SIMULATED" else ""
    edge = ml_metrics["accuracy"] - ml_metrics["base_rate_up"]

    html = TEMPLATE.format(
        label=label, side_text="Long and short" if short else "Long only", start=test.index[0].date(), end=test.index[-1].date(), sim_note=sim_note,
        rows=rows, signals=sig, data_date=df.index[-1].date(),
        p_now=f"{p_now:.0%}" if pd.notna(p_now) else "–", horizon=a.horizon,
        acc=f"{ml_metrics['accuracy']:.1%}", base=f"{ml_metrics['base_rate_up']:.1%}",
        edge=f"{edge:+.1%}", auc=f"{ml_metrics['auc']:.3f}", cost=a.cost_bps,
        auc0=f"{ml_metrics['auc_nofib']:.3f}", fib_verdict=fib_verdict(ml_metrics),
        chart_json=json.dumps(chart))
    with open(os.path.join(a.out, "report.html"), "w", encoding="utf-8") as f:
        f.write(html)


def fib_verdict(m):
    d = m["auc"] - m["auc_nofib"]
    if abs(d) < 0.01:
        return "No meaningful difference: Fibonacci levels added no real information here."
    return ("Fibonacci features helped slightly; confirm on other assets before trusting it." if d > 0
            else "Fibonacci features made the model slightly worse (likely noise).")


TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trading Bot Report</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{{--bg:#fcfcfb;--card:#fff;--ink:#0b0b0b;--ink2:#52514e;--line:#e6e5e0;--s1:#8a8984;--s2:#2a78d6;--s3:#eb6834;--good:#008300;--bad:#c4302b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--card:#242422;--ink:#fff;--ink2:#c3c2b7;--line:#3a3a37;--s1:#9b9a94;--s2:#3987e5;--s3:#d95926;--good:#3fae3f;--bad:#e66767}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1000px;margin:auto;padding:24px 16px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:17px;margin:0 0 12px}} .sub{{color:var(--ink2);margin:0 0 20px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:18px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} @media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}} th,td{{text-align:right;padding:7px 6px;border-bottom:1px solid var(--line)}}
th:first-child,td:first-child{{text-align:left}} th{{color:var(--ink2);font-weight:600}} .scroll{{overflow-x:auto}}
ul{{list-style:none;padding:0;margin:0}} li{{padding:5px 0;font-size:13.5px}}
.pill{{display:inline-block;width:46px;text-align:center;border-radius:4px;font-size:11px;font-weight:700;padding:2px 0;margin-right:8px;color:#fff}}
.in{{background:var(--good)}} .out{{background:var(--ink2)}} .short{{background:var(--bad)}}
.big{{font-size:34px;font-weight:700}} .muted{{color:var(--ink2);font-size:13px}}
.warn{{background:#eda10022;border:1px solid #eda100;border-radius:8px;padding:10px 12px}}
canvas{{max-height:300px}}
</style></head><body><main>
<h1>{label}: strategy report</h1>
<p class="sub">Out-of-sample test period {start} → {end}. {side_text}. Costs of {cost} bps per trade included. Not financial advice.</p>
{sim_note}
<div class="grid">
 <div class="card"><h2>Current signals (data to {data_date})</h2><ul>{signals}</ul></div>
 <div class="card"><h2>ML model</h2>
  <div class="big">{p_now}</div><div class="muted">probability price is higher in {horizon} bars (candles)</div>
  <p class="muted" style="margin-top:14px">Out-of-sample accuracy <b>{acc}</b> vs <b>{base}</b> for always guessing "up" (edge {edge}). AUC {auc} (0.5 = coin flip).</p>
  <p class="muted"><b>Fibonacci check:</b> AUC with Fibonacci features {auc} vs without {auc0}. {fib_verdict}</p></div>
</div>
<div class="card"><h2>Growth of $1 (return)</h2><canvas id="eq"></canvas></div>
<div class="card"><h2>Drawdown from peak</h2><canvas id="dd"></canvas></div>
<div class="card"><h2>ML probability of "up" vs entry/exit thresholds</h2><canvas id="pr"></canvas></div>
<div class="card scroll"><h2>All strategies, test period (ranked by Sharpe)</h2>
<table><tr><th>Strategy</th><th>Total</th><th>CAGR</th><th>Sharpe</th><th>Max DD</th><th>In market</th><th>Short</th><th>Trades</th></tr>{rows}</table></div>
<p class="muted">Rule parameters were picked on earlier data only; the ML model was retrained walk-forward and never saw the period it was scored on. Past performance does not predict future results.</p>
</main><script>
const D={chart_json};
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const cols=[css('--s1'),css('--s2'),css('--s3')];
Chart.defaults.color=css('--ink2');Chart.defaults.borderColor=css('--line');Chart.defaults.font.family='system-ui';
const base=(fmt)=>({{responsive:true,animation:false,interaction:{{mode:'index',intersect:false}},
 plugins:{{legend:{{position:'top',align:'start',labels:{{boxWidth:12,boxHeight:2}}}},
 tooltip:{{callbacks:{{label:c=>c.dataset.label+': '+fmt(c.parsed.y)}}}}}},
 scales:{{x:{{ticks:{{maxTicksLimit:8}},grid:{{display:false}}}},y:{{ticks:{{callback:fmt}}}}}},elements:{{point:{{radius:0,hoverRadius:4}},line:{{borderWidth:2}}}}}});
const pctf=v=>(v*100).toFixed(0)+'%';
const ds=key=>D.names.map((n,i)=>({{label:n,data:D[key][n],borderColor:cols[i],backgroundColor:cols[i]}}));
new Chart(eq,{{type:'line',data:{{labels:D.dates,datasets:ds('equity')}},options:base(pctf)}});
new Chart(dd,{{type:'line',data:{{labels:D.dates,datasets:ds('drawdown')}},options:base(pctf)}});
const flat=v=>D.dates.map(()=>v);
new Chart(pr,{{type:'line',data:{{labels:D.dates,datasets:[
 {{label:'P(up)',data:D.prob,borderColor:cols[2],backgroundColor:cols[2]}},
 {{label:'Enter',data:flat(D.enter),borderColor:css('--good'),borderDash:[5,4],borderWidth:1.5}},
 {{label:'Exit',data:flat(D.exit),borderColor:css('--bad'),borderDash:[5,4],borderWidth:1.5}}]}},
 options:{{...base(pctf),scales:{{x:{{ticks:{{maxTicksLimit:8}},grid:{{display:false}}}},y:{{min:0,max:1,ticks:{{callback:pctf}}}}}}}}}});
</script></body></html>"""

if __name__ == "__main__":
    main()

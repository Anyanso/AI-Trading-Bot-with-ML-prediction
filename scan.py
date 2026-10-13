"""Scan many symbols with every strategy and the ML model, then rank what works.

    python scan.py --cost-bps 2
    python scan.py --symbols EURUSD GBPUSD XAUUSD --timeframe H4 --bars 10000 --horizon 6
    python scan.py --simulate            # test the scanner without MT5

Output: output/scan.html and output/scan_results.csv

Why scan: a strategy that only works on one symbol is probably luck. One that
holds up across many symbols, on data it never saw, is worth a closer look.
"""
import argparse, os
import numpy as np
import pandas as pd

import data
import run

DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "XAUUSD"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--simulate", action="store_true", help="use fake data instead of MT5")
    ap.add_argument("--timeframe", default="D1")
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--side", choices=["long", "both"], default="both")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--enter", type=float, default=0.55)
    ap.add_argument("--exit", type=float, default=0.45)
    ap.add_argument("--cost-bps", type=float, default=2.0)
    ap.add_argument("--test-frac", type=float, default=0.4)
    ap.add_argument("--out", default="output")
    a = ap.parse_args()
    a.ticker = None  # evaluate() treats this as a non-stock source
    tf = a.timeframe.upper()
    run.PPY = 252 if a.simulate else data.BARS_PER_YEAR[tf]

    rows, signals = [], []
    for i, sym in enumerate(a.symbols):
        try:
            df = data.simulate(seed=100 + i) if a.simulate else data.load_mt5(sym, tf, a.bars)
            r = run.evaluate(df, sym, a, verbose=False)
        except (SystemExit, ValueError) as e:
            print(f"  {sym:8s} skipped: {e}")
            continue
        for key, st in r["results"].items():
            rows.append({"symbol": sym, "strategy": key.split(" {")[0], "params": key[len(key.split(" {")[0]):].strip(),
                         **{k: st[k] for k in ("sharpe", "total_return", "cagr", "max_drawdown", "trades")}})
        m = r["ml_metrics"]
        signals.append({"symbol": sym, "ml_auc": m["auc"], "ml_auc_no_fib": m["auc_nofib"],
                        "ml_prob_up_now": r["probs"].iloc[-1],
                        **{k.split(" {")[0]: run.SIDE[v] for k, v in r["latest"].items()}})
        best = max((k for k in r["results"] if k != "Buy & hold"),
                   key=lambda k: np.nan_to_num(r["results"][k]["sharpe"], nan=-9))
        print(f"  {sym:8s} best: {best.split(' {')[0]:22s} sharpe {r['results'][best]['sharpe']:+.2f} | ML AUC {m['auc']:.3f}")

    if not rows:
        raise SystemExit("No symbols could be tested.")
    res = pd.DataFrame(rows)
    os.makedirs(a.out, exist_ok=True)
    res.to_csv(os.path.join(a.out, "scan_results.csv"), index=False)

    # Robustness summary: how does each strategy do ACROSS symbols?
    summ = (res.groupby("strategy")
               .agg(median_sharpe=("sharpe", "median"), mean_sharpe=("sharpe", "mean"),
                    positive=("sharpe", lambda s: int((s > 0).sum())), symbols=("sharpe", "size"),
                    median_return=("total_return", "median"), worst_drawdown=("max_drawdown", "min"))
               .sort_values("median_sharpe", ascending=False))
    print("\nAcross all symbols (out-of-sample):")
    print(summ.to_string(float_format=lambda x: f"{x:.2f}"))
    write_html(a, tf, res, summ, pd.DataFrame(signals))
    print(f"\nReport: {os.path.join(a.out, 'scan.html')}")


def write_html(a, tf, res, summ, sig):
    pct = lambda x: f"{x:+.1%}"
    srows = "".join(
        f"<tr><td>{k}</td><td>{r.median_sharpe:+.2f}</td><td>{r.mean_sharpe:+.2f}</td>"
        f"<td>{int(r.positive)} / {int(r.symbols)}</td><td>{pct(r.median_return)}</td><td>{r.worst_drawdown:.1%}</td></tr>"
        for k, r in summ.iterrows())
    piv = res.pivot_table(index="strategy", columns="symbol", values="sharpe").loc[summ.index]

    def cell(v):
        if pd.isna(v):
            return "<td>–</td>"
        tone = "pos" if v >= 0.5 else "neg" if v <= -0.3 else ""
        return f"<td class='{tone}'>{v:+.2f}</td>"
    head = "".join(f"<th>{c}</th>" for c in piv.columns)
    prows = "".join(f"<tr><td>{k}</td>{''.join(cell(v) for v in r.values)}</tr>" for k, r in piv.iterrows())
    sig_cols = [c for c in sig.columns if c not in ("symbol", "ml_auc", "ml_auc_no_fib", "ml_prob_up_now")]
    pill = {"LONG": "in", "SHORT": "short", "CASH": "out"}
    sigrows = "".join(
        f"<tr><td>{r.symbol}</td><td>{r.ml_auc:.3f}</td><td>{r.ml_prob_up_now:.0%}</td>"
        + "".join(f"<td><span class='pill {pill[r[c]]}'>{r[c]}</span></td>" for c in sig_cols) + "</tr>"
        for _, r in sig.iterrows())
    sig_head = "".join(f"<th>{c.split(' (')[0]}</th>" for c in sig_cols)
    side = "long and short" if a.side == "both" else "long only"
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Strategy Scan</title><style>
:root{{--bg:#fcfcfb;--card:#fff;--ink:#0b0b0b;--ink2:#52514e;--line:#e6e5e0;--good:#008300;--bad:#c4302b;--goodbg:#00830018;--badbg:#c4302b18}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--card:#242422;--ink:#fff;--ink2:#c3c2b7;--line:#3a3a37;--good:#3fae3f;--bad:#e66767;--goodbg:#3fae3f22;--badbg:#e6676722}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}} main{{max-width:1100px;margin:auto;padding:24px 16px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:17px;margin:0 0 6px}} .sub,.note{{color:var(--ink2);font-size:13.5px}} .sub{{margin:0 0 20px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:18px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}} th,td{{text-align:right;padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} th{{color:var(--ink2);font-weight:600}}
td.pos{{background:var(--goodbg);color:var(--good);font-weight:600}} td.neg{{background:var(--badbg);color:var(--bad)}}
.pill{{display:inline-block;width:52px;text-align:center;border-radius:4px;font-size:11px;font-weight:700;padding:2px 0;color:#fff}}
.in{{background:var(--good)}} .out{{background:var(--ink2)}} .short{{background:var(--bad)}}
</style></head><body><main>
<h1>Strategy scan: {tf}, {side}</h1>
<p class="sub">{len(piv.columns)} symbols. Every number is out-of-sample (last {a.test_frac:.0%} of each history), after {a.cost_bps} bps costs per trade. Not financial advice.</p>
<div class="card"><h2>Which strategies hold up across symbols?</h2>
<p class="note">A real edge should show a positive Sharpe on most symbols, not just one. Median Sharpe above ~0.5 is worth a closer look.</p>
<table><tr><th>Strategy</th><th>Median Sharpe</th><th>Mean Sharpe</th><th>Positive on</th><th>Median return</th><th>Worst drawdown</th></tr>{srows}</table></div>
<div class="card"><h2>Sharpe by symbol</h2><p class="note">Green ≥ +0.50, red ≤ −0.30.</p>
<table><tr><th>Strategy</th>{head}</tr>{prows}</table></div>
<div class="card"><h2>Current signals</h2><p class="note">ML AUC: 0.50 = coin flip; above ~0.53 suggests some predictive power.</p>
<table><tr><th>Symbol</th><th>ML AUC</th><th>P(up) now</th>{sig_head}</tr>{sigrows}</table></div>
</main></body></html>"""
    with open(os.path.join(a.out, "scan.html"), "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()

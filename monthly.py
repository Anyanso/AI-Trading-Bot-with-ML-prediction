"""Monthly report and scorecard: is the bot doing what it should, month by month?

Reads the logs the bot writes on every run and answers, for each calendar month:
  * how much the account made, against what the bank would have paid,
  * how closely the live account followed the model ("shadow" profit: what the research
    portfolio should have made with the same targets, before rounding, skipped markets and costs),
  * worst dip, order problems, and what trading cost (spread, commission, overnight swap),
  * how good the predictions were (from the prediction journal) and which market regime dominated.

Then a scorecard with fixed rules, each marked PASS, FAIL or TOO EARLY. The rules are set in
advance (below) so the goalposts can't move after seeing the results.

    python monthly.py        # writes output/monthly.html and output/monthly_report.csv

The control panel shows the same page under "Monthly report".
"""
import html, json, os
from datetime import datetime
import numpy as np
import pandas as pd

OUT = "output"
MAGIC = 260826

# ---- the rules, fixed in advance -------------------------------------------------------------
RULES = {
    "runs":      {"min_share": 0.90, "min_weekdays": 10},
    "orders":    {"max_rejected": 0.02, "min_orders": 20},
    "tracking":  {"min_corr": 0.80, "max_gap_per_year": 0.03, "min_rows": 15},
    "costs":     {"max_per_year": 0.01, "min_days": 30},
    "risk":      {"low": 0.7, "high": 1.3, "min_days": 60},
    "month":     {"worst_allowed": -0.06},
    "bank":      {"min_days": 365},
    "ml":        {"min_scored": 500},
    "trend":     {"min_calls": 500},
}
BANK_RATE_DEFAULT = 0.03     # yearly; change with "bank_rate" in settings.json


def _csv(name, **kw):
    p = os.path.join(OUT, name)
    try:
        return pd.read_csv(p, **kw) if os.path.exists(p) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _settings():
    try:
        with open("settings.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load(login=None):
    acct = _csv("account_log.csv")
    if acct.empty:
        return None
    acct["time"] = pd.to_datetime(acct["time"])
    if "login" in acct:
        login = login or acct["login"].iloc[-1]
        acct = acct[acct["login"].astype(str) == str(login)]
    acct = acct.sort_values("time")
    start = acct["time"].min()
    trades = _csv("trade_log.csv")
    if not trades.empty:
        trades["time"] = pd.to_datetime(trades["time"])
        if "login" in trades:
            trades = trades[(trades["login"].astype(str) == str(login)) | trades["login"].isna()]
        trades = trades[trades["time"] >= start - pd.Timedelta(days=1)]
    shadow = _csv("shadow_log.csv")
    if not shadow.empty:
        shadow["time"] = pd.to_datetime(shadow["time"])
        if "login" in shadow:
            shadow = shadow[shadow["login"].astype(str) == str(login)]
    preds = _csv("predictions.csv")
    if not preds.empty:
        preds["signal_date"] = pd.to_datetime(preds["signal_date"])
        preds = preds[preds["signal_date"] >= start.normalize() - pd.Timedelta(days=1)]
    regimes = _csv("regime_log.csv")
    if not regimes.empty:
        regimes["date"] = pd.to_datetime(regimes["date"])
    return {"login": login, "acct": acct, "trades": trades, "shadow": shadow, "preds": preds, "regimes": regimes}


def deal_costs(mt5):
    """Commission + fees and swap per deal, from MT5's own history (empty without MT5)."""
    if mt5 is None:
        return pd.DataFrame()
    try:
        got = mt5.history_deals_get(datetime(2000, 1, 1), datetime.now()) or []
    except Exception:
        return pd.DataFrame()
    rows = [{"time": pd.Timestamp(d.time, unit="s"),
             "commission": getattr(d, "commission", 0.0) + getattr(d, "fee", 0.0),
             "swap": getattr(d, "swap", 0.0)} for d in got if getattr(d, "magic", None) == MAGIC]
    return pd.DataFrame(rows)


def _max_dip(eq):
    return float((eq / eq.cummax() - 1).min()) if len(eq) > 1 else 0.0


def monthly_table(d, deals, bank_rate):
    import predictions as P
    acct, trades, shadow, preds, regimes = d["acct"], d["trades"], d["shadow"], d["preds"], d["regimes"]
    eq = acct.set_index("time")["equity"].astype(float)
    months = pd.period_range(eq.index.min(), eq.index.max(), freq="M")
    rows = []
    for m in months:
        a, b = m.start_time, m.end_time
        before = eq[eq.index < a]
        inside = eq[(eq.index >= a) & (eq.index <= b)]
        if inside.empty:
            continue
        start_eq = float(before.iloc[-1]) if len(before) else float(inside.iloc[0])
        end_eq = float(inside.iloc[-1])
        first_t = before.index[-1] if len(before) else inside.index[0]
        days = max((inside.index[-1] - first_t).total_seconds() / 86400, 0)
        path = pd.concat([before.iloc[-1:], inside]) if len(before) else inside
        r = {"month": str(m), "start_equity": start_eq, "end_equity": end_eq,
             "return": end_eq / start_eq - 1 if start_eq else np.nan, "days": days,
             "bank_return": bank_rate * days / 365, "worst_dip": _max_dip(path)}
        r["vs_bank"] = r["return"] - r["bank_return"]
        sh = shadow[(shadow["time"] >= a) & (shadow["time"] <= b)] if not shadow.empty else shadow
        r["live_pnl"] = float(sh["live_pnl"].sum()) if len(sh) else np.nan
        r["model_pnl"] = float(sh["model_pnl"].sum()) if len(sh) else np.nan
        r["tracking_gap"] = (r["live_pnl"] - r["model_pnl"]) / start_eq if len(sh) else np.nan
        tr = trades[(trades["time"] >= a) & (trades["time"] <= b)] if not trades.empty else trades
        if len(tr):
            ok = tr["ok"].astype(str) == "True"
            closed = tr["result"].astype(str).str.contains("closed", case=False)
            r.update(orders=int(len(tr)), rejected=int((~ok & ~closed).sum()), market_closed=int((~ok & closed).sum()),
                     spread_cost=float(pd.to_numeric(tr.get("spread_cost", 0), errors="coerce").fillna(0).sum()))
        else:
            r.update(orders=0, rejected=0, market_closed=0, spread_cost=0.0)
        dl = deals[(deals["time"] >= a) & (deals["time"] <= b)] if not deals.empty else deals
        r["commission_fees"] = float(-dl["commission"].sum()) if len(dl) else 0.0
        r["swap"] = float(-dl["swap"].sum()) if len(dl) else 0.0
        r["total_cost"] = r["spread_cost"] + r["commission_fees"] + r["swap"]
        pr = preds[(preds["signal_date"] >= a) & (preds["signal_date"] <= b)] if not preds.empty else preds
        st = P.stats(pr) if len(pr) else P.stats(pd.DataFrame())
        r.update(ml_scored=st["scored"], ml_hit=st["ml_hit_rate"], ml_auc=st["ml_auc"], trend_hit=st["trend_hit_rate"])
        rg = regimes[(regimes["date"] >= a) & (regimes["date"] <= b)] if not regimes.empty else regimes
        r["main_regime"] = rg["regime"].mode().iloc[0] if len(rg) else ""
        rows.append(r)
    return pd.DataFrame(rows)


def scorecard(d, table, deals, bank_rate, target_vol):
    import predictions as P
    acct, trades, shadow, preds = d["acct"], d["trades"], d["shadow"], d["preds"]
    eq = acct.set_index("time")["equity"].astype(float)
    t0, t1 = eq.index.min(), eq.index.max()
    days = max((t1 - t0).total_seconds() / 86400, 0)
    cards = []

    def card(key, title, status, value, rule):
        cards.append({"key": key, "title": title, "status": status, "value": value, "rule": rule})

    # 1. runs
    R = RULES["runs"]
    weekdays = pd.bdate_range(t0.normalize(), t1.normalize())
    run_days = set(acct.loc[acct.get("note", pd.Series("", index=acct.index)).astype(str).str.contains("rebalance"),
                            "time"].dt.normalize())
    share = len(run_days & set(weekdays)) / len(weekdays) if len(weekdays) else 0
    card("runs", "Runs every weekday", "too early" if len(weekdays) < R["min_weekdays"] else
         "pass" if share >= R["min_share"] else "fail",
         f"{len(run_days & set(weekdays))} of {len(weekdays)} weekdays", f"at least {R['min_share']:.0%} of weekdays")
    # 2. orders
    R = RULES["orders"]
    n = int(table["orders"].sum()) if len(table) else 0
    rej = int(table["rejected"].sum()) if len(table) else 0
    card("orders", "Orders accepted by the broker", "too early" if n < R["min_orders"] else
         "pass" if rej / n <= R["max_rejected"] else "fail",
         f"{rej} rejected of {n} ({rej / n:.1%})" if n else "no orders yet",
         f"at most {R['max_rejected']:.0%} rejected (not counting 'market closed')")
    # 3. tracking
    R = RULES["tracking"]
    if len(shadow) >= 2:
        corr = shadow["live_pnl"].corr(shadow["model_pnl"])
        gap = (shadow["live_pnl"].sum() - shadow["model_pnl"].sum()) / eq.mean()
        gap_yr = gap * 365 / max(days, 1)
        status = "too early" if len(shadow) < R["min_rows"] else \
            "pass" if corr >= R["min_corr"] and abs(gap_yr) <= R["max_gap_per_year"] else "fail"
        val = f"correlation {corr:.2f}, gap {gap_yr:+.1%} a year"
    else:
        status, val = "too early", f"{len(shadow)} comparisons so far"
    card("tracking", "Live account follows the model", status, val,
         f"day-to-day correlation ≥ {R['min_corr']:.2f} and gap within ±{R['max_gap_per_year']:.0%} a year")
    # 4. costs
    R = RULES["costs"]
    cost = float(table["total_cost"].sum()) if len(table) else 0.0
    cost_yr = cost / eq.mean() * 365 / max(days, 1) if days else 0.0
    card("costs", "Trading costs", "too early" if days < R["min_days"] else
         "pass" if cost_yr <= R["max_per_year"] else "fail",
         f"${cost:,.2f} so far ≈ {cost_yr:.2%} a year", f"under {R['max_per_year']:.0%} a year")
    # 5. risk
    R = RULES["risk"]
    wk = eq.resample("W-FRI").last().dropna().pct_change().dropna()
    vol = float(wk.std() * np.sqrt(52)) if len(wk) >= 4 else np.nan
    lo, hi = R["low"] * target_vol, R["high"] * target_vol
    card("risk", "Swings in the target range", "too early" if days < R["min_days"] or np.isnan(vol) else
         "pass" if lo <= vol <= hi else "fail",
         f"{vol:.1%} a year" if not np.isnan(vol) else "needs a few weeks",
         f"between {lo:.0%} and {hi:.0%} a year (target {target_vol:.0%})")
    # 6. worst month
    R = RULES["month"]
    full = table[table["days"] >= 20] if len(table) else table
    worst = float(full["return"].min()) if len(full) else np.nan
    card("month", "No big monthly loss", "too early" if not len(full) else
         "pass" if worst >= R["worst_allowed"] else "fail",
         f"worst full month {worst:+.1%}" if len(full) else "no full month yet", f"no month below {R['worst_allowed']:.0%}")
    # 7. bank
    R = RULES["bank"]
    total = float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) > 1 else 0.0
    bank = bank_rate * days / 365
    card("bank", "Beats the bank", "too early" if days < R["min_days"] else "pass" if total > bank else "fail",
         f"{total:+.2%} vs bank {bank:+.2%} over {days:.0f} days",
         f"more than the bank ({bank_rate:.1%} a year) after a full year")
    # 8. ML, 9. trend signals
    st = P.stats(preds) if len(preds) else P.stats(pd.DataFrame())
    v, why = P.verdict(st, RULES["ml"]["min_scored"])
    card("ml", "ML predictions beat a coin flip", v, why, f"AUC clearly above 0.5 on ≥ {RULES['ml']['min_scored']} unseen predictions")
    R = RULES["trend"]
    th = st["trend_hit_rate"]
    se = 0.5 / np.sqrt(max(st["trend_calls"], 1))
    card("trend", "Trend signals call direction", "too early" if st["trend_calls"] < R["min_calls"] or np.isnan(th) else
         "pass" if th - 2 * se > 0.5 else "fail",
         f"{th:.1%} right on {st['trend_calls']} calls" if not np.isnan(th) else "no scored calls yet",
         f"clearly above 50% on ≥ {R['min_calls']} calls")
    return cards, {"days": days, "total": total, "bank": bank, "vol": vol, "cost_yr": cost_yr}


def regime_breakdown(d):
    """Live account change grouped by the regime logged that day."""
    sh, rg = d["shadow"], d["regimes"]
    if sh.empty or rg.empty:
        return pd.DataFrame()
    x = sh.copy()
    x["date"] = x["time"].dt.normalize()
    x = x.merge(rg[["date", "regime"]], on="date", how="left")
    x["regime"] = x["regime"].fillna("not recorded")
    g = x.groupby("regime").agg(days=("live_pnl", "size"), live_pnl=("live_pnl", "sum"), model_pnl=("model_pnl", "sum"))
    return g.reset_index().sort_values("days", ascending=False)


def build(mt5=None):
    """Return the report page as HTML (and save it plus the CSV)."""
    import execute
    s = _settings()
    bank_rate = float(s.get("bank_rate", BANK_RATE_DEFAULT))
    login = None
    if mt5 is not None:
        try:
            login = mt5.account_info().login
        except Exception:
            pass
    d = load(login)
    if d is None or d["acct"].empty:
        page = PAGE.format(body="<div class='card'><p>No live runs recorded yet. The monthly report fills in after the "
                                "bot's first <b>Trade now</b> or automatic run.</p></div>", login="–", now=datetime.now().strftime("%d %b %Y %H:%M"))
        return page
    demo = str(d["acct"]["demo"].iloc[-1]) == "True" if "demo" in d["acct"] else True
    preset = execute.RISK_PRESETS.get(s.get("risk", "normal"), execute.RISK_PRESETS["normal"])
    target_vol = preset["target_vol"] * (1 if demo else 0.5)
    deals = deal_costs(mt5)
    table = monthly_table(d, deals, bank_rate)
    cards, tot = scorecard(d, table, deals, bank_rate, target_vol)
    regimes = regime_breakdown(d)
    os.makedirs(OUT, exist_ok=True)
    table.to_csv(os.path.join(OUT, "monthly_report.csv"), index=False)
    page = PAGE.format(body=_body(table, cards, tot, regimes, bank_rate), login=html.escape(str(d["login"])),
                       now=datetime.now().strftime("%d %b %Y %H:%M"))
    with open(os.path.join(OUT, "monthly.html"), "w", encoding="utf-8") as f:
        f.write(page)
    return page


def _body(table, cards, tot, regimes, bank_rate):
    p = lambda x, d=1: "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.{d}%}"
    usd = lambda x: "–" if x is None or (isinstance(x, float) and np.isnan(x)) else ("−" if x < 0 else "") + f"${abs(x):,.2f}"
    tone = lambda x: "" if x is None or (isinstance(x, float) and np.isnan(x)) else "pos" if x > 0 else "neg" if x < 0 else ""
    label = {"pass": "PASS", "fail": "FAIL", "too early": "TOO EARLY"}
    n_pass = sum(c["status"] == "pass" for c in cards)
    n_fail = sum(c["status"] == "fail" for c in cards)
    grid = "".join(
        f"<div class='sc {c['status'].replace(' ', '-')}'><div class='chip'>{label[c['status']]}</div>"
        f"<div class='t'>{html.escape(c['title'])}</div><div class='v'>{html.escape(c['value'])}</div>"
        f"<div class='r'>Rule: {html.escape(c['rule'])}</div></div>" for c in cards)
    rows = "".join(
        f"<tr><td><b>{r.month}</b></td><td class='{tone(r['return'])}'>{p(r['return'], 2)}</td><td>{p(r.bank_return, 2)}</td>"
        f"<td class='{tone(r.vs_bank)}'>{p(r.vs_bank, 2)}</td><td>{p(r.worst_dip, 1)}</td>"
        f"<td class='{tone(r.live_pnl)}'>{usd(r.live_pnl)}</td><td>{usd(r.model_pnl)}</td><td>{p(r.tracking_gap, 2)}</td>"
        f"<td>{r.orders}</td><td>{r.rejected}</td><td>{usd(r.total_cost)}</td>"
        f"<td>{'–' if np.isnan(r.ml_hit) else f'{r.ml_hit:.0%}'} <span class='sub'>({r.ml_scored})</span></td>"
        f"<td>{'–' if np.isnan(r.trend_hit) else f'{r.trend_hit:.0%}'}</td><td>{html.escape(str(r.main_regime))}</td></tr>"
        for _, r in table.iloc[::-1].iterrows()) or "<tr><td colspan=14 class='sub'>No months yet.</td></tr>"
    reg = ""
    if not regimes.empty:
        reg_rows = "".join(f"<tr><td>{html.escape(r.regime)}</td><td>{r.days}</td><td class='{tone(r.live_pnl)}'>{usd(r.live_pnl)}</td>"
                           f"<td>{usd(r.model_pnl)}</td></tr>" for r in regimes.itertuples())
        reg = (f"<div class='card'><h2>By market regime</h2><table><tr><th>Regime</th><th>Days</th><th>Live account</th>"
               f"<th>Model (shadow)</th></tr>{reg_rows}</table><p class='note'>Regimes come from the unsupervised model "
               f"(regimes.py). Over months this shows which market moods suit the bot.</p></div>")
    return f"""
<div class="card"><h2>Scorecard</h2>
<p class="sub">{n_pass} pass · {n_fail} fail · {len(cards) - n_pass - n_fail} too early · {tot['days']:.0f} days of live record ·
account {p(tot['total'], 2)} vs bank {p(tot['bank'], 2)}</p>
<div class="grid">{grid}</div>
<p class="note">The rules were fixed before any results came in, so they can't be bent afterwards. "Too early" means there
isn't enough data yet to judge fairly. A real edge in results takes 1–2 years to show; mechanics, costs and risk can be judged in 1–3 months.</p></div>
<div class="card"><h2>Month by month</h2>
<table><tr><th>Month</th><th>Account</th><th>Bank</th><th>vs bank</th><th>Worst dip</th><th>Live profit</th><th>Model profit</th>
<th>Gap</th><th>Orders</th><th>Rejected</th><th>Costs</th><th>ML right</th><th>Trend right</th><th>Main regime</th></tr>{rows}</table>
<p class="note"><b>Bank</b>: {bank_rate:.1%} a year for the same days (change "bank_rate" in settings.json). <b>Model profit</b>:
what the research portfolio would have made with the same daily targets, before rounding to lot sizes, skipped markets and costs.
<b>Gap</b>: live minus model, as % of the account; small and steady means the bot does what the research says.
<b>ML right</b>: share of the ML model's 5-day calls that came true (number scored in brackets). Saved as output/monthly_report.csv.</p></div>
{reg}"""


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monthly Report</title><style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#0b0b0b;--ink2:#5d5c58;--line:#e6e5e0;--good:#0b7a35;--bad:#c4302b;--goodbg:#0b7a3514;--badbg:#c4302b12;--grey:#8a898414}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1e;--ink:#f3f2ee;--ink2:#b5b4ad;--line:#33332f;--good:#4cc27a;--bad:#ec6b66;--goodbg:#4cc27a1c;--badbg:#ec6b661c;--grey:#ffffff10}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.55 system-ui,"Segoe UI",sans-serif}}
main{{max-width:1100px;margin:auto;padding:24px 16px 40px}} h1{{font-size:23px;margin:0 0 4px}} h2{{font-size:16.5px;margin:0 0 10px}}
.sub,.note{{color:var(--ink2)}} .note{{font-size:13px}} a{{color:inherit}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px;overflow-x:auto}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin:12px 0}}
.sc{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--grey)}} .sc.pass{{background:var(--goodbg);border-color:var(--good)}} .sc.fail{{background:var(--badbg);border-color:var(--bad)}}
.chip{{display:inline-block;font-size:11px;font-weight:800;letter-spacing:.04em;padding:2px 8px;border-radius:99px;background:var(--line)}}
.pass .chip{{background:var(--good);color:#fff}} .fail .chip{{background:var(--bad);color:#fff}}
.sc .t{{font-weight:700;margin-top:6px}} .sc .v{{margin-top:2px}} .sc .r{{font-size:12.5px;color:var(--ink2);margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}} th,td{{text-align:right;padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} th{{color:var(--ink2);font-weight:600;font-size:12px}} td.pos{{color:var(--good)}} td.neg{{color:var(--bad)}}
</style></head><body><main>
<h1>Monthly report</h1><p class="sub">Account {login} · generated {now} · <a href="/">back to the control panel</a> · not financial advice</p>
{body}
</main></body></html>"""


def main():
    mt5 = None
    try:
        import data
        mt5 = data.mt5_connect()
    except BaseException:
        print("(MT5 not available: commission and swap will show as 0)")
    try:
        build(mt5)
    finally:
        if mt5 is not None:
            mt5.shutdown()
    print(f"Report: {os.path.join(OUT, 'monthly.html')}  ·  table: {os.path.join(OUT, 'monthly_report.csv')}")


if __name__ == "__main__":
    main()

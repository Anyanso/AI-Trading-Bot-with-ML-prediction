"""Shareable signal report: today's market readings in plain English.

    python signals.py        # writes output/signal_report.html and output/signal_message.txt

Built from the bot's latest run (output/signal_history.csv) and its own track record
(output/account_log.csv). Needs no MT5 connection. Meant for sharing for free with family
and friends, as information about what an automated model is doing, not as advice.
"""
import html, os, webbrowser
from datetime import datetime
import pandas as pd

from dashboard import name

OUT = "output"
MIN_TRACK_DAYS = 60   # below this, the report says the record is too short to judge

TREND = {4: ("Strong uptrend", "up2"), 3: ("Uptrend", "up1"), 2: ("Mixed, no clear trend", "flat"),
         1: ("Downtrend", "dn1"), 0: ("Strong downtrend", "dn2")}
CLASS = {"Forex": "Currencies", "Metals": "Metals", "Indices": "Stock markets", "Bonds": "Bonds",
         "Commodities": "Commodities"}
DISCLAIMER = ("These readings come from an automated computer model that I'm testing on a practice account. "
              "They are information, not financial advice. The model can be wrong, past results don't predict "
              "future results, and leveraged trading can lose more than you put in. Do your own research and "
              "only risk money you can afford to lose.")


def jumpiness(vol):
    if pd.isna(vol):
        return ""
    return "calm" if vol < 0.08 else "normal" if vol < 0.20 else "jumpy"


def load():
    hp = os.path.join(OUT, "signal_history.csv")
    if not os.path.exists(hp):
        raise SystemExit("No signals yet. Run the bot once (Preview or Trade now) first.")
    h = pd.read_csv(hp)
    h["time"] = pd.to_datetime(h["time"])
    h["day"] = h.time.dt.date
    latest_t = h.time.max()
    cur = h[h.time == latest_t].copy()
    prev_days = h[h.day < latest_t.date()]
    prev = prev_days[prev_days.time == prev_days.time.max()] if not prev_days.empty else pd.DataFrame()
    return cur, prev, latest_t


def track_record():
    ap = os.path.join(OUT, "account_log.csv")
    if not os.path.exists(ap):
        return None
    a = pd.read_csv(ap)
    if "demo" in a:
        a = a[a.demo.astype(str) == "True"] if (a.demo.astype(str) == "True").any() else a
    if a.empty:
        return None
    a["time"] = pd.to_datetime(a["time"])
    eq = a.equity.astype(float)
    return {"start": a.time.iloc[0], "days": (a.time.iloc[-1] - a.time.iloc[0]).days,
            "ret": eq.iloc[-1] / eq.iloc[0] - 1, "worst": float((eq / eq.cummax() - 1).min())}


def build(open_browser=False):
    import portfolio as pf
    import symbols
    cur, prev, when = load()
    pv = dict(zip(prev.symbol, prev.votes_up)) if not prev.empty else {}
    rows = []
    for _, r in cur.iterrows():
        if pd.isna(r.votes_up):
            continue
        v = int(r.votes_up)
        before = pv.get(r.symbol)
        change = ""
        if before is not None and not pd.isna(before) and int(before) != v:
            change = ("strengthened" if v > before else "weakened") + f" ({int(before)}→{v} of 4 signals up)"
        rows.append({"symbol": r.symbol, "name": name(r.symbol), "cls": CLASS.get(pf.CLASS_OF.get(r.symbol) or pf.CLASS_OF.get(symbols.standard(r.symbol), ""), "Other"),
                     "votes": v, "trend": TREND[v][0], "tone": TREND[v][1], "jumpy": jumpiness(r.vol),
                     "weight": float(r.weight), "change": change,
                     "watch": r.near_flip if isinstance(r.near_flip, str) else ""})
    df = pd.DataFrame(rows).sort_values(["votes", "name"], ascending=[False, True])
    tr = track_record()
    date_s = when.strftime("%a %d %b %Y")

    # ---- short message for WhatsApp / Telegram
    def names(sub):
        return ", ".join(sub.name) if not sub.empty else "none"
    lines = [f"📊 Market readings · {date_s}", "(from my automated model: info, not advice)", ""]
    lines.append(f"⬆️ Uptrends: {names(df[df.votes >= 3])}")
    lines.append(f"⬇️ Downtrends: {names(df[df.votes <= 1])}")
    lines.append(f"↔️ No clear trend: {names(df[df.votes == 2])}")
    ch = df[df.change != ""]
    if not ch.empty:
        lines += ["", "Changed since last time:"] + [f"• {r['name']} {r.change}" for _, r in ch.iterrows()]
    w = df[df.watch != ""]
    if not w.empty:
        lines += ["", "Could change soon:"] + [f"• {r['name']}: {r.watch}" for _, r in w.head(6).iterrows()]
    if tr:
        lines += ["", f"Model's practice-account result so far: {tr['ret']:+.1%} over {tr['days']} days "
                      f"(worst dip {tr['worst']:.1%})."]
        if tr["days"] < MIN_TRACK_DAYS:
            lines.append("⚠️ Still early: too short a record to judge.")
    lines += ["", "Not financial advice. Trading can lose money; only risk what you can afford to lose."]
    msg = "\n".join(lines)

    # ---- shareable page
    def group_rows(sub):
        out = ""
        for _, r in sub.iterrows():
            if r.weight < -0.001:
                side = "Betting on a fall"
            elif r.weight > 0.001:
                side = ("Holding more than usual" if r.votes >= 3 else
                        "Holding less than usual" if r.votes <= 1 else "Holding a normal amount")
            else:
                side = "Not holding"
            dots = "".join("<i class='u'></i>" if i < r.votes else "<i></i>" for i in range(4))
            extra = ""
            if r.change:
                extra += f"<div class='chg'>Trend {html.escape(r.change)}</div>"
            if r.watch:
                extra += f"<div class='watch'>Watch: {html.escape(r.watch)}</div>"
            out += (f"<tr><td><b>{html.escape(r['name'])}</b>{extra}</td>"
                    f"<td><span class='tr {r.tone}'>{r.trend}</span></td><td class='dots'>{dots}</td>"
                    f"<td>{r.jumpy}</td><td class='sub'>{side}</td></tr>")
        return out
    sections = ""
    for cls in ["Metals", "Stock markets", "Bonds", "Commodities", "Currencies", "Other"]:
        sub = df[df.cls == cls]
        if not sub.empty:
            sections += (f"<h3>{cls}</h3><table><tr><th>Market</th><th>Trend reading</th><th>Signals up</th>"
                         f"<th>Movement</th><th>Model's position</th></tr>{group_rows(sub)}</table>")
    if tr:
        early = (f"<p class='warn'>Still early: {tr['days']} days is too short to tell whether the model works. "
                 f"Treat these readings as an experiment.</p>") if tr["days"] < MIN_TRACK_DAYS else ""
        rec = (f"<div class='kpis'><div><span>Practice account result</span><b class='{'pos' if tr['ret'] >= 0 else 'neg'}'>"
               f"{tr['ret']:+.1%}</b></div><div><span>Since</span><b>{tr['start']:%d %b %Y}</b></div>"
               f"<div><span>Worst dip</span><b>{tr['worst']:.1%}</b></div></div>{early}")
    else:
        rec = "<p class='warn'>No track record yet.</p>"
    page = PAGE.format(date=date_s, rec=rec, sections=sections, disclaimer=html.escape(DISCLAIMER),
                       msg=html.escape(msg))
    os.makedirs(OUT, exist_ok=True)
    hp, tp = os.path.join(OUT, "signal_report.html"), os.path.join(OUT, "signal_message.txt")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(page)
    with open(tp, "w", encoding="utf-8") as f:
        f.write(msg)
    if open_browser:
        webbrowser.open("file://" + os.path.abspath(hp))
    return page, msg, hp, tp


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Readings</title><style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#0b0b0b;--ink2:#5d5c58;--line:#e6e5e0;--accent:#2a78d6;--good:#0b7a35;--bad:#c4302b;--warn:#8a5a00;--warnbg:#eda1001f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1e;--ink:#f3f2ee;--ink2:#b5b4ad;--line:#33332f;--accent:#3987e5;--good:#4cc27a;--bad:#ec6b66;--warn:#e0a83a;--warnbg:#e0a83a1f}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:860px;margin:auto;padding:22px 16px 40px}} h1{{font-size:23px;margin:0}} h2{{font-size:16.5px;margin:0 0 8px}}
h3{{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink2);margin:18px 0 4px}}
.sub{{color:var(--ink2);font-size:13px}} .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-top:16px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;table-layout:fixed}} th:nth-child(1){{width:36%}} th:nth-child(2){{width:20%}} th:nth-child(3){{width:13%}} th:nth-child(4){{width:12%}}
@media(max-width:600px){{table{{table-layout:auto}}}} td,th{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-size:12.5px;color:var(--ink2);font-weight:600}}
.tr{{font-weight:600;font-size:13.5px;white-space:nowrap}} .up2,.up1{{color:var(--good)}} .dn2,.dn1{{color:var(--bad)}} .flat{{color:var(--ink2)}}
.dots i{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:3px;background:var(--line)}} .dots i.u{{background:var(--accent)}} .dots{{white-space:nowrap}}
.chg{{font-size:12.5px;color:var(--accent);margin-top:2px}} .watch{{font-size:12.5px;color:var(--warn);margin-top:2px}}
.kpis{{display:flex;gap:28px;flex-wrap:wrap}} .kpis span{{display:block;font-size:12.5px;color:var(--ink2)}} .kpis b{{font-size:21px}}
.pos{{color:var(--good)}} .neg{{color:var(--bad)}} .warn{{background:var(--warnbg);border-radius:8px;padding:9px 12px;font-size:14px}}
.disc{{font-size:13px;color:var(--ink2);border-left:3px solid var(--line);padding-left:10px}}
pre{{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;font:14px/1.45 system-ui}}
button{{border:0;background:var(--accent);color:#fff;font:inherit;font-weight:600;padding:8px 14px;border-radius:8px;cursor:pointer}}
</style></head><body><main>
<h1>Market readings</h1><p class="sub">{date} · from an automated trend model · information, not advice</p>
<p class="disc">{disclaimer}</p>
<div class="card"><h2>How has the model done?</h2>{rec}</div>
<div class="card"><h2>Today's readings</h2>
<p class="sub" style="margin-top:0">Each market gets four trend checks, from about one week to one year. The more point up, the stronger the uptrend. "Movement" is how much the price has been swinging lately. The model always keeps some of every market and leans more or less into each one with the trend, so "holding less than usual" is its way of being cautious.</p>
{sections}</div>
<div class="card"><h2>Short version to paste into a message</h2><pre id="m">{msg}</pre>
<button onclick="navigator.clipboard.writeText(document.getElementById('m').innerText).then(()=>this.textContent='Copied ✓')">Copy message</button></div>
</main></body></html>"""


if __name__ == "__main__":
    _, msg, hp, tp = build(open_browser=True)
    print(msg)
    print(f"\nSaved: {hp} and {tp}")

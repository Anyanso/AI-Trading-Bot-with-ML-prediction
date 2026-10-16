"""Control panel: the dashboard with buttons. Runs only on your own computer.

    python app.py          # or double-click "Start bot.bat"

Opens http://127.0.0.1:8765 in your browser. Keep this window open while you use it
(and while auto-trading is switched on).

Safety
  * Listens on this computer only (127.0.0.1); nothing on the network can reach it.
  * Every button that changes something needs a secret token that only this page has,
    so other websites can't press the buttons for you.
  * The buttons can never trade a real-money account: they run execute.py without --real,
    which refuses real accounts. Real trading stays a deliberate command-line step.
"""
import html, json, os, secrets, subprocess, sys, threading, time, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import alerts
import dashboard
import markettime as mtime
from execute import MAGIC, RISK_PRESETS, SETTINGS_FILE, load_settings

HOST, PORT = "127.0.0.1", 8765
TOKEN = secrets.token_urlsafe(24)
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"risk": "normal", "profile": "classic", "auto": False, "auto_time": "08:45", "last_auto": "",
            "alerts_on": False, "daily_summary": True, "alert_email": "", "last_deal_check": 0, "last_dd_warn": "",
            "auto_mode": "market", "dst_alerts_sent": []}

JOB = {"running": False, "kind": "", "lines": [], "ok": None, "started": "", "finished": "", "finished_ts": 0}
LOCK = threading.Lock()
MT5_LOCK = threading.Lock()   # one MT5 connection at a time inside the panel


# ---------------------------------------------------------------- settings
def settings():
    return {**DEFAULTS, **load_settings()}


def save_settings(new):
    s = settings()
    s.update(new)
    with open(os.path.join(HERE, SETTINGS_FILE), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    return s


# ---------------------------------------------------------------- running the bot
def start_job(kind):
    """kind: preview | live | closeall. Returns False if a job is already running."""
    args = {"preview": ["execute.py"],
            "live": ["execute.py", "--live"],
            "closeall": ["execute.py", "--close-all", "--live"]}[kind]
    with LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, kind=kind, lines=[], ok=None,
                   started=datetime.now().strftime("%H:%M:%S"), finished="")
    threading.Thread(target=_run, args=(args,), daemon=True).start()
    return True


def _run(args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    try:
        p = subprocess.Popen([sys.executable, *args], cwd=HERE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        for line in p.stdout:
            with LOCK:
                JOB["lines"].append(line.rstrip())
                JOB["lines"] = JOB["lines"][-400:]
        ok = p.wait() == 0
    except Exception as e:
        with LOCK:
            JOB["lines"].append(f"Could not start the bot: {e}")
        ok = False
    with LOCK:
        JOB.update(running=False, ok=ok, finished=datetime.now().strftime("%H:%M:%S"), finished_ts=time.time())


def watcher():
    """While the panel is open: every 10 minutes, alert right away if a safety stop closed a
    position, or if the account is getting close to the safety switch."""
    import data, pandas as pd
    while True:
        time.sleep(600)
        s = settings()
        if not s["alerts_on"] or JOB["running"]:
            continue
        if not MT5_LOCK.acquire(timeout=30):
            continue
        try:
            mt5 = data.mt5_connect()
        except BaseException:
            MT5_LOCK.release()
            continue
        try:
            since = float(s.get("last_deal_check") or time.time() - 600)
            deals = mt5.history_deals_get(datetime.fromtimestamp(since), datetime.now()) or []
            sl = getattr(mt5, "DEAL_REASON_SL", 4)
            for d in deals:
                if getattr(d, "magic", None) == MAGIC and getattr(d, "reason", None) == sl and d.time > since:
                    alerts.send("Trading bot: safety stop hit",
                                f"{dashboard.name(d.symbol)} ({d.symbol}) was closed by its safety stop at "
                                f"{d.price:g}, result {d.profit:+,.2f}. The bot will wait 3 days before buying back in.",
                                priority="high", tags="warning")
            acc = mt5.account_info()
            log = os.path.join(HERE, "output", "account_log.csv")
            if acc and os.path.exists(log):
                eq = pd.read_csv(log)
                if "login" in eq:
                    eq = eq[eq.login.astype(str) == str(acc.login)]
                if not eq.empty:
                    peak = max(float(eq.equity.max()), acc.equity)
                    dd = acc.equity / peak - 1
                    limit = RISK_PRESETS.get(s["risk"], RISK_PRESETS["normal"])["max_drawdown"]
                    today = datetime.now().date().isoformat()
                    if dd <= -0.7 * limit and s.get("last_dd_warn") != today:
                        alerts.send("Trading bot: account well below its peak",
                                    f"The account is {dd:.1%} below its peak. The safety switch closes everything "
                                    f"at -{limit:.0%} at the next run.", priority="high", tags="chart_with_downwards_trend")
                        save_settings({"last_dd_warn": today})
            save_settings({"last_deal_check": time.time()})
        except Exception:
            pass
        finally:
            mt5.shutdown()
            MT5_LOCK.release()


def due_time(s, day):
    """When the automatic run is due on `day`, on this PC's clock."""
    if s.get("auto_mode", "market") == "market":
        return mtime.run_time(day)              # 45 minutes after the US market opens
    hh, mm = map(int, s["auto_time"].split(":"))
    return datetime(day.year, day.month, day.day, hh, mm)


def clock_change_alerts():
    """Heads-up 3 days and 1 day before the US changes its clocks."""
    from datetime import timedelta
    s = settings()
    today = datetime.now().date()
    cur, change, nxt = mtime.season(today)
    days = (change - today).days
    key = f"{change.isoformat()}:{days}"
    if days not in (3, 1) or key in s.get("dst_alerts_sent", []):
        return
    o_now, _ = mtime.us_hours_local(today)
    o_new, _ = mtime.us_hours_local(change + timedelta(days=1))
    when = "in 3 days" if days == 3 else "tomorrow"
    if s.get("auto_mode", "market") == "market":
        todo = (f"Your bot follows this automatically: its daily run moves from {mtime.run_time(today):%H:%M} "
                f"to {mtime.run_time(change + timedelta(days=1)):%H:%M} your time. Nothing to do.")
    else:
        todo = (f"You use a fixed run time ({s['auto_time']}). Check it's at least an hour after the new "
                f"opening time, or switch to 'Follow US market hours' in the control panel.")
    alerts.send(f"Trading bot: US clocks change {when}",
                f"On {change:%A %d %B} the US switches to {nxt} time. US markets will open at "
                f"{o_new:%H:%M} your time instead of {o_now:%H:%M}. {todo}", tags="clock1")
    save_settings({"dst_alerts_sent": (s.get("dst_alerts_sent", []) + [key])[-20:]})


def scheduler():
    """Auto-trading: once per weekday at the scheduled time, while this app is open."""
    while True:
        try:
            s = settings()
            now = datetime.now()
            late = (now - due_time(s, now.date())).total_seconds()
            if (s["auto"] and now.weekday() < 5 and 0 <= late <= 3 * 3600   # catch up for up to 3 hours
                    and s["last_auto"] != now.date().isoformat() and not JOB["running"]):
                save_settings({"last_auto": now.date().isoformat()})
                start_job("live")
            clock_change_alerts()
        except Exception:
            pass
        time.sleep(30)


# ---------------------------------------------------------------- page
def next_run_text(s):
    """Plain-English 'next automatic run' line."""
    from datetime import timedelta
    if not s.get("auto"):
        return "Automatic trading is off."
    now = datetime.now()
    day = now.date()
    if s.get("last_auto") == day.isoformat() or now > due_time(s, day):
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    when = due_time(s, day)
    label = "today" if day == now.date() else "tomorrow" if day == now.date() + timedelta(days=1) else when.strftime("%A")
    return f"Next automatic run: {label} at {when:%H:%M} (if this window is open)."


def season_text():
    from datetime import timedelta
    today = datetime.now().date()
    cur, change, nxt = mtime.season(today)
    o, c = mtime.us_hours_local(today)
    o2, c2 = mtime.us_hours_local(change + timedelta(days=1))
    soon = (change - today).days <= 7
    txt = (f"US markets now: {o:%H:%M}–{c:%H:%M} your time ({cur} time). On {change:%a %d %b} the US switches to "
           f"{nxt} time and they'll be {o2:%H:%M}–{c2:%H:%M}.")
    return txt, soon


def controls_html():
    s = settings()
    risk_desc = {"cautious": "Small positions. Swings of about ±5% a year. Safety switch at −10%.",
                 "normal": "The tested default. Swings of about ±10% a year. Safety switch at −15%.",
                 "aggressive": "Bigger positions. Swings of about ±15% a year. Safety switch at −25%."}
    seg = "".join(
        f"<button class='seg rseg{' on' if s['risk'] == k else ''}' data-risk='{k}'>{k.title()}</button>" for k in RISK_PRESETS)
    prof_desc = {"classic": "The original mix: every market gets equal risk, currencies included. "
                            "Best for the MetaQuotes demo, where stock indices can't be traded.",
                 "balanced": "Stocks + bonds: half the steady part's risk in stock indices, 35% bonds, 15% gold and "
                             "commodities. Needs a broker with tradable stock indices. Run compare.py first."}
    pseg = "".join(f"<button class='seg pseg{' on' if s['profile'] == k else ''}' data-profile='{k}'>{k.title()}</button>"
                   for k in ("classic", "balanced"))
    halted = os.path.exists(os.path.join(HERE, "output", "HALTED"))
    topic = alerts.ensure_topic()
    season_txt, season_soon = season_text()
    resume = ("<button class='btn danger-o' id='resume'>Resume trading after the safety stop</button>"
              if halted else "")
    return f"""
<div class="card ctl">
 <div class="ctl-grid">
  <div>
   <h2>Risk level</h2>
   <div class="segs" id="risk">{seg}</div>
   <p class="sub" id="riskdesc">{html.escape(risk_desc[s['risk']])}</p>
   <p class="sub">A new level takes effect at the next trade run.</p>
   <h2 style="margin-top:18px">Strategy</h2>
   <div class="segs" id="profile">{pseg}</div>
   <p class="sub" id="profdesc">{html.escape(prof_desc.get(s['profile'], ''))}</p>
   <h2 style="margin-top:18px">Money to trade with</h2>
   <label class="auto">$ <input type="number" id="capital" min="100" step="100" placeholder="whole account"
     value="{'' if not s.get('capital') else int(s['capital'])}" style="width:140px"></label>
   <p class="sub">Leave empty to use the whole account. Set e.g. 1000 to size every position as if you only had $1,000.
   Press <b>Preview</b> to see which markets are then too small for the broker's minimum trade size.</p>
  </div>
  <div>
   <h2>Actions</h2>
   <div class="btns">
    <button class="btn" id="preview">Preview today's trades</button>
    <button class="btn primary" id="live">Trade now</button>
    <button class="btn danger-o" id="closeall">Close everything</button>
    <a class="btn" href="/signals" target="_blank">Signal report to share</a>
    <a class="btn" href="/monthly" target="_blank">Monthly report</a>
    <a class="btn" href="/regimes" target="_blank">Market regimes</a>
    {resume}
   </div>
   <label class="auto"><input type="checkbox" id="auto" {'checked' if s['auto'] else ''}> <b>Trade automatically every weekday</b></label>
   <div class="modes">
    <label><input type="radio" name="mode" value="market" {'checked' if s['auto_mode'] == 'market' else ''}>
     Follow US market hours <span class="sub">(recommended: 45 min after the US market opens, adjusts itself when clocks change)</span></label>
    <label><input type="radio" name="mode" value="fixed" {'checked' if s['auto_mode'] == 'fixed' else ''}>
     At a fixed time: <input type="time" id="autotime" value="{s['auto_time']}"></label>
   </div>
   <p class="sub {'soon' if season_soon else ''}">{html.escape(season_txt)}</p>
   <p class="sub" id="nextrun">{html.escape(next_run_text(s))}</p>
   <p class="sub">Auto-trading only works while this control panel window is open. You'll get a phone alert 3 days and 1 day before the US changes its clocks.</p>
  </div>
 </div>
 <details class="alerts" {'open' if not s['alerts_on'] else ''}><summary><b>Phone alerts</b> <span class="sub" id="alertstate">{'on' if s['alerts_on'] else 'off'}</span></summary>
  <ol class="sub">
   <li>Install the free <b>ntfy</b> app on your phone (App Store or Google Play).</li>
   <li>In the app, tap <b>+</b> and subscribe to this topic: <code id="topic">{html.escape(topic)}</code>
       <button class="btn mini" id="copytopic">Copy</button><br>Keep it private: anyone with the name can read your alerts.</li>
   <li>Switch alerts on below and press <b>Send test alert</b>.</li>
  </ol>
  <label class="auto"><input type="checkbox" id="alerts_on" {'checked' if s['alerts_on'] else ''}> Send alerts to my phone</label>
  <label class="auto"><input type="checkbox" id="daily_summary" {'checked' if s['daily_summary'] else ''}> Also send a short summary after each daily run</label>
  <label class="auto">Also email them to (optional): <input type="email" id="alert_email" placeholder="you@example.com" value="{html.escape(s['alert_email'])}"></label>
  <p class="sub">You'll be alerted when: a safety stop closes a position, the account gets close to the safety switch, the safety switch triggers, a run fails, or orders are rejected. Stop checks happen every 10 minutes while this window is open.</p>
  <button class="btn" id="testalert">Send test alert</button> <span class="sub" id="testres"></span>
 </details>
 <div id="job" class="job" hidden><div class="jobhead"><b id="jobtitle"></b><span id="jobstate" class="sub"></span></div><pre id="joblog"></pre></div>
</div>
<style>
.ctl h2{{margin-bottom:8px}} .ctl-grid{{display:grid;grid-template-columns:1fr 1.4fr;gap:22px}} @media(max-width:760px){{.ctl-grid{{grid-template-columns:1fr}}}}
.segs{{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}}
.seg{{border:0;background:transparent;color:var(--ink);padding:9px 16px;font:inherit;cursor:pointer;border-right:1px solid var(--line)}}
.seg:last-child{{border-right:0}} .seg.on{{background:var(--accent);color:#fff;font-weight:600}}
.btns{{display:flex;flex-wrap:wrap;gap:8px}}
a.btn{{text-decoration:none;display:inline-block}} .btn{{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:9px 14px;border-radius:9px;font:inherit;font-weight:600;cursor:pointer}}
.btn:hover{{border-color:var(--ink2)}} .btn.primary{{background:var(--accent);border-color:var(--accent);color:#fff}}
.btn.danger-o{{color:var(--bad);border-color:var(--bad)}} .btn:disabled{{opacity:.5;cursor:wait}}
.auto{{display:flex;align-items:center;gap:8px;margin-top:14px;flex-wrap:wrap}} .auto input[type=time]{{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}}
.modes{{margin:6px 0 2px 24px;display:grid;gap:6px}} .modes input[type=time]{{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}}
.soon{{color:var(--warn,#a46a00);font-weight:600}}
.alerts{{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}} .alerts summary{{cursor:pointer}} .alerts ol{{padding-left:20px}}
.alerts input[type=email]{{font:inherit;padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);min-width:220px}}
.btn.mini{{padding:2px 8px;font-size:12px;margin-left:4px}}
.job{{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}} .jobhead{{display:flex;justify-content:space-between;gap:8px}}
.job pre{{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;max-height:300px;overflow:auto;font-size:12.5px;white-space:pre-wrap;margin:8px 0 0}}
</style>
<script>
const TOKEN={json.dumps(TOKEN)};
const RISK_DESC={json.dumps(risk_desc)};
const PROF_DESC={json.dumps(prof_desc)};
async function post(url, body){{
  const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json','X-Token':TOKEN}},body:JSON.stringify(body||{{}})}});
  const j=await r.json().catch(()=>({{}})); if(!r.ok) alert(j.error||'Something went wrong'); return j;
}}
document.querySelectorAll('.rseg').forEach(b=>b.onclick=async()=>{{
  const k=b.dataset.risk;
  if(k==='aggressive' && !confirm('Aggressive means bigger positions: larger gains AND larger losses (worst past drop about −37%). Switch?')) return;
  await post('/api/settings',{{risk:k}});
  document.querySelectorAll('.rseg').forEach(x=>x.classList.toggle('on',x===b));
  document.getElementById('riskdesc').textContent=RISK_DESC[k];
}});
document.querySelectorAll('.pseg').forEach(b=>b.onclick=async()=>{{
  const k=b.dataset.profile;
  if(!confirm(k==='balanced'
     ? 'Switch to the Balanced (stocks + bonds) strategy? At the next run the bot will rebuild the portfolio: it sells the currency holdings of the steady part and buys stock indices, bonds and gold. Press Preview first to see the trades.'
     : 'Switch back to the Classic strategy? At the next run the bot will rebuild the portfolio. Press Preview first to see the trades.')) return;
  await post('/api/settings',{{profile:k}});
  document.querySelectorAll('.pseg').forEach(x=>x.classList.toggle('on',x===b));
  document.getElementById('profdesc').textContent=PROF_DESC[k];
}});
async function saveAuto(extra){{ const j=await post('/api/settings',extra); if(j.next_run) document.getElementById('nextrun').textContent=j.next_run; }}
document.getElementById('auto').onchange=e=>saveAuto({{auto:e.target.checked,auto_time:document.getElementById('autotime').value}});
document.getElementById('autotime').onchange=e=>saveAuto({{auto_time:e.target.value,auto_mode:'fixed'}});
document.querySelectorAll('input[name=mode]').forEach(r=>r.onchange=e=>saveAuto({{auto_mode:e.target.value}}));
document.getElementById('autotime').onfocus=()=>{{ document.querySelector('input[name=mode][value=fixed]').checked=true; }};
document.getElementById('capital').onchange=async e=>{{ await post('/api/settings',{{capital:e.target.value}}); }};
document.getElementById('alerts_on').onchange=e=>{{ post('/api/settings',{{alerts_on:e.target.checked}}); document.getElementById('alertstate').textContent=e.target.checked?'on':'off'; }};
document.getElementById('daily_summary').onchange=e=>post('/api/settings',{{daily_summary:e.target.checked}});
document.getElementById('alert_email').onchange=e=>post('/api/settings',{{alert_email:e.target.value}});
document.getElementById('copytopic').onclick=()=>navigator.clipboard.writeText(document.getElementById('topic').textContent);
document.getElementById('testalert').onclick=async()=>{{ const r=document.getElementById('testres'); r.textContent='Sending…';
  const j=await post('/api/test_alert'); r.textContent=j.delivered?'Sent ✓ Check your phone.':'Could not send. Check your internet connection.'; }};
const titles={{preview:"Preview of today's trades (nothing was sent)",live:'Trading now',closeall:'Closing all bot positions'}};
async function run(kind){{
  if(kind==='live' && !confirm('Place today\\'s trades on the practice account now?')) return;
  if(kind==='closeall' && !confirm('Close EVERY position the bot has opened?')) return;
  const j=await post('/api/run',{{kind}}); if(j.started) poll(true);
}}
['preview','live','closeall'].forEach(k=>document.getElementById(k).onclick=()=>run(k));
const rb=document.getElementById('resume'); if(rb) rb.onclick=async()=>{{ if(confirm('Allow the bot to trade again?')){{ await post('/api/resume'); location.reload(); }} }};
let wasRunning=false;
async function poll(force){{
  const j=await (await fetch('/api/job')).json();
  const box=document.getElementById('job');
  const recent = j.finished_ts && (Date.now()/1000 - j.finished_ts) < 1800;   // keep the last result on screen for 30 min
  if(j.kind && (j.running||force||wasRunning||recent)){{ box.hidden=false;
    document.getElementById('jobtitle').textContent=titles[j.kind]||j.kind;
    document.getElementById('jobstate').textContent=j.running?('running since '+j.started+' (takes 1–2 minutes)…'):(j.ok?'finished '+j.finished:'stopped with a problem at '+j.finished);
    const pre=document.getElementById('joblog'); pre.textContent=j.lines.join('\\n'); pre.scrollTop=pre.scrollHeight; }}
  document.querySelectorAll('.btns .btn').forEach(b=>b.disabled=j.running);
  if(wasRunning && !j.running){{ setTimeout(()=>location.reload(),2500); }}
  wasRunning=j.running;
  setTimeout(poll, j.running?1500:15000);
}}
poll(false);
// Refresh the account value and profit numbers once a minute, straight from MT5, without reloading the page.
function setNum(id,text,cls){{ const el=document.getElementById(id); if(!el) return; el.textContent=text;
  if(cls!==undefined){{ el.classList.remove('pos','neg'); if(cls) el.classList.add(cls); }} }}
async function liveTick(){{
  try{{
    if(!wasRunning){{
      const d=await (await fetch('/api/live')).json();
      if(d.equity){{
        setNum('k_eq',d.equity); setNum('k_since',d.since,d.since_cls); setNum('k_since_pct',d.since_pct);
        setNum('k_day',d.day,d.day_cls); setNum('k_open',d.open_pnl,d.open_cls);
        setNum('k_up',String(d.n_up)); setNum('k_dn',String(d.n_dn)); setNum('k_upd',d.time+' (live)');
        document.querySelectorAll('[data-pnl]').forEach(td=>{{ const p=d.positions[td.dataset.pnl];
          if(p){{ td.textContent=p.pnl; td.classList.remove('pos','neg'); if(p.cls) td.classList.add(p.cls); }} }});
      }}
    }}
  }}catch(e){{}}
  setTimeout(liveTick,60000);
}}
setTimeout(liveTick,60000);
</script>"""


def error_page(msg):
    return f"""<!doctype html><meta charset=utf-8><title>Trading bot</title>
<body style="font:16px system-ui;max-width:640px;margin:60px auto;padding:0 16px">
<h2>Can't reach MetaTrader 5</h2><p>{html.escape(msg)}</p>
<p>Open MetaTrader 5, log in, then <a href="/">reload this page</a>.</p></body>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        path = urlparse(self.path).path
        if path == "/":
            try:
                with MT5_LOCK:
                    page = dashboard.render(controls=controls_html())
                return self._send(200, page, "text/html")
            except BaseException as e:   # e.g. MT5 not open (data.mt5_connect raises SystemExit)
                return self._send(200, error_page(str(e)), "text/html")
        if path == "/signals":
            try:
                import signals
                return self._send(200, signals.build()[0], "text/html")
            except BaseException as e:
                return self._send(200, error_page(str(e)).replace("Can't reach MetaTrader 5", "No signal report yet"), "text/html")
        if path == "/monthly":
            try:
                import monthly
                with MT5_LOCK:
                    mt5 = None
                    try:
                        import data
                        mt5 = data.mt5_connect()
                    except BaseException:
                        pass
                    try:
                        page = monthly.build(mt5)
                    finally:
                        if mt5 is not None:
                            mt5.shutdown()
                return self._send(200, page, "text/html")
            except BaseException as e:
                return self._send(200, error_page(str(e)).replace("Can't reach MetaTrader 5", "Monthly report not available"), "text/html")
        if path == "/regimes":
            p = os.path.join(HERE, "output", "regimes.html")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html")
            return self._send(200, error_page("The regime report is created by the bot's next run "
                                              "(Preview or Trade now).").replace("Can't reach MetaTrader 5", "No regime report yet"), "text/html")
        if path == "/api/live":
            if not MT5_LOCK.acquire(timeout=10):
                return self._send(200, {"busy": True})
            try:
                return self._send(200, dashboard.live())
            except BaseException as e:
                return self._send(200, {"error": str(e)})
            finally:
                MT5_LOCK.release()
        if path == "/api/job":
            with LOCK:
                return self._send(200, dict(JOB))
        if path == "/api/settings":
            return self._send(200, settings())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, {"error": "This page is out of date. Please reload it."})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        path = urlparse(self.path).path
        if path == "/api/run":
            kind = body.get("kind")
            if kind not in ("preview", "live", "closeall"):
                return self._send(400, {"error": "unknown action"})
            if not start_job(kind):
                return self._send(409, {"error": "The bot is already running. Please wait for it to finish."})
            return self._send(200, {"started": True})
        if path == "/api/settings":
            new = {}
            if body.get("risk") in RISK_PRESETS:
                new["risk"] = body["risk"]
            if body.get("profile") in ("classic", "balanced"):
                new["profile"] = body["profile"]
            if isinstance(body.get("auto"), bool):
                new["auto"] = body["auto"]
                if body["auto"]:   # switched on after today's time: first run is the next weekday
                    probe = {**settings(), **{k: body[k] for k in ("auto_time", "auto_mode") if k in body}}
                    if datetime.now() > due_time(probe, datetime.now().date()):
                        new["last_auto"] = datetime.now().date().isoformat()
            if "capital" in body:
                try:
                    c = str(body["capital"]).strip()
                    new["capital"] = None if c in ("", "0") else max(100.0, float(c))
                except ValueError:
                    pass
            if body.get("auto_mode") in ("market", "fixed"):
                new["auto_mode"] = body["auto_mode"]
            for k in ("alerts_on", "daily_summary"):
                if isinstance(body.get(k), bool):
                    new[k] = body[k]
            if "alert_email" in body:
                e = str(body["alert_email"]).strip()
                if e == "" or ("@" in e and "." in e.split("@")[-1] and " " not in e and len(e) < 120):
                    new["alert_email"] = e
            t = str(body.get("auto_time", ""))
            if len(t) == 5 and t[2] == ":" and t.replace(":", "").isdigit():
                new["auto_time"] = t
            saved = save_settings(new)
            return self._send(200, {**saved, "next_run": next_run_text(saved)})
        if path == "/api/test_alert":
            ok = alerts.send("Trading bot: test alert", "Alerts are working. You'll hear from the bot here.",
                             tags="bell", force=True)
            return self._send(200, {"delivered": ok})
        if path == "/api/resume":
            f = os.path.join(HERE, "output", "HALTED")
            if os.path.exists(f):
                os.remove(f)
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


def main():
    os.chdir(HERE)
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"The control panel already seems to be running. Opening http://{HOST}:{PORT}")
        webbrowser.open(f"http://{HOST}:{PORT}")
        return
    url = f"http://{HOST}:{PORT}"
    print(f"Control panel running at {url}\nKeep this window open. Close it (or press Ctrl+C) to stop.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()

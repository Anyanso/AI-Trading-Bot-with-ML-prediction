"""Phone (and optional email) alerts through ntfy.sh, a free notification service.

Setup (once): install the "ntfy" app on your phone (App Store / Google Play), tap +,
and subscribe to the topic shown in the control panel's Alerts section.
No account or password is needed. Anyone who knows the topic name can read the alerts,
so the name is long and random, and alerts never include your account number.
"""
import json, os, secrets, urllib.request
from datetime import datetime

SETTINGS_FILE = "settings.json"
LOG = os.path.join("output", "alerts_log.csv")


def _settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ensure_topic():
    """Create a private random topic name the first time; return it."""
    s = _settings()
    if not s.get("ntfy_topic"):
        s["ntfy_topic"] = "tradebot-" + secrets.token_hex(8)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1)
    return s["ntfy_topic"]


def send(title, message, priority="default", tags="", force=False):
    """Send an alert. Never raises: alerts must not break trading. Returns True if delivered."""
    s = _settings()
    if not (s.get("alerts_on") or force):
        return False
    topic = s.get("ntfy_topic") or ensure_topic()
    headers = {"Title": title.encode("utf-8").decode("latin-1", "replace"), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if s.get("alert_email"):
        headers["Email"] = s["alert_email"]
    ok, err = False, ""
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = 200 <= r.status < 300
    except Exception as e:
        err = str(e)
    try:
        os.makedirs("output", exist_ok=True)
        new = not os.path.exists(LOG)
        with open(LOG, "a", encoding="utf-8") as f:
            if new:
                f.write("time,title,delivered,error\n")
            f.write(f"{datetime.now().isoformat(timespec='seconds')},\"{title}\",{ok},\"{err}\"\n")
    except Exception:
        pass
    return ok

"""Match the bot's market names to whatever your broker calls them.

The bot thinks in standard names (US500, XAUUSD, TLT…). Brokers use their own: US500 might be
"SPX500", "US500.cash", "US500m" or "USA500". When execute.py connects, it looks each market up
in the broker's symbol list and remembers the match in output/symbol_map.json.

If a market isn't found automatically, add it to settings.json by hand, e.g.
    "symbol_map": {"US500": "SPX500.r", "DE40": "GER40"}
"""
import json, os, re

MAP_FILE = os.path.join("output", "symbol_map.json")

# Common alternative names, most likely first. Suffixes like ".cash", "m", ".r", "_i" are tried automatically.
ALIASES = {
    "US500":   ["US500", "SPX500", "US500Cash", "USA500", "SP500", "US SPX 500", "S&P500", "USA500IDXUSD"],
    "USTEC":   ["USTEC", "NAS100", "US100", "NDX100", "USTECH", "NASDAQ100", "USA100", "US Tech 100"],
    "US30":    ["US30", "DJ30", "WS30", "DOW30", "USA30", "US Wall St 30", "DJI30"],
    "US2000":  ["US2000", "RUSSELL2000", "US2000Cash", "USSC2000", "US Small Cap 2000"],
    "DE40":    ["DE40", "GER40", "DAX40", "GER30", "DE30", "Germany 40"],
    "UK100":   ["UK100", "FTSE100", "GB100", "UK 100"],
    "FRA40":   ["FRA40", "FR40", "CAC40", "France 40"],
    "EUSTX50": ["EUSTX50", "STOXX50", "EU50", "ESTX50", "EUSTX", "Europe 50"],
    "JPN225":  ["JPN225", "JP225", "NIKKEI225", "N225", "JAP225", "Japan 225"],
    "AUS200":  ["AUS200", "AU200", "ASX200", "Australia 200"],
    "HK50":    ["HK50", "HK33", "HSI50", "HKG33", "Hong Kong 50"],
    "CHINA50": ["CHINA50", "CN50", "CHINAA50", "China A50"],
    "XAUUSD":  ["XAUUSD", "GOLD", "Gold"],
    "XAGUSD":  ["XAGUSD", "SILVER", "Silver"],
    "XPTUSD":  ["XPTUSD", "PLATINUM"],
    "XPDUSD":  ["XPDUSD", "PALLADIUM"],
    "USO":     ["USO", "USO.US", "USOIL", "WTI", "Oil - US Crude", "XTIUSD"],
    "UNG":     ["UNG", "UNG.US", "NGAS", "NATGAS", "XNGUSD", "Natural Gas"],
    "CPER":    ["CPER", "CPER.US", "COPPER", "XCUUSD", "Copper"],
    "CORN":    ["CORN", "CORN.US", "Corn"],
    "WEAT":    ["WEAT", "WEAT.US", "WHEAT", "Wheat"],
    "DBC":     ["DBC", "DBC.US"],
    "DBA":     ["DBA", "DBA.US"],
    # Bond ETFs; if the broker has none, its US Treasury bond/note CFDs are used instead.
    "TLT":     ["TLT", "TLT.US", "TLT.O", "USTBOND", "US30YT", "UST30Y", "TBOND", "US T-Bond"],
    "IEF":     ["IEF", "IEF.US", "IEF.O", "USTNOTE", "US10YT", "UST10Y", "TNOTE", "US10Y", "US T-Note"],
    "TIP":     ["TIP", "TIP.US"],
    "LQD":     ["LQD", "LQD.US"],
}
# Broker suffixes: ".cash", "_i", "-r" etc. need a separator; only "cash" and "m" may be glued on
# ("US500cash", "SPX500m"). Without this rule "SPXC" (a company's shares) looked like "SPX".
SUFFIX = re.compile(r"^(?P<base>.+?)([._-](cash|spot|c|m|r|i|pro|ecn|raw|std|a|b|us|o|n|f)|cash|m)?$", re.I)
# For indices, currencies and metals, never accept a symbol filed under shares or ETFs.
NOT_FOR = {"Indices", "Forex", "Metals"}
WRONG_KIND = ("stock", "share", "equit", "etf")


def _norm(s):
    return re.sub(r"[^a-z0-9&]", "", s.lower())


def resolve(mt5, standard, overrides=None):
    """{standard name: broker name} for every market the broker has (overrides from settings first).
    When a broker lists several versions (e.g. "US500" quote-only and "US500.cash" tradable),
    the tradable one wins."""
    overrides = overrides or {}
    syms = list(mt5.symbols_get() or [])
    names = [s.name for s in syms]
    kind = {s.name: (str(getattr(s, "path", "")) + " " + str(getattr(s, "description", ""))).lower() for s in syms}
    exact = set(names)
    try:
        from portfolio import CLASS_OF
    except Exception:
        CLASS_OF = {}
    by_norm = {}
    for n in names:   # "US500.cash" -> "us500", "SPX500m" -> "spx500"
        m = SUFFIX.match(n)
        for key in dict.fromkeys([_norm(n), _norm(m.group("base")) if m else _norm(n)]):
            by_norm.setdefault(key, []).append(n)
    full = getattr(mt5, "SYMBOL_TRADE_MODE_FULL", 4)

    def ok(n):
        try:
            info = mt5.symbol_info(n)
            return info is not None and info.trade_mode == full
        except Exception:
            return False

    out = {}
    for std in standard:
        if std in overrides and overrides[std] in exact:
            out[std] = overrides[std]; continue
        hits = []
        for cand in [std] + ALIASES.get(std, []):
            for n in ([cand] if cand in exact else []) + by_norm.get(_norm(cand), []):
                if CLASS_OF.get(std) in NOT_FOR and any(w in kind.get(n, "") for w in WRONG_KIND):
                    continue   # e.g. "SPXC" filed under Stocks is not the S&P 500
                if n not in hits:
                    hits.append(n)
        if hits:
            out[std] = next((n for n in hits if ok(n)), hits[0])
    return out


def save_map(mapping):
    """Remember broker -> standard names for the dashboard and signal report."""
    os.makedirs(os.path.dirname(MAP_FILE), exist_ok=True)
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump({b: s for s, b in mapping.items()}, f, indent=1)


_CACHE = {"mtime": None, "map": {}}


def standard(sym):
    """Broker name -> the bot's standard name (unchanged if unknown)."""
    try:
        mt = os.path.getmtime(MAP_FILE)
        if mt != _CACHE["mtime"]:
            with open(MAP_FILE, encoding="utf-8") as f:
                _CACHE.update(mtime=mt, map=json.load(f))
    except Exception:
        pass
    return _CACHE["map"].get(sym, sym)

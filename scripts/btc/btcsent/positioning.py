"""OKX retail-positioning series (keyless public API; Binance futures return HTTP 451 from this box).

  ls_account_ratio : long/short ACCOUNT ratio of BTC-USDT-SWAP (accounts net long / accounts net short),
                     periods 5m / 1H / 4H / 1D. OKX serves at most 1440 bars per period (1D: since 2024-01-01).
  funding_realized : realized 8h funding rate of BTC-USDT-SWAP (OKX history endpoint serves ~3 months).

Stored in data/positioning2.db table `positioning` (moved out of sentiment.db 2026-10-24; open with pos_con()).
Research (experiments P1/P1b, 2026-10-09): the 4H L/S account ratio is LOWER at swing highs and HIGHER at swing
lows even after removing price (BH-significant), i.e. it marks swings COINCIDENTALLY; it has no next-bar lead and
no out-of-sample contrarian-zone edge -> shown as context, never as a signal, no zones.
"""
import json, time, urllib.request
from .common import log, CONFIG

API = "https://www.okx.com/api/v5"
SCHEMA = """CREATE TABLE IF NOT EXISTS positioning (
    source TEXT NOT NULL, inst TEXT NOT NULL, metric TEXT NOT NULL, period TEXT NOT NULL,
    ts INTEGER NOT NULL,          -- unix seconds UTC as reported by OKX
    value REAL, fetched_utc INTEGER,
    PRIMARY KEY (source, inst, metric, period, ts));"""


def pos_con():
    """Connection to data/positioning2.db (holds the `positioning` table since 2026-10-24)."""
    import os, sqlite3
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "positioning2.db")
    con = sqlite3.connect(p, timeout=120); con.execute("PRAGMA busy_timeout=120000")
    con.executescript(SCHEMA)
    return con


def cfg():
    return CONFIG.get("positioning") or {"enabled": True, "inst": "BTC-USDT-SWAP", "periods": ["5m", "1H", "4H", "1D"]}


def _get(path, timeout=20):
    req = urllib.request.Request(API + path, headers={"User-Agent": "Mozilla/5.0 btc-sentiment"})
    d = json.load(urllib.request.urlopen(req, timeout=timeout))
    if str(d.get("code")) != "0":
        raise RuntimeError(f"OKX {path}: code {d.get('code')} {d.get('msg')}")
    return d.get("data") or []


def _upsert(con, rows):
    con.executemany("INSERT INTO positioning(source,inst,metric,period,ts,value,fetched_utc) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(source,inst,metric,period,ts) DO UPDATE SET value=excluded.value, fetched_utc=excluded.fetched_utc", rows)
    con.commit()


def update(con, backfill=False, max_pages=None):
    """Fetch the newest page (every cycle) or page back to the API limit (backfill=True). Returns rows written."""
    c = cfg()
    if not c.get("enabled", True):
        return 0
    con.executescript(SCHEMA)
    inst, now, n = c.get("inst", "BTC-USDT-SWAP"), int(time.time()), 0
    pages = max_pages or (60 if backfill else 1)
    for per in c.get("periods", ["5m", "1H", "4H", "1D"]):
        end = ""
        for _ in range(pages):
            d = _get(f"/rubik/stat/contracts/long-short-account-ratio-contract?instId={inst}&period={per}&limit=100" + (f"&end={end}" if end else ""))
            if not d:
                break
            _upsert(con, [("okx", inst, "ls_account_ratio", per, int(t) // 1000, float(v), now) for t, v in d])
            n += len(d); end = d[-1][0]
            if len(d) < 100:
                break
            time.sleep(0.25)
    after = ""
    for _ in range(pages):
        d = _get(f"/public/funding-rate-history?instId={inst}&limit=100" + (f"&after={after}" if after else ""))
        if not d:
            break
        _upsert(con, [("okx", inst, "funding_realized", "8h", int(r["fundingTime"]) // 1000,
                       float(r.get("realizedRate") or r.get("fundingRate")), now) for r in d])
        n += len(d); after = d[-1]["fundingTime"]
        if len(d) < 100:
            break
        time.sleep(0.25)
    return n


def load(con, metric, period, start=None, end=None, inst=None):
    import pandas as pd
    con.executescript(SCHEMA)
    q = "SELECT ts, value FROM positioning WHERE source='okx' AND inst=? AND metric=? AND period=?"
    p = [inst or cfg().get("inst", "BTC-USDT-SWAP"), metric, period]
    if start is not None:
        q += " AND ts >= ?"; p.append(int(start))
    if end is not None:
        q += " AND ts < ?"; p.append(int(end))
    df = pd.read_sql_query(q + " ORDER BY ts", con, params=p)
    return df.set_index("ts")["value"]

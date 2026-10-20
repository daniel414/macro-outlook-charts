#!/usr/bin/env python3
"""Positioning data fetcher (free, keyless) -> data/positioning2.db, table series(src,metric,tf,ts,value).

  python posdata.py              # incremental (latest pages), run every 15 min from run_collect.sh
  python posdata.py --backfill   # one-off: page OKX/Gate history fully, Bitfinex 1m since --bfx-since

Sources: OKX rubik (all-account L/S, top-trader account/position L/S, OI), Gate.io contract_stats (BTC_USDT; account
L/S, top-trader account/size L/S, OI usd, funding; max 180d), Bitfinex stats1 pos.size long/short (1m -> stored as
hourly last value, tf=1H), Binance UM BTCUSDT 5m via www.binance.com/futures/data (live, ~30 days of history;
fapi not used) + data.binance.vision daily dumps (backup / long history, 1-2 day lag), both into table binance
(INSERT OR IGNORE + fill-NULL only: append-only, never overwrite). Bybit (403) blocked.
Hyperliquid collection stopped 2026-10-20 (Daniel); its old rows (src=hyperliquid) and data/hl_top_addrs.json are kept.
ts = bucket start, unix seconds UTC.
"""
import argparse, json, sqlite3, sys, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "positioning2.db"
UA = {"User-Agent": "curl/8.5", "Content-Type": "application/json"}
OKX = "https://www.okx.com/api/v5/rubik/stat/contracts/"
INST = "BTC-USDT-SWAP"


def get(url, data=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2 + 3 * i)


def db():
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS series(src TEXT, metric TEXT, tf TEXT, ts INTEGER, value REAL, PRIMARY KEY(src,metric,tf,ts))")
    return con


def put(con, rows):
    con.executemany("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?)", rows); con.commit()


def okx(con, backfill):
    eps = {"ls_account": "long-short-account-ratio-contract", "top_ls_account": "long-short-account-ratio-contract-top-trader",
           "top_ls_position": "long-short-position-ratio-contract-top-trader", "oi": "open-interest-history"}
    n = 0
    for m, ep in eps.items():
        for tf in ("1H", "4H", "1D"):
            end = None
            for _ in range(40 if backfill else 1):
                url = f"{OKX}{ep}?instId={INST}&period={tf}&limit=100" + (f"&end={end}" if end else "")
                d = get(url)["data"]
                if not d:
                    break
                if m == "oi":   # [ts, oi contracts, oiCcy (BTC), oiUsd]
                    rows = [("okx", "oi_usd", tf, int(r[0]) // 1000, float(r[3])) for r in d] + \
                           [("okx", "oi_btc", tf, int(r[0]) // 1000, float(r[2])) for r in d]
                else:
                    rows = [("okx", m, tf, int(r[0]) // 1000, float(r[1])) for r in d]
                put(con, rows); n += len(rows); end = d[-1][0]
                if len(d) < 100:
                    break
                time.sleep(0.25)
    return n


def gate(con, backfill):
    n = 0; now = int(time.time())
    for iv, tf, step in (("1h", "1H", 3600), ("4h", "4H", 14400), ("1d", "1D", 86400)):
        frm = now - 179 * 86400 if backfill else now - 12 * step
        d = []
        while True:   # max 2000 rows per call -> page forward
            p = get(f"https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=BTC_USDT&interval={iv}&limit=2000&from={frm}")
            d += p
            if len(p) < 2000:
                break
            frm = int(p[-1]["time"]) + step
        rows = []
        for r in d:
            t = int(r["time"])
            lu, su = r.get("long_users") or 0, r.get("short_users") or 0
            vals = {"ls_account": r.get("lsr_account"), "top_ls_account": r.get("top_lsr_account"), "top_ls_position": r.get("top_lsr_size"),
                    "oi_usd": r.get("open_interest_usd"), "funding": float(r["last_funding_rate"]) if r.get("last_funding_rate") not in (None, "") else None,
                    "long_users": lu, "short_users": su}
            rows += [("gate", k, tf, t, float(v)) for k, v in vals.items() if v is not None]
        put(con, rows); n += len(rows)
    return n


def bitfinex(con, since_ms=None):
    """1m pos.size long/short -> hourly last value (tf=1H)."""
    n = 0
    for side in ("long", "short"):
        if since_ms is None:
            last = con.execute("SELECT max(ts) FROM series WHERE src='bitfinex' AND metric=? AND tf='1H'", (side,)).fetchone()[0]
            start = max(last * 1000 if last else 0, int(time.time() * 1000) - 3 * 86400000)   # backfill fills older gaps
        else:
            start = since_ms
        while True:
            d = get(f"https://api-pub.bitfinex.com/v2/stats1/pos.size:1m:tBTCUSD:{side}/hist?limit=10000&sort=1&start={start}")
            if not d:
                break
            hourly = {}
            for t, v in d:
                hourly[(t // 1000) // 3600 * 3600] = float(v)   # ascending -> keeps last value in the hour
            put(con, [("bitfinex", side, "1H", k, v) for k, v in hourly.items()]); n += len(hourly)
            if len(d) < 10000:
                break
            start = d[-1][0] + 1
            time.sleep(1.2)   # public rate limit
    return n


BG = "https://api.bitget.com/api/v2/mix/market/"


def bitget(con):
    """Bitget USDT-M BTCUSDT L/S ratios. Endpoint keeps only the latest 30 points per period (no paging), so history
    accumulates from our own runs. long-short = all accounts; account-long-short / position-long-short = Bitget's
    account / position ratio endpoints (docs: top-trader style). Stored src=bitget, tf=1H/4H."""
    eps = {"ls_account": ("long-short", "longShortRatio"), "top_ls_account": ("account-long-short", "longShortAccountRatio"),
           "top_ls_position": ("position-long-short", "longShortPositionRatio")}
    n = 0
    for m, (ep, k) in eps.items():
        for tf, per in (("1H", "1h"), ("4H", "4h")):
            d = get(f"{BG}{ep}?symbol=BTCUSDT&productType=usdt-futures&period={per}")["data"] or []
            rows = [("bitget", m, tf, int(r["ts"]) // 1000, float(r[k])) for r in d]
            put(con, rows); n += len(rows); time.sleep(0.2)
    return n


def deribit(con):
    """Deribit has no long/short-ratio API. PROXY only: BTC-PERPETUAL OI (USD) and 8h funding snapshot."""
    r = get("https://www.deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL")["result"]
    t = int(time.time()) // 900 * 900
    put(con, [("deribit", "oi_usd", "snap", t, float(r["open_interest"])), ("deribit", "funding_8h", "snap", t, float(r["funding_8h"]))])
    return 2


def kraken_daily(con):
    """Daily BTC/USD close (720 days) for the ahr999 index; FRED CBBTCUSD in data/macro.db covers older history."""
    d = get("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440")["result"]
    rows = [("kraken", "close", "1D", int(r[0]), float(r[4])) for r in d["XXBTZUSD"]]
    put(con, rows); return len(rows)


BNB = "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{}.zip"
BCOLS = ["sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio",
         "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]


BCOL_NAMES = ["oi_btc", "oi_usd", "top_account", "top_position", "global_account", "taker_ratio"]


def bn_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS binance(ts INTEGER PRIMARY KEY, oi_btc REAL, oi_usd REAL, top_account REAL,"
                " top_position REAL, global_account REAL, taker_ratio REAL)")
    if "src" not in [r[1] for r in con.execute("PRAGMA table_info(binance)")]:
        con.execute("ALTER TABLE binance ADD COLUMN src TEXT")   # NULL = old vision rows; 'www' / 'vision'


def bn_put(con, rows, src):
    """Append-only: new ts inserted; existing ts only gets its NULL columns filled (no value is overwritten)."""
    q = "INSERT OR IGNORE INTO binance VALUES (?,?,?,?,?,?,?,?)"
    before = con.total_changes
    con.executemany(q, [(*r, src) for r in rows])
    for c_i, c in enumerate(BCOL_NAMES):
        con.executemany(f"UPDATE binance SET {c}=? WHERE ts=? AND {c} IS NULL", [(r[1 + c_i], r[0]) for r in rows if r[1 + c_i] is not None])
    con.commit(); return con.total_changes - before


BW = "https://www.binance.com/futures/data/"
BW_EPS = {"globalLongShortAccountRatio": [("global_account", "longShortRatio")], "topLongShortAccountRatio": [("top_account", "longShortRatio")],
          "topLongShortPositionRatio": [("top_position", "longShortRatio")], "takerlongshortRatio": [("taker_ratio", "buySellRatio")],
          "openInterestHist": [("oi_btc", "sumOpenInterest"), ("oi_usd", "sumOpenInterestValue")]}


def binance_www(con, backfill=False):
    """Binance UM BTCUSDT 5m from public www.binance.com/futures/data endpoints (Binance keeps ~30 days).
    Incremental: latest 500 rows (~41h) per endpoint; --backfill pages startTime/endTime back to the oldest available.
    Raises on HTTP errors (451/403 included) so main() falls back to data.binance.vision and logs it."""
    bn_table(con); rec = {}
    for ep, cols in BW_EPS.items():
        end = None
        for pg in range(40 if backfill else 1):
            try:
                d = get(f"{BW}{ep}?symbol=BTCUSDT&period=5m&limit=500" + (f"&endTime={end}" if end else ""), tries=2)
            except Exception:
                if pg: break   # paged past Binance's ~30-day window (400 endTime invalid): keep what we have
                raise
            if not isinstance(d, list):
                raise RuntimeError(f"{ep}: unexpected {str(d)[:120]}")
            for r in d:
                x = rec.setdefault(int(r["timestamp"]) // 1000, {})
                for c, k in cols:
                    if r.get(k) not in (None, ""): x[c] = float(r[k])
            if len(d) < 500:
                break
            end = min(int(r["timestamp"]) for r in d) - 1; time.sleep(0.3)
    rows = [(t, *[v.get(c) for c in BCOL_NAMES]) for t, v in sorted(rec.items())]
    return bn_put(con, rows, "www")


def binance(con, since="2024-01-01"):
    """Binance UM BTCUSDT 5m metrics from data.binance.vision (official public daily dumps, 1-2 day lag).
    Table binance(ts UTC sec, oi_btc, oi_usd, top_account, top_position, global_account, taker_ratio).
    Fetches every day after the latest stored one (or from `since`); missing (404) recent days are skipped quietly."""
    import io, csv, zipfile, datetime as dt, urllib.error
    bn_table(con)
    mx = con.execute("SELECT MAX(ts) FROM binance WHERE src IS NULL OR src='vision'").fetchone()[0]
    d = dt.date.fromisoformat(since) if mx is None else dt.datetime.fromtimestamp(mx, dt.timezone.utc).date()
    today = dt.datetime.now(dt.timezone.utc).date(); n = 0
    while d < today:
        try:
            b = urllib.request.urlopen(urllib.request.Request(BNB.format(d), headers={"User-Agent": "curl/8.5"}), timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                d += dt.timedelta(days=1); continue
            raise
        z = zipfile.ZipFile(io.BytesIO(b)); rows = []
        for r in csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))):
            try:
                ts = int(dt.datetime.fromisoformat(r["create_time"]).replace(tzinfo=dt.timezone.utc).timestamp())
                rows.append((ts, *[float(r[c]) if r.get(c) not in (None, "") else None for c in BCOLS]))
            except (KeyError, ValueError):
                continue
        n += bn_put(con, rows, "vision")
        d += dt.timedelta(days=1); time.sleep(0.3)
    return n


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--bfx-since", default="2024-01-01"); ap.add_argument("--only")
    a = ap.parse_args(); con = db(); ok = True
    import datetime as dt
    jobs = {"okx": lambda: okx(con, a.backfill), "gate": lambda: gate(con, a.backfill), "bitget": lambda: bitget(con), "deribit": lambda: deribit(con), "kraken": lambda: kraken_daily(con), "binance_www": lambda: binance_www(con, a.backfill), "binance": lambda: binance(con),
            "bitfinex": lambda: bitfinex(con, int(dt.datetime.fromisoformat(a.bfx_since).replace(tzinfo=dt.timezone.utc).timestamp() * 1000) if a.backfill else None)}
    for k, f in jobs.items():
        if a.only and k not in a.only.split(","):
            continue
        try:
            print(f"[{time.strftime('%F %T')}] posdata {k}: {f()} rows", flush=True)
        except Exception as e:
            ok = False; print(f"[{time.strftime('%F %T')}] posdata {k} FAILED: {e!r}", flush=True)
            if k == "binance_www":
                print(f"[{time.strftime('%F %T')}] posdata binance_www FALLBACK: using data.binance.vision daily dumps only (1-2 day lag)", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

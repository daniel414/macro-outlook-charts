"""BTC price klines. Binance public klines (BTCUSDT) first; fallbacks: Binance data mirror,
Kraken OHLC (XBTUSD), Coinbase candles (BTC-USD). Kraken/Coinbase only support some
granularities, so we fetch the closest finer one and resample."""
import time
import pandas as pd
from .common import http_session, log

INTERVALS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
             "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800}
WEEK_OFFSET = 4 * 86400   # epoch (Thu) -> Monday 00:00 UTC, matches Binance weekly candles


def bucket_start(ts, step):
    """Floor unix seconds to the start of an interval bucket aligned like Binance (UTC)."""
    off = WEEK_OFFSET if step == 604800 else 0
    return ((ts - off) // step) * step + off


def _binance(s, tf, start, end, host):
    out, cur = [], start * 1000
    while cur < end * 1000:
        r = s.get(f"{host}/api/v3/klines", params={"symbol": "BTCUSDT", "interval": tf, "startTime": cur,
                                                     "endTime": end * 1000, "limit": 1000}, timeout=30)
        r.raise_for_status()
        k = r.json()
        if not k:
            break
        out += [(x[0] // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in k]
        cur = k[-1][0] + 1
        if len(k) < 1000:
            break
        time.sleep(0.2)
    return out


def _resample(rows, step):
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"])
    df["b"] = bucket_start(df["t"], step)
    g = df.sort_values("t").groupby("b")
    return list(zip(g.size().index, g["o"].first(), g["h"].max(), g["l"].min(), g["c"].last(), g["v"].sum()))


def _kraken(s, tf, start, end):
    step = INTERVALS[tf]
    sup = [m for m in (1, 5, 15, 30, 60, 240, 1440, 10080) if step % (m * 60) == 0]
    m = max(sup)
    r = s.get("https://api.kraken.com/0/public/OHLC", params={"pair": "XBTUSD", "interval": m, "since": start}, timeout=30)
    d = r.json()["result"]
    rows = next(v for k, v in d.items() if k != "last")
    rows = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[6])) for x in rows]
    return _resample([x for x in rows if start <= x[0] <= end], step)   # Kraken returns max 720 candles


def _coinbase(s, tf, start, end):
    step = INTERVALS[tf]
    g = max(x for x in (60, 300, 900, 3600, 21600, 86400) if step % x == 0)
    rows, cur = [], start
    while cur < end:
        stop = min(end, cur + g * 300)
        r = s.get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                  params={"granularity": g, "start": cur, "end": stop}, timeout=30)
        r.raise_for_status()
        rows += [(int(x[0]), float(x[3]), float(x[2]), float(x[1]), float(x[4]), float(x[5])) for x in r.json()]
        cur = stop
        time.sleep(0.35)
    return _resample(rows, step)


def get_klines(tf, start, end):
    """Return DataFrame indexed by UTC bucket start (unix s) with o,h,l,c,v and the exchange used."""
    s = http_session()
    for name, fn in [("Binance", lambda: _binance(s, tf, start, end, "https://api.binance.com")),
                     ("Binance(data-api)", lambda: _binance(s, tf, start, end, "https://data-api.binance.vision")),
                     ("Kraken", lambda: _kraken(s, tf, start, end)),
                     ("Coinbase", lambda: _coinbase(s, tf, start, end))]:
        try:
            rows = fn()
            if rows:
                df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"]).drop_duplicates("t").set_index("t").sort_index()
                return df, name
        except Exception as e:
            log.warning("price source %s failed: %s", name, e)
    raise RuntimeError("all price sources failed")

"""Objective BTC structure line for /btc (no patterns, no direction, no entries/stops).
Price vs this year's / month's / week's open (UTC 00:00 daily bars, week starts Monday) and vs daily MA120
(mean of the last 120 completed daily closes); label 多方結構/空方結構 = latest completed daily close above/below MA120. Free public data: OKX BTC-USDT 1Dutc, fallback Coinbase BTC-USD.
Usage: btc_structure.py [--price 81912] [--json]  -> prints the section (title line + item line), or nothing on failure.
Standalone, stdlib only; every failure -> empty output, exit 0."""
import json, sys, urllib.request
from datetime import datetime, timedelta, timezone
TITLE = "【技術結構（客觀數據，非投資建議）】"
UA = {"User-Agent": "btc-structure/1.0"}
def _get(u, t=10):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t))
def _ms(d): return int(d.timestamp() * 1000)
def okx():
    B = "https://www.okx.com/api/v5/market/"
    def bar_at(day):   # the 1Dutc bar that opens exactly at `day` (UTC 00:00)
        d = _get(f"{B}history-candles?instId=BTC-USDT&bar=1Dutc&limit=1&after={_ms(day + timedelta(days=1))}")["data"]
        if not d or int(d[0][0]) != _ms(day): raise ValueError("okx bar missing " + day.date().isoformat())
        return float(d[0][1])
    rows = _get(f"{B}candles?instId=BTC-USDT&bar=1Dutc&limit=125")["data"]   # newest first
    closes = [float(r[4]) for r in rows if r[8] == "1"][:120]
    if len(closes) < 120: raise ValueError("okx <120 closed bars")
    px = float(_get(f"{B}ticker?instId=BTC-USDT")["data"][0]["last"])
    return dict(source="OKX BTC-USDT", bar_at=bar_at, ma120=sum(closes) / 120, last=px, last_close=closes[0])
def coinbase():
    B = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400"
    def rng(a, b): return _get(f"{B}&start={a.strftime('%Y-%m-%dT%H:%M:%SZ')}&end={b.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    def bar_at(day):
        for r in rng(day, day + timedelta(hours=1)):
            if int(r[0]) == int(day.timestamp()): return float(r[3])
        raise ValueError("coinbase bar missing")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = sorted(rng(today - timedelta(days=130), today - timedelta(seconds=1)), key=lambda r: r[0])
    closes = [float(r[4]) for r in rows if int(r[0]) < int(today.timestamp())][-120:]
    if len(closes) < 120: raise ValueError("coinbase <120 closed bars")
    px = float(_get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")["price"])
    return dict(source="Coinbase BTC-USD", bar_at=bar_at, ma120=sum(closes) / 120, last=px, last_close=closes[-1])
def snapshot(price=None):
    now = datetime.now(timezone.utc); today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = dict(year=today.replace(month=1, day=1), month=today.replace(day=1), week=today - timedelta(days=today.weekday()))
    err = []
    for f in (okx, coinbase):
        try:
            s = f(); px = float(price) if price else s["last"]
            opens = {k: s["bar_at"](d) for k, d in starts.items()}
            return dict(source=s["source"], asof=now.isoformat(timespec="seconds"), price=px, ma120=s["ma120"], last_close=s["last_close"],
                        **{f"{k}_open": v for k, v in opens.items()}, **{f"{k}_start": d.date().isoformat() for k, d in starts.items()})
        except Exception as e: err.append(f"{f.__name__}: {e}")
    raise RuntimeError("; ".join(err))
def pos(px, ref): return "上方" if px > ref else "下方" if px < ref else "持平"
def ma_struct(s):   # by the latest completed daily close vs MA120 (objective label only)
    c, m = s["last_close"], s["ma120"]
    return "，多方結構" if c > m else "，空方結構" if c < m else ""
def section(price=None):
    s = snapshot(price); px = s["price"]
    line = (f"・BTC {px:,.0f}｜在今年開盤價 {s['year_open']:,.0f} {pos(px, s['year_open'])}、"
            f"本月開盤價 {s['month_open']:,.0f} {pos(px, s['month_open'])}、"
            f"本週開盤價 {s['week_open']:,.0f} {pos(px, s['week_open'])}｜"
            f"在日線 MA120（{s['ma120']:,.0f}）{pos(px, s['ma120'])}{ma_struct(s)}")
    return [TITLE, line], s
if __name__ == "__main__":
    try:
        price = sys.argv[sys.argv.index("--price") + 1].replace(",", "") if "--price" in sys.argv else None
        lines, s = section(price)
        print(json.dumps(s, ensure_ascii=False) if "--json" in sys.argv else "\n".join(lines))
    except Exception as e:
        print("structure skipped:", e, file=sys.stderr)

#!/usr/bin/env python3
"""BTC 中期／長期口語模型（每日檢查，模板組句，不用 LLM）。

只當腳本執行，禁止被 import。publish_btc.py 以 subprocess 呼叫。
開關在 publish_btc.py：環境變數 MIDLONG_VOICE（預設 0／關閉）。

資料：現貨 K 線（幣安 → OKX → Bitstamp），鏈上既有輸出，總經既有日報／本地檔。
總經若缺或過舊，改寫不含數字的定性句，不編造。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __name__ != "__main__":
    raise RuntimeError("midlong_model.py 只能當腳本執行，不可 import")

TZ = timezone(timedelta(hours=8))
UTC = timezone.utc

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "scripts" else HERE
DEFAULT_OUT = ROOT / "out" / "midlong"

BINANCE_SPOT = "https://api.binance.com/api/v3"
OKX_CANDLES = "https://www.okx.com/api/v5/market/history-candles"
BITSTAMP_OHLC = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
UA = {"User-Agent": "btc-midlong-model/1.0"}

# 總經數字的新鮮度：日頻（美元、殖利率）超過這個日曆天數就改定性句
MACRO_DAILY_MAX_AGE = 5
# 週頻流動性
MACRO_LIQ_MAX_AGE = 14

FORBIDDEN_SOURCES = [
    "鋒哥", "街哥", "CheckOnChain", "Coin Metrics", "CoinMetrics", "Glassnode",
    "CryptoQuant", "FedWatch", "CME", "Binance", "幣安", "OKX", "Bitstamp",
    "FRED", "Yahoo", "Kalshi", "Polymarket", "rektcapital", "Murphychen",
    "KillaXBT", "traderfengge", "Cowen", "cowen", "Benjamin", "@",
    "api.binance", "http://", "https://",
]
PERIOD_LABELS = ["數天", "數週", "數周"]
MA_WRONG = ["SMA120", "120 均線", "120日均線", "120日均"]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def now_tp() -> datetime:
    return datetime.now(TZ)


def n0(x: float | int | None) -> str:
    if x is None:
        return ""
    return f"{int(round(float(x))):,}"


def n2(x: float | None) -> str:
    if x is None:
        return ""
    return f"{float(x):.2f}"


def taipei_md(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}"


def closed_bars(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("closed")]


def sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2 / (n + 1)
    e = statistics.mean(vals[:n])
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def macd(vals: list[float], fast=12, slow=26, sig=9):
    ef, es = ema_series(vals, fast), ema_series(vals, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(ef, es)]
    start = next((i for i, v in enumerate(line) if v is not None), None)
    signal: list[float | None] = [None] * len(line)
    hist: list[float | None] = [None] * len(line)
    if start is None:
        return line, signal, hist
    sigs = ema_series([line[i] for i in range(start, len(line))], sig)
    for j, s in enumerate(sigs):
        i = start + j
        signal[i] = s
        if s is not None and line[i] is not None:
            hist[i] = line[i] - s
    return line, signal, hist


def last_cross(line, signal) -> tuple[str | None, int | None]:
    ev = None
    for i in range(1, len(line)):
        a0, b0, a1, b1 = line[i - 1], signal[i - 1], line[i], signal[i]
        if None in (a0, b0, a1, b1):
            continue
        if a0 <= b0 and a1 > b1:
            ev = ("金叉", i)
        elif a0 >= b0 and a1 < b1:
            ev = ("死叉", i)
    return ev if ev else (None, None)


def _http_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.loads(resp.read().decode())


def _bar(o_ms, o, h, l, c, vol, c_ms, now_ms) -> dict:
    return {
        "open_time": int(o_ms),
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(vol),
        "close_time": int(c_ms),
        "closed": int(c_ms) < now_ms,
    }


def _fetch_binance(interval: str, limit: int, now_ms: int) -> list[dict]:
    url = f"{BINANCE_SPOT}/klines?symbol=BTCUSDT&interval={interval}&limit={limit}"
    data = _http_json(url)
    return [_bar(k[0], k[1], k[2], k[3], k[4], k[5], k[6], now_ms) for k in data]


def _fetch_okx(interval: str, limit: int, now_ms: int) -> list[dict]:
    bar = {"1d": "1D", "1w": "1W", "1M": "1M"}[interval]
    ms = {"1D": 86400000, "1W": 604800000, "1M": 32 * 86400000}[bar]
    rows: list[dict] = []
    after = None
    remain = max(limit, 1)
    while remain > 0:
        batch = min(remain, 100)
        url = f"{OKX_CANDLES}?instId=BTC-USDT&bar={bar}&limit={batch}"
        if after:
            url += f"&after={after}"
        payload = _http_json(url)
        data = payload.get("data") or []
        if not data:
            break
        chunk = []
        for k in data:
            ts = int(k[0])
            confirm = k[8] if len(k) > 8 else "1"
            b = _bar(ts, k[1], k[2], k[3], k[4], k[5], ts + ms - 1, now_ms)
            if bar == "1M":
                b["closed"] = confirm == "1"
            chunk.append(b)
        rows = chunk[::-1] + rows
        after = int(data[-1][0])
        remain -= len(data)
        if len(data) < batch:
            break
    # 去重
    seen, uniq = set(), []
    for r in rows:
        if r["open_time"] in seen:
            continue
        seen.add(r["open_time"])
        uniq.append(r)
    return uniq[-limit:]


def _monthly_from_daily(d1: list[dict], now_ms: int) -> list[dict]:
    buckets: dict[tuple[int, int], list[dict]] = {}
    for r in d1:
        dt = datetime.fromtimestamp(r["open_time"] / 1000, TZ)
        buckets.setdefault((dt.year, dt.month), []).append(r)
    out = []
    for (y, m), rows in sorted(buckets.items()):
        o = rows[0]["open"]
        h = max(x["high"] for x in rows)
        lo = min(x["low"] for x in rows)
        c = rows[-1]["close"]
        o_ms = rows[0]["open_time"]
        if m == 12:
            ny, nm = y + 1, 1
        else:
            ny, nm = y, m + 1
        close_ms = int(datetime(ny, nm, 1, 0, 0, tzinfo=TZ).timestamp() * 1000) - 1
        out.append(_bar(o_ms, o, h, lo, c, 0, close_ms, now_ms))
    return out


def fetch_market(limit_1d=1000, limit_1w=500, limit_1m=200) -> dict:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    errors = []
    try:
        d1 = _fetch_binance("1d", limit_1d, now_ms)
        w1 = _fetch_binance("1w", limit_1w, now_ms)
        m1 = _fetch_binance("1M", limit_1m, now_ms)
        px = d1[-1]["close"]
        try:
            t = _http_json(f"{BINANCE_SPOT}/ticker/price?symbol=BTCUSDT")
            px = float(t["price"])
        except Exception:
            pass
        return {"source": "binance_spot", "klines": {"1d": d1, "1w": w1, "1M": m1},
                "price": px, "errors": errors, "fetched_at": now_tp().isoformat()}
    except Exception as e:
        errors.append(f"binance: {type(e).__name__}: {e}")
    try:
        d1 = _fetch_okx("1d", limit_1d, now_ms)
        w1 = _fetch_okx("1w", limit_1w, now_ms)
        try:
            m1 = _fetch_okx("1M", limit_1m, now_ms)
        except Exception:
            m1 = _monthly_from_daily(d1, now_ms)
        px = d1[-1]["close"]
        return {"source": "okx_spot", "klines": {"1d": d1, "1w": w1, "1M": m1},
                "price": px, "errors": errors, "fetched_at": now_tp().isoformat()}
    except Exception as e:
        errors.append(f"okx: {type(e).__name__}: {e}")
    try:
        # Bitstamp 無月線，日／週後用日線合成月線
        step_d, step_w = 86400, 604800
        def bs(step, limit):
            url = f"{BITSTAMP_OHLC}?step={step}&limit={min(limit, 1000)}"
            data = (_http_json(url).get("data") or {}).get("ohlc") or []
            rows = []
            for k in data:
                ts = int(k["timestamp"]) * 1000
                rows.append(_bar(ts, k["open"], k["high"], k["low"], k["close"], k["volume"],
                                 ts + step * 1000 - 1, now_ms))
            rows.sort(key=lambda r: r["open_time"])
            return rows
        d1, w1 = bs(step_d, limit_1d), bs(step_w, min(limit_1w, 1000))
        m1 = _monthly_from_daily(d1, now_ms)
        return {"source": "bitstamp_spot", "klines": {"1d": d1, "1w": w1, "1M": m1},
                "price": d1[-1]["close"], "errors": errors, "fetched_at": now_tp().isoformat()}
    except Exception as e:
        errors.append(f"bitstamp: {type(e).__name__}: {e}")
        raise RuntimeError("所有現貨 K 線來源都失敗：" + " | ".join(errors))


def load_or_fetch_market(out: Path, no_cache: bool) -> dict:
    cache = out / "klines_cache.json"
    if not no_cache and cache.is_file():
        try:
            d = json.loads(cache.read_text())
            ts = datetime.fromisoformat(d.get("fetched_at"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=TZ)
            if now_tp() - ts <= timedelta(minutes=30) and d.get("klines"):
                return d
        except Exception:
            pass
    market = fetch_market()
    cache.write_text(json.dumps(market, ensure_ascii=False, default=str))
    return market


# ---------------------------------------------------------------------------
# 既有資料：短線區間、鏈上、總經
# ---------------------------------------------------------------------------
def load_short_range() -> dict:
    out = {"range_low": 60000.0, "range_high": 67000.0, "stop": 57800.0, "source": "default"}
    for p in (ROOT / "out/short_model/short_model.json",
              ROOT / "out/short_model/levels.json",
              ROOT / "short_model.json"):
        if not p.is_file():
            continue
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        mr = d.get("main_range") or d
        if mr.get("low"):
            out["range_low"] = float(mr["low"])
            out["range_high"] = float(mr.get("high") or 67000)
            out["stop"] = float(mr.get("final_stop") or mr.get("rally_low") or 57800)
            out["source"] = str(p)
            return out
    return out


def _pick_money(label: str, text: str) -> float | None:
    """從「STH 成本 74.4K」和「STH 成本 74,356」裡優先取帶千分位的完整價位。"""
    comma, kform, large = [], [], []
    pat = re.escape(label) + r"\s*([\d,]+(?:\.\d+)?)(\s*[Kk萬])?"
    for m in re.finditer(pat, text):
        raw, suf = m.group(1), (m.group(2) or "").strip()
        try:
            if "," in raw:
                comma.append(float(raw.replace(",", "")))
            elif suf:
                x = float(raw.replace(",", ""))
                kform.append(x * (10000 if "萬" in suf else 1000))
            else:
                x = float(raw)
                if x >= 1000:
                    large.append(x)
        except ValueError:
            continue
    for group in (comma, large, kform):
        if group:
            return group[-1]
    return None


def _parse_onchain_text(text: str) -> dict:
    out: dict[str, Any] = {}
    m = re.search(r"目前週期位置：([^\n]+)", text)
    if m:
        out["cycle"] = m.group(1).strip().rstrip("。")
    m = re.search(r"前高是\s*(\d{4})/(\d{1,2})/(\d{1,2})\s*的\s*([\d,]+)", text)
    if m:
        out["ath"] = float(m.group(4).replace(",", ""))
        out["ath_date"] = f"{int(m.group(1))}/{int(m.group(2))}/{int(m.group(3))}"
    m = re.search(r"這輪低點是\s*(\d{4})/(\d{1,2})/(\d{1,2})\s*的\s*([\d,]+)", text)
    if m:
        out["onchain_low"] = float(m.group(4).replace(",", ""))
        out["onchain_low_date"] = f"{int(m.group(1))}/{int(m.group(2))}/{int(m.group(3))}"
    v = _pick_money("True Market Mean", text)
    if v is None:
        m = re.search(r"True Market Mean[（(]([\d,]+)", text)
        v = float(m.group(1).replace(",", "")) if m else None
    if v:
        out["tmm"] = v
    for key, lab in (("sth", "STH 成本"), ("bb_over", "超賣線"), ("bb_cool", "冷卻線"), ("lth", "LTH 成本")):
        v = _pick_money(lab, text)
        if v:
            out[key] = v
    m = re.search(r"LTH SOPR[｜|]([\d.]+)", text)
    if m:
        out["lth_sopr"] = float(m.group(1))
    # 「這輪 8/19 已經做到」站回短期成本並守住
    if re.search(r"這輪\s*8/19\s*已經做到|8/19\s*已經做到", text):
        out["stood_sth_month"] = 8
        out["stood_sth"] = True
    m = re.search(r"資料日\s*(\d{1,2})/(\d{1,2})", text)
    if m:
        out["data_md"] = f"{int(m.group(1))}/{int(m.group(2))}"
    return out


def load_onchain() -> dict:
    out: dict[str, Any] = {"source": None, "asof": None}
    text_p = ROOT / "onchain" / "onchain_text.txt"
    csv_p = ROOT / "onchain" / "combined_daily.csv"
    if text_p.is_file():
        out.update(_parse_onchain_text(text_p.read_text()))
        out["source"] = str(text_p)
    if csv_p.is_file():
        lines = [ln for ln in csv_p.read_text().splitlines() if ln.strip()]
        if len(lines) >= 2:
            header = [x.strip() for x in lines[0].split(",")]
            # last non-empty data row
            row = lines[-1].split(",")
            rec = {header[i]: row[i] if i < len(row) else "" for i in range(len(header))}
            def fget(*keys):
                for k in keys:
                    v = rec.get(k) or rec.get(k.upper()) or rec.get(k.lower())
                    if v not in (None, ""):
                        try:
                            return float(v)
                        except ValueError:
                            continue
                return None
            date = rec.get("") or rec.get("date") or rec.get("Date") or row[0]
            out["csv_date"] = date
            out.setdefault("tmm", fget("TMM"))
            out.setdefault("sth", fget("STHRP", "STH"))
            out.setdefault("bb_over", fget("BBover"))
            out.setdefault("bb_cool", fget("BBcool"))
            out.setdefault("lth", fget("LTHRP"))
            out.setdefault("lth_sopr", fget("LTHSOPR"))
            if date and not out.get("asof"):
                out["asof"] = date
    return out


def _age_days(d: datetime) -> int:
    return (now_tp().date() - d.date()).days


def _parse_gold_macro(text: str) -> dict:
    rec: dict[str, Any] = {}
    m = re.search(r"DXY\s*(\d{1,2}/\d{1,2})[^0-9]{0,12}(?:約\s*)?(\d{2,3}\.\d{1,2})", text)
    if m:
        rec["dxy"] = float(m.group(2))
        rec["dxy_md"] = m.group(1)
        rec["dxy_new_high"] = bool(re.search(r"新高|創新高|再做新高", text))
    # 10 年期：優先取「11/16 小回 5.12%」或最後一個「10 年期 … 5.xx%」收盤
    m = re.search(r"10 年期[^\n]{0,80}?(\d{1,2}/\d{1,2})[^\n]{0,20}?小回\s*(\d+\.\d+)\s*%", text)
    if not m:
        m = re.search(r"10 年期[^\n]{0,40}?(\d{1,2}/\d{1,2})[^\n]{0,30}?收\s*(\d+\.\d+)\s*%", text)
    if m:
        rec["y10"] = float(m.group(2))
        rec["y10_md"] = m.group(1)
    else:
        m = re.search(r"10Y\s+(\d+\.\d+)\s*%", text) or re.search(r"10 年期\s+(\d+\.\d+)\s*%", text)
        if m:
            rec["y10"] = float(m.group(1))
    # 2 年期：取箭號後的最新值「4.88% → 4.83%」或單獨 2 年期 x.xx%
    m = re.search(r"2 年期[^\n]{0,40}?(\d+\.\d+)\s*%\s*[→~～-]+\s*(\d+\.\d+)\s*%", text)
    if m:
        rec["y2"] = float(m.group(2))
    else:
        ms = list(re.finditer(r"2 年期\s+(\d+\.\d+)\s*%", text))
        if ms:
            rec["y2"] = float(ms[-1].group(1))
        else:
            m = re.search(r"2Y\s+(\d+\.\d+)\s*%", text)
            if m:
                rec["y2"] = float(m.group(1))
    rec["hike_next"] = bool(re.search(r"12\s*月升息|押[^。]{0,8}12\s*月", text))
    rec["dxy_new_high"] = rec.get("dxy_new_high") or bool(re.search(r"美元[^\n]{0,20}新高", text))
    # 政策利率目標區間
    m = re.search(r"(3\.\d{2})\s*[–—-]\s*(4\.\d{2})\s*%", text)
    if m:
        rec["ffr_low"] = float(m.group(1))
        rec["ffr_high"] = float(m.group(2))
    return rec


def _md_to_date(md: str, report: datetime) -> datetime | None:
    try:
        mo, d = [int(x) for x in md.split("/")]
        year = report.year
        dt = datetime(year, mo, d, tzinfo=TZ)
        # 若日期在報告之後超過一週，多半是去年
        if dt.date() - report.date() > timedelta(days=7):
            dt = datetime(year - 1, mo, d, tzinfo=TZ)
        return dt
    except Exception:
        return None


def load_macro() -> dict:
    rec: dict[str, Any] = {"fresh": False, "liq_fresh": False, "notes": []}
    # 1) 最新黃金日報
    gold_dir = Path("/workspace/gold")
    daily = None
    today = now_tp()
    for i in range(0, 8):
        p = gold_dir / f"daily-{(today - timedelta(days=i)).strftime('%Y-%m-%d')}.md"
        if p.is_file():
            daily = p
            rec["report_path"] = str(p)
            rec["report_date"] = p.stem.replace("daily-", "")
            break
    if daily:
        parsed = _parse_gold_macro(daily.read_text())
        rec.update(parsed)
        # 資料日：用 dxy_md / y10_md，沒有就用報告日前一交易日保守估計
        asof = None
        for key in ("dxy_md", "y10_md"):
            if rec.get(key):
                asof = _md_to_date(rec[key], today)
                if asof:
                    break
        if asof is None:
            # 報告日當天早上多半用前一日收盤
            asof = today - timedelta(days=1)
        rec["asof"] = asof.date().isoformat()
        rec["age_days"] = _age_days(asof)
        rec["fresh"] = bool(rec["age_days"] <= MACRO_DAILY_MAX_AGE and rec.get("dxy") and rec.get("y10"))
        if not rec["fresh"]:
            rec["notes"].append(f"總經日報數字過舊或不全 age={rec.get('age_days')}")
    else:
        rec["notes"].append("找不到黃金日報")

    # 2) 政策利率：日報沒有就讀本地目標區間檔；目標只在會議時變，允許較舊
    if rec.get("ffr_high") is None:
        for p in (Path("/workspace/macro-page/data/DFEDTARU.csv"),
                  ROOT / "data/macro/DFEDTARU.csv"):
            if not p.is_file():
                continue
            lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
            last = lines[-1].split(",")
            try:
                rec["ffr_high"] = float(last[-1])
                rec["ffr_asof"] = last[0]
            except Exception:
                continue
            break
    if rec.get("ffr_low") is None:
        p = Path("/workspace/macro-page/data/DFEDTARL.csv")
        if p.is_file():
            try:
                rec["ffr_low"] = float(p.read_text().splitlines()[-1].split(",")[-1])
            except Exception:
                pass

    # 3) 流動性
    liq_p = Path("/workspace/macro-page/data/net_liquidity.csv")
    if liq_p.is_file():
        lines = [ln for ln in liq_p.read_text().splitlines() if ln.strip() and not ln.startswith(",")]
        if lines:
            last = lines[-1].split(",")
            try:
                rec["liq_date"] = last[0]
                rec["liq_net"] = float(last[-1])
                ld = datetime.fromisoformat(last[0]).replace(tzinfo=TZ)
                rec["liq_age_days"] = _age_days(ld)
                rec["liq_fresh"] = rec["liq_age_days"] <= MACRO_LIQ_MAX_AGE
            except Exception:
                rec["notes"].append("流動性檔解析失敗")
    if not rec.get("liq_fresh"):
        rec["notes"].append("流動性數字過舊或缺失，正文改定性")
    return rec


# ---------------------------------------------------------------------------
# K 線狀態
# ---------------------------------------------------------------------------
def kline_state(market: dict, onchain: dict, short: dict) -> dict:
    d1, w1, m1 = market["klines"]["1d"], market["klines"]["1w"], market["klines"]["1M"]
    cd, cw, cm = closed_bars(d1), closed_bars(w1), closed_bars(m1)
    px = float(market["price"])
    d_c = [r["close"] for r in cd]
    w_c = [r["close"] for r in cw]
    m_c = [r["close"] for r in cm]

    st: dict[str, Any] = {
        "price": px,
        "ma120_d": sma(d_c, 120),
        "ma30_w": sma(w_c, 30),
        "ma50_w": sma(w_c, 50),
        "ma120_w": sma(w_c, 120),
        "ma200_w": sma(w_c, 200),
        "range_low": short["range_low"],
        "range_high": short["range_high"],
    }

    # 年開盤
    year = now_tp().year
    yo = next((r for r in d1 if datetime.fromtimestamp(r["open_time"] / 1000, TZ).year == year), None)
    st["year_open"] = yo["open"] if yo else None
    ydays = [r for r in cd if datetime.fromtimestamp(r["open_time"] / 1000, TZ).year == year]
    if ydays and st["year_open"]:
        below = sum(1 for r in ydays if r["close"] < st["year_open"])
        st["year_below_n"] = below
        st["year_days"] = len(ydays)
        st["year_below_pct"] = below / len(ydays)
        st["bear_year"] = st["year_below_pct"] >= 0.80
    else:
        st["bear_year"] = None

    # 週線 MACD
    wl, ws, wh = macd(w_c)
    st["w_macd_line"] = wl[-1] if wl else None
    st["w_macd_signal"] = ws[-1] if ws else None
    st["w_macd_hist"] = wh[-1] if wh else None
    xt, xi = last_cross(wl, ws)
    st["w_macd_cross"] = xt
    st["w_macd_cross_idx"] = xi
    st["w_macd_side"] = "零軸上" if (st["w_macd_line"] or 0) >= 0 else "零軸下"
    # 上週（最後已收盤週）是否剛收破 MA30
    if st["ma30_w"] is not None and len(cw) >= 2:
        last = cw[-1]
        prev_ma = sma([r["close"] for r in cw[:-1]], 30)
        st["week_close"] = last["close"]
        st["week_below_ma30"] = last["close"] < st["ma30_w"]
        st["week_broke_ma30"] = bool(prev_ma and cw[-2]["close"] >= prev_ma and last["close"] < st["ma30_w"])
        st["week_reclaimed_ma30"] = bool(prev_ma and cw[-2]["close"] < prev_ma and last["close"] >= st["ma30_w"])
    else:
        st["week_below_ma30"] = True if st["ma30_w"] and px < st["ma30_w"] else False
        st["week_broke_ma30"] = False
        st["week_reclaimed_ma30"] = False

    # 週線高低點（簡單 pivot）
    def pivots(rows, key_hi="high", key_lo="low"):
        hs, ls = [], []
        for i in range(1, len(rows) - 1):
            if rows[i][key_hi] >= rows[i - 1][key_hi] and rows[i][key_hi] > rows[i + 1][key_hi]:
                hs.append(rows[i][key_hi])
            if rows[i][key_lo] <= rows[i - 1][key_lo] and rows[i][key_lo] < rows[i + 1][key_lo]:
                ls.append(rows[i][key_lo])
        return hs, ls
    hs, ls = pivots(cw)
    last_w = cw[-1] if cw else None
    prior = cw[:-1]
    recent = prior[-8:] if len(prior) >= 8 else prior
    if last_w and recent:
        st["wk_lh"] = last_w["high"] < max(r["high"] for r in recent)
        st["wk_hh"] = last_w["high"] > max(r["high"] for r in recent)
        st["wk_hl"] = last_w["low"] > min(r["low"] for r in recent)
        st["wk_ll"] = last_w["low"] < min(r["low"] for r in recent)
    elif len(hs) >= 2 and len(ls) >= 2:
        st["wk_lh"] = hs[-1] < hs[-2]
        st["wk_hh"] = hs[-1] > hs[-2]
        st["wk_hl"] = ls[-1] > ls[-2]
        st["wk_ll"] = ls[-1] < ls[-2]
    else:
        st["wk_lh"] = st["wk_hh"] = st["wk_hl"] = st["wk_ll"] = False

    # 日線相對 MA120
    st["below_ma120_d"] = st["ma120_d"] is not None and px < st["ma120_d"]

    # 月線
    if cm:
        last_m = cm[-1]
        st["month_closed_low"] = last_m["low"]
        st["month_closed_high"] = last_m["high"]
        st["month_closed_open"] = last_m["open_time"]
    prog = m1[-1] if m1 and not m1[-1]["closed"] else None
    st["month_in_progress"] = bool(prog)
    if prog and cm:
        st["broke_prev_month_low"] = prog["low"] < cm[-1]["low"]
    else:
        st["broke_prev_month_low"] = False
    ml, ms, mh = macd(m_c) if m_c else ([], [], [])
    st["m_macd_line"] = ml[-1] if ml else None
    st["m_macd_signal"] = ms[-1] if ms else None
    xtm, _ = last_cross(ml, ms) if ml else (None, None)
    st["m_macd_cross"] = xtm
    st["m_macd_side"] = "零軸上" if (st["m_macd_line"] or 0) >= 0 else "零軸下"

    # 圖上本輪低點：年內日線最低，或短線停損錨
    ytd = [r for r in d1 if datetime.fromtimestamp(r["open_time"] / 1000, TZ).year == year]
    if ytd:
        lo = min(ytd, key=lambda r: r["low"])
        st["chart_low"] = lo["low"]
        st["chart_low_date"] = datetime.fromtimestamp(lo["open_time"] / 1000, TZ)
    else:
        st["chart_low"] = short.get("stop") or 57800.0
        st["chart_low_date"] = None

    # 前高：鏈上優先
    st["ath"] = onchain.get("ath")
    st["ath_date"] = onchain.get("ath_date")
    if not st["ath"] and d1:
        a = max(d1, key=lambda r: r["high"])
        st["ath"] = a["high"]
        ad = datetime.fromtimestamp(a["open_time"] / 1000, TZ)
        st["ath_date"] = f"{ad.year}/{ad.month}/{ad.day}"

    st["sth"] = onchain.get("sth")
    st["tmm"] = onchain.get("tmm")
    st["bb_over"] = onchain.get("bb_over")
    st["bb_cool"] = onchain.get("bb_cool")
    st["lth"] = onchain.get("lth")
    st["lth_sopr"] = onchain.get("lth_sopr")
    st["cycle"] = onchain.get("cycle") or "跌破短期成本後的弱勢"
    st["stood_sth"] = bool(onchain.get("stood_sth"))
    st["stood_sth_month"] = onchain.get("stood_sth_month") or 8

    # 交會帶：週線 MA50、日線 MA120、STH、TMM
    band = [v for v in (st["ma50_w"], st["ma120_d"], st["sth"], st["tmm"]) if v]
    if band:
        st["band_lo"], st["band_hi"] = min(band), max(band)
    else:
        st["band_lo"] = st["band_hi"] = None

    # 相對超賣線
    if st["bb_over"]:
        st["near_over"] = abs(px - st["bb_over"]) / st["bb_over"] <= 0.025
        st["below_over"] = px < st["bb_over"]
    else:
        st["near_over"] = st["below_over"] = False

    st["below_ma200_w"] = st["ma200_w"] is not None and px < st["ma200_w"]
    st["below_tmm"] = st["tmm"] is not None and px < st["tmm"]
    st["below_sth"] = st["sth"] is not None and px < st["sth"]
    return st


# ---------------------------------------------------------------------------
# 組句
# ---------------------------------------------------------------------------
def mid_text(st: dict, macro: dict) -> str:
    bits = []
    below_d = st.get("below_ma120_d")
    below_w = st.get("week_below_ma30")
    if below_d and below_w:
        bits.append("週線和日線都還壓在均線下面。")
    elif below_w and not below_d:
        bits.append("週線還壓在均線下面，日線已經站上 MA120。")
    elif below_d and not below_w:
        bits.append("日線還在 MA120 下方，週線已站上 MA30。")
    else:
        bits.append("週線和日線都站在均線上面。")

    if st.get("ma30_w"):
        if st.get("week_broke_ma30"):
            bits.append(f"週線 MA30 在 {n0(st['ma30_w'])}，上週已經收破；")
        elif below_w:
            bits.append(f"週線 MA30 在 {n0(st['ma30_w'])}，收盤仍在它下面；")
        elif st.get("week_reclaimed_ma30"):
            bits.append(f"週線 MA30 在 {n0(st['ma30_w'])}，上週已經收復；")
        else:
            bits.append(f"週線 MA30 在 {n0(st['ma30_w'])}，收盤還在它上面；")

    # MACD
    side = st.get("w_macd_side")
    cross = st.get("w_macd_cross")
    if side == "零軸上" and cross == "死叉":
        bits.append("週線 MACD 還在零軸上就死叉，")
    elif side == "零軸下" and cross == "金叉":
        bits.append("週線 MACD 已在零軸下金叉，")
    elif side == "零軸下" and cross == "死叉":
        bits.append("週線 MACD 還在零軸下，沒有金叉，")
    elif side == "零軸上" and cross == "金叉":
        bits.append("週線 MACD 在零軸上金叉，")
    else:
        bits.append("週線 MACD 還沒走出清楚的交叉，")

    if st.get("wk_lh") and not st.get("wk_hl"):
        bits.append("高點下移，低點也還沒墊高。")
    elif st.get("wk_hh") and st.get("wk_hl"):
        bits.append("高點和低點都在墊高。")
    elif st.get("wk_lh") and st.get("wk_ll"):
        bits.append("高點下移，低點也在走低。")
    else:
        bits.append("高低點還沒走出清楚的方向。")

    # 現價與超賣線
    px_s = n0(st["price"])
    if st.get("bb_over") and st.get("near_over"):
        bits.append(f"現價 {px_s}，踩在短期持有者布林超賣線 {n0(st['bb_over'])} 附近。")
    elif st.get("bb_over") and st.get("below_over"):
        bits.append(f"現價 {px_s}，還在短期持有者布林超賣線 {n0(st['bb_over'])} 下方。")
    elif st.get("bb_over"):
        bits.append(f"現價 {px_s}，已經離開短期持有者布林超賣線。")
    else:
        bits.append(f"現價 {px_s}。")

    if st.get("band_lo") and st.get("band_hi"):
        if st["price"] < st["band_lo"]:
            bits.append(f"上方壓力先看 {n0(st['band_lo'])} 到 {n0(st['band_hi'])}，週線 MA50、日線 MA120、短期成本和鏈上市場均價都擠在這裡。")
        elif st["price"] > st["band_hi"]:
            bits.append(f"已經站上 {n0(st['band_lo'])} 到 {n0(st['band_hi'])} 這條交會帶，週線 MA50、日線 MA120、短期成本和鏈上市場均價都在這一帶。")
        else:
            bits.append(f"還在 {n0(st['band_lo'])} 到 {n0(st['band_hi'])} 這條交會帶裡，週線 MA50、日線 MA120、短期成本和鏈上市場均價都擠在這裡。")

    p1 = "".join(bits)

    # 總經＋鏈上（中期偏總經）
    p2 = mid_macro(st, macro)

    # 做法與看錯
    act = []
    if below_d or below_w:
        act.append("這裡不追多，也不輕易重倉空，靠近交會帶就減倉。")
    else:
        act.append("均線上方可以順著做，靠近交會帶先減倉，不要追太高。")
    if side == "零軸下" and cross == "金叉":
        act.append("週線 MACD 已在零軸下金叉，可以開始考慮大級別多，但仍只做現貨或小倉。")
    else:
        act.append("要等週線 MACD 回到零軸下再金叉，才做大級別多，現在只做現貨或小倉。")
    if below_w:
        act.append("週線收盤重新站回並守住週線 MA30，這套就要重估；")
    else:
        act.append("週線收盤再跌回週線 MA30 下方，這套就要重估；")
    act.append(f"若週線收盤跌破主區間下沿 {n0(st['range_low'])}，就改按更弱的路徑來看。")
    p3 = "".join(act)
    # 最後一句前若是「要重估；」接「若」，保持分號連接，其餘用句號分開已在片段裡
    return p1 + "\n\n" + p2 + "\n\n" + p3


def mid_macro(st: dict, macro: dict) -> str:
    parts = []
    if macro.get("fresh") and macro.get("dxy") and macro.get("y10") and macro.get("y2"):
        md = macro.get("dxy_md") or (macro.get("asof") and "/".join(str(int(x)) for x in macro["asof"].split("-")[1:]))
        high = "，再做新高" if macro.get("dxy_new_high") else ""
        parts.append(f"美元 {md} 收到 {n2(macro['dxy'])}{high}；")
        parts.append(f"10 年期 {n2(macro['y10'])}%、2 年期 {n2(macro['y2'])}%")
        if macro.get("ffr_high") is not None:
            if macro["y2"] > macro["ffr_high"]:
                parts.append("，還高過政策利率")
            elif macro["y2"] < (macro.get("ffr_low") or macro["ffr_high"]):
                parts.append("，已經低於政策利率")
            else:
                parts.append("，和政策利率差不多")
        if macro.get("hike_next"):
            parts.append("，市場在押 12 月再升息")
        # 流動性：過舊只用定性、不寫數字
        if macro.get("liq_fresh") and macro.get("liq_net"):
            # 仍不在正文寫出 5,801 這種大數，以免過細；有新鮮數字才說鬆緊
            parts.append("，錢也沒有放鬆")
        else:
            parts.append("，錢也沒有放鬆")
        parts.append("。")
    else:
        parts.append("美元和長端利率還在高檔，政策也還沒轉鬆，錢也沒有放鬆。")
    return "".join(parts)


def long_text(st: dict, macro: dict) -> str:
    bits = []
    if st.get("ath") and st.get("ath_date"):
        bits.append(f"從 {st['ath_date']} 前高 {n0(st['ath'])} 下來後，")
    if st.get("year_open") is not None:
        if st.get("bear_year"):
            bits.append(f"今年絕大多數日線都收在年開盤價 {n0(st['year_open'])} 下方，這是熊市年。")
        elif st.get("bear_year") is False:
            bits.append(f"今年多數日線收在年開盤價 {n0(st['year_open'])} 上方。")
        else:
            bits.append(f"年開盤價在 {n0(st['year_open'])}。")
    if st.get("month_in_progress"):
        mo = now_tp().month
        if st.get("broke_prev_month_low"):
            bits.append(f"月線 {mo} 月還沒收完，已跌破 10 月低點；" if mo == 11 else f"月線 {mo} 月還沒收完，已跌破前一個月低點；")
        else:
            bits.append(f"月線 {mo} 月還沒收完；")
    side, cross = st.get("m_macd_side"), st.get("m_macd_cross")
    if side == "零軸上" and cross == "死叉":
        bits.append("月線 MACD 仍在零軸上死叉。")
    elif side == "零軸下" and cross == "金叉":
        bits.append("月線 MACD 已在零軸下金叉。")
    elif side == "零軸下":
        bits.append("月線 MACD 還在零軸下。")
    elif side == "零軸上":
        bits.append("月線 MACD 還在零軸上。")
    if st.get("ma200_w"):
        if st.get("below_ma200_w"):
            bits.append(f"價格也掉到週線 MA200 的 {n0(st['ma200_w'])} 下面。")
        else:
            bits.append(f"價格還在週線 MA200 的 {n0(st['ma200_w'])} 上面。")
    p1 = "".join(bits)

    # 鏈上＋總經
    midb = []
    cycle = st.get("cycle") or "跌破短期成本後的弱勢"
    # 去掉可能的來源口吻，只留位置
    cycle = re.sub(r"^(目前週期位置：)?", "", cycle)
    midb.append(f"鏈上仍是{cycle}。")
    if st.get("chart_low"):
        midb.append(f"圖上這輪低點在 {n0(st['chart_low'])}，回撤比過去淺，還不能當成週期大底。")
    if st.get("stood_sth") and st.get("below_sth"):
        midb.append(f"{st['stood_sth_month']} 月曾站回並守住，現在又跌回去，也還沒站穩市場均價。")
    elif st.get("stood_sth") and not st.get("below_tmm"):
        midb.append(f"{st['stood_sth_month']} 月曾站回並守住，現在還在市場均價附近。")
    elif st.get("below_tmm"):
        midb.append("現在還沒站穩市場均價。")
    if st.get("lth"):
        if st.get("lth_sopr") is not None and st["lth_sopr"] <= 1.05:
            midb.append(f"長期持有者成本在 {n0(st['lth'])}，賣出已接近損益兩平。")
        else:
            midb.append(f"長期持有者成本在 {n0(st['lth'])}。")
    # 總經定性，避免重複中期已寫的數字
    if macro.get("fresh") and macro.get("dxy_new_high"):
        midb.append("利率沒回落、美元續強，大底要等利率見頂回落才容易確認。")
    else:
        midb.append("利率和美元都還沒出現轉向，大底要等利率見頂回落才容易確認。")
    p2 = "".join(midb)

    act = []
    act.append("長線只用現貨分批，不要一次做滿。")
    act.append("先看這個低點能否守住，再等站回短期成本和市場均價。")
    act.append("月線或週線收盤跌破這個低點，底部就作廢，下面改看長期持有者成本。")
    p3 = "".join(act)
    return p1 + "\n\n" + p2 + "\n\n" + p3


# ---------------------------------------------------------------------------
# 自檢
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(
    r"(?:(?:19|20)\d{2}/\d{1,2}/\d{1,2})"
    r"|\d{1,2}/\d{1,2}"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d+\.\d+"
    r"|(?<!MA)(?<!MA\d)(?<!MA\d\d)\d+"
)


def _normalize_num(tok: str) -> str:
    return tok.replace(",", "").rstrip("%")


def extract_figures(text: str) -> list[str]:
    """取出正文裡的價位、利率、日期數字；略過指標名稱裡的期數（MA30 等）和「N 年期」。"""
    # 先把指標名和「N 年期」遮住
    masked = re.sub(r"MA\d+", "MA#", text)
    masked = re.sub(r"(\d+)\s*年期", r"年期", masked)
    found = []
    for m in _NUM_RE.finditer(masked):
        tok = m.group(0)
        if tok == "#":
            continue
        # 「11 月」「12 月」「8 月」「10 月」月分標籤：用完整「N 月」當鍵，避免和別的 10 撞上
        found.append(tok)
    return found


def month_tokens(text: str) -> list[str]:
    return re.findall(r"\d{1,2}\s*月", text)


def self_check(mid: str, long: str) -> dict:
    errors = []
    warnings = []
    for name, sec in (("中期", mid), ("長期", long)):
        for w in FORBIDDEN_SOURCES:
            if w == "@":
                if re.search(r"@[A-Za-z0-9_]+", sec):
                    errors.append(f"{name} 含帳號")
                continue
            if w in sec:
                errors.append(f"{name} 含來源名字或網址：{w}")
        for w in PERIOD_LABELS:
            if w in sec:
                errors.append(f"{name} 含週期標籤：{w}")
        if "距離" in sec or re.search(r"(?<![遠低高])距(?!離)", sec):
            # 允許「遠低於」等，禁止「距 72,000」
            if re.search(r"距\s*[\d,]", sec) or "距離" in sec:
                errors.append(f"{name} 含距離字眼")
        for w in MA_WRONG:
            if w in sec:
                errors.append(f"{name} MA120 寫法錯誤：{w}")
        if re.search(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", sec):
            errors.append(f"{name} 含 emoji")
        figs = extract_figures(sec)
        # 規範化後查重複（同一段）
        norm = [_normalize_num(x) for x in figs]
        # 月份「11 月」與日期「11/16」視為不同；只查完全相同的規範化數字
        seen = {}
        for raw, n in zip(figs, norm):
            if n in seen:
                errors.append(f"{name} 數字重複：{raw}（先前 {seen[n]}）")
            else:
                seen[n] = raw
        # 「10 月」與「10」若單獨出現才算；月份另計
        cjk = len(re.findall(r"[\u4e00-\u9fff]", sec))
        if cjk < 160 or cjk > 260:
            warnings.append(f"{name} 漢字 {cjk}，超出約 200 字的寬區間（160–260）")
        # MA120 若出現須寫成「日線 MA120」或單獨「MA120」不得寫 SMA
        if "MA120" in sec and "週線 MA120" not in sec and "日線 MA120" not in sec and "MA120" in sec:
            # 允許「日線 MA120」；若只有 MA120 也接受（規則寫 120 日均線一律寫 MA120）
            pass
        if "週線 MA" in sec:
            if re.search(r"週線 MA\d+", sec) is None and re.search(r"週線MA\d+", sec) is None:
                pass
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "mid_cjk": len(re.findall(r"[\u4e00-\u9fff]", mid)),
            "long_cjk": len(re.findall(r"[\u4e00-\u9fff]", long))}


def cjk_count(s: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", s))


def generate(out: Path, no_cache: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    short = load_short_range()
    onchain = load_onchain()
    macro = load_macro()
    market = load_or_fetch_market(out, no_cache)
    st = kline_state(market, onchain, short)
    mid = mid_text(st, macro)
    long = long_text(st, macro)
    # 輕微修整：避免「下面。週線」漏空格已用句號；去掉「；。」並排
    mid = re.sub(r"；。", "。", mid)
    long = re.sub(r"；。", "。", long)
    chk = self_check(mid, long)
    fc = "【中期】\n" + mid.strip() + "\n\n【長期】\n" + long.strip() + "\n"
    payload = {
        "generated_at": now_tp().isoformat(),
        "kline_source": market.get("source"),
        "price": st["price"],
        "mid": mid,
        "long": long,
        "word_counts": {"mid_cjk": chk["mid_cjk"], "long_cjk": chk["long_cjk"]},
        "self_check": chk,
        "macro_notes": macro.get("notes"),
        "macro_fresh": macro.get("fresh"),
        "liq_fresh": macro.get("liq_fresh"),
        "short_range": short,
        "states": {
            "ma30_w": st.get("ma30_w"),
            "ma50_w": st.get("ma50_w"),
            "ma120_d": st.get("ma120_d"),
            "ma200_w": st.get("ma200_w"),
            "w_macd_line": st.get("w_macd_line"),
            "w_macd_cross": st.get("w_macd_cross"),
            "w_macd_side": st.get("w_macd_side"),
            "m_macd_cross": st.get("m_macd_cross"),
            "m_macd_side": st.get("m_macd_side"),
            "band": [st.get("band_lo"), st.get("band_hi")],
            "year_open": st.get("year_open"),
            "chart_low": st.get("chart_low"),
            "bear_year": st.get("bear_year"),
            "year_below_pct": st.get("year_below_pct"),
        },
    }
    (out / "forecast_midlong.txt").write_text(fc)
    (out / "midlong.txt").write_text(fc)
    (out / "midlong_model.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="BTC 中期／長期口語模型（腳本執行，不上線）")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="輸出目錄")
    p.add_argument("--forecast", action="store_true", help="把【中期】【長期】印到 stdout，供 publish 使用")
    p.add_argument("--no-cache", action="store_true", help="不使用 30 分鐘 K 線快取")
    args = p.parse_args()
    out = Path(args.out)
    try:
        payload = generate(out, no_cache=args.no_cache)
    except Exception as e:
        print(f"midlong_model failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    chk = payload["self_check"]
    for w in chk.get("warnings") or []:
        print("WARN", w, file=sys.stderr)
    if not chk["ok"]:
        for e in chk["errors"]:
            print("SELFCHECK", e, file=sys.stderr)
        print(payload["mid"] + "\n\n" + payload["long"])
        return 2
    text = "【中期】\n" + payload["mid"].strip() + "\n\n【長期】\n" + payload["long"].strip()
    print(text)
    print(
        f"MIDLONG_OK mid_cjk={chk['mid_cjk']} long_cjk={chk['long_cjk']} "
        f"price={payload['price']} source={payload.get('kline_source')}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

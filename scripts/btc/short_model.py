#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC 短線模型（試做，不上線）。

每天只做一次程式檢查，不靠語言模型重寫分析。
初始劇本取自 2026-11-17 已確認結論：主區間 60,000–67,000。
未結構脫離時沿用同一套劇本，只更新現價與距觸發價距離。
結構脫離時才重算主區間與劇本，並寫出脫離原因。

K 線：先試幣安現貨 api.binance.com /api/v3/klines（4h／1d／1w）。
不准碰 fapi，不用 proxy／VPN／偽裝地區。被擋則改 OKX 或 Bitstamp 現貨。

輸出（預設）／workspace/btc-sentiment/out/short_model_trial/：
  short_model.json   結構化狀態（主區間、劇本價位、檢查結果）
  btc_short.txt      /btc 短線段樣稿
  report_btc.txt     /report BTC 段樣稿
"""
from __future__ import annotations

import argparse, os
import json
import re
import math
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常數（由規則與固定流程提煉，日常檢查不再重讀規則全文）
# ---------------------------------------------------------------------------
TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")
UTC = timezone.utc

SMA_LEN = 120
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
FIB_0786, FIB_0886 = 0.786, 0.886
DROP_BEAR_PCT = 0.20
SMA_FLAT_SPAN = 4  # SMA[t] 對 SMA[t-4]，共 5 根讀數
SMA_FLAT_PCT = 0.005  # 5 根變動低於此視為有走平傾向
HUG_MA_PCT = 0.03  # 價格距均線超過此值，不算「貼著均線震盪待突破」
MACD_DEEP_FRAC = 0.40  # |MACD| 佔近段振幅比例，超過視為深處
MACD_NEAR_ZERO_FRAC = 0.15
NEAR_UPPER = 1000  # 「靠近上沿」距離
LOWER_ZONE_TOP = 61800  # 下沿分批區上緣（價值區下沿）
INTEGER_STEP = 5000
UPPER_ROUND = 1000
STAND_FIRM_BAND = 500  # 上沿到上沿+500 為站穩確認帶
VALUE_AREA_PCT = 0.70
SHARP_LEG_PCT = 0.04
PAUSE_MIN_4H = 3
SWING_LEFT = 5
SWING_RIGHT = 5

# 2026-11-17 已確認結論（再修正段）。初始狀態直接採用，不用程式重估。
INIT_RANGE_LOW = 60000.0
INIT_RANGE_HIGH = 67000.0
INIT_UPPER_REFS = [67292.0, 67568.0]  # 11/14 停頓／11/13（週五）低點
INIT_VAL = 61800.0  # 價值區下沿（已確認數字，非正式 70% VA 重算）
INIT_FIB_0886 = 61174.0  # 上一波完整主升 57,800→87,396 的 0.886
INIT_FIB_0786 = 64134.0  # 同一段 0.786
INIT_STOP = 57800.0  # 最終停損／錨定低點（7/1 低點）
INIT_STAND_FIRM = [67000.0, 67500.0]
INIT_RALLY_LOW = 57800.2
INIT_RALLY_HIGH = 87395.7
INIT_RECOMPUTE_BAR = "2026-11-16"  # 11/17 結論成立時最後一根已收完日線（台北日期）

BINANCE_SPOT = "https://api.binance.com/api/v3"
def _lic(key, env):
    """授權開關（2026-11-26）：btc_summary.json 的 omit_okx／omit_cq，預設關；環境變數 BTC_OMIT_OKX／BTC_OMIT_CQ 可暫時覆蓋。"""
    v = os.environ.get(env)
    if v not in (None, ""): return v.strip().lower() in ("1", "true", "yes", "on")
    _d = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(_d, "btc_summary.json"), os.path.join(os.path.dirname(_d), "btc_summary.json")):
        try:
            if os.path.isfile(p): return bool(json.load(open(p, encoding="utf-8")).get(key, False))
        except Exception: continue
    return False
OKX_CANDLES = "https://www.okx.com/api/v5/market/history-candles"
BITSTAMP_OHLC = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
UA = {"User-Agent": "btc-short-model/1.0"}

DEFAULT_OUT = Path("/workspace/btc-sentiment/out/short_model_trial")


# ---------------------------------------------------------------------------
# HTTP / K 線
# ---------------------------------------------------------------------------
def _http_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} {url}")
        return json.loads(resp.read().decode())


def _bar(open_time_ms: int, o, h, l, c, volume, close_time_ms: int, quote: float, now_ms: int) -> dict:
    return {
        "open_time": int(open_time_ms),
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(volume),
        "close_time": int(close_time_ms),
        "quote": float(quote),
        "closed": int(close_time_ms) < now_ms,
    }


def _fetch_binance(interval: str, limit: int, now_ms: int) -> list[dict]:
    url = f"{BINANCE_SPOT}/klines?symbol=BTCUSDT&interval={interval}&limit={limit}"
    data = _http_json(url)
    rows = []
    for k in data:
        rows.append(_bar(k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7], now_ms))
    return rows


def _fetch_binance_ticker() -> float:
    data = _http_json(f"{BINANCE_SPOT}/ticker/price?symbol=BTCUSDT")
    return float(data["price"])


def _okx_bar(interval: str) -> str:
    return {"1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}[interval]


def _fetch_okx(interval: str, limit: int, now_ms: int) -> list[dict]:
    bar = _okx_bar(interval)
    # OKX 單次最多 300，若不夠再往前翻頁
    rows: list[dict] = []
    after = None
    remain = max(limit, 1)
    while remain > 0:
        batch = min(remain, 300)
        url = f"{OKX_CANDLES}?instId=BTC-USDT&bar={bar}&limit={batch}"
        if after:
            url += f"&after={after}"
        payload = _http_json(url)
        data = payload.get("data") or []
        if not data:
            break
        # 回傳由新到舊；欄位 ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm
        chunk = []
        for k in data:
            ts = int(k[0])
            # OKX 的 ts 是開盤時間；週期毫秒
            ms = {"1H": 3600000, "4H": 14400000, "1D": 86400000, "1W": 604800000}[bar]
            chunk.append(_bar(ts, k[1], k[2], k[3], k[4], k[5], ts + ms - 1, k[7] if len(k) > 7 else k[6], now_ms))
        rows = chunk[::-1] + rows
        after = int(data[-1][0])
        remain -= len(data)
        if len(data) < batch:
            break
    return rows


def _bitstamp_step(interval: str) -> int:
    return {"1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}[interval]


def _fetch_bitstamp(interval: str, limit: int, now_ms: int) -> list[dict]:
    step = _bitstamp_step(interval)
    url = f"{BITSTAMP_OHLC}?step={step}&limit={min(limit, 1000)}"
    payload = _http_json(url)
    data = payload.get("data", {}).get("ohlc") or []
    rows = []
    for k in data:
        ts = int(k["timestamp"]) * 1000
        close_ms = ts + step * 1000 - 1
        rows.append(_bar(ts, k["open"], k["high"], k["low"], k["close"], k["volume"], close_ms, 0.0, now_ms))
    rows.sort(key=lambda r: r["open_time"])
    return rows


def fetch_market(limit_4h: int = 500, limit_1d: int = 400, limit_1w: int = 200, skip_ticker: bool = False) -> dict:
    """回傳 klines 與來源名稱。失敗才往下一個免費現貨來源。
    skip_ticker：發布端已傳入統一現價時，不再自己抓 ticker，暫用最後一根 K 線收盤，稍後覆寫。"""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    errors = []

    try:
        d1 = _fetch_binance("1d", limit_1d, now_ms)
        h4 = _fetch_binance("4h", limit_4h, now_ms)
        w1 = _fetch_binance("1w", limit_1w, now_ms)
        if skip_ticker:
            px = (h4[-1]["close"] if h4 else d1[-1]["close"])
        else:
            try:
                px = _fetch_binance_ticker()
            except Exception:
                px = (h4[-1]["close"] if h4 else d1[-1]["close"])
        return {"source": "binance_spot", "source_note": "幣安現貨 api.binance.com /api/v3/klines",
                "klines": {"4h": h4, "1d": d1, "1w": w1}, "price": px, "errors": errors}
    except Exception as e:
        errors.append(f"binance: {type(e).__name__}: {e}")

    try:
        if _lic("omit_okx", "BTC_OMIT_OKX"): raise RuntimeError("omit_okx")
        d1 = _fetch_okx("1d", limit_1d, now_ms)
        h4 = _fetch_okx("4h", limit_4h, now_ms)
        w1 = _fetch_okx("1w", limit_1w, now_ms)
        px = h4[-1]["close"] if h4 else d1[-1]["close"]
        return {"source": "okx_spot", "source_note": "OKX 現貨 BTC-USDT history-candles（幣安被擋）",
                "klines": {"4h": h4, "1d": d1, "1w": w1}, "price": px, "errors": errors}
    except Exception as e:
        errors.append(f"okx: {type(e).__name__}: {e}")

    try:
        d1 = _fetch_bitstamp("1d", limit_1d, now_ms)
        h4 = _fetch_bitstamp("4h", limit_4h, now_ms)
        w1 = _fetch_bitstamp("1w", min(limit_1w, 1000), now_ms)
        px = h4[-1]["close"] if h4 else d1[-1]["close"]
        return {"source": "bitstamp_spot", "source_note": "Bitstamp 現貨 BTCUSD ohlc（幣安與 OKX 被擋）",
                "klines": {"4h": h4, "1d": d1, "1w": w1}, "price": px, "errors": errors}
    except Exception as e:
        errors.append(f"bitstamp: {type(e).__name__}: {e}")
        raise RuntimeError("所有現貨 K 線來源都失敗：" + " | ".join(errors))


# ---------------------------------------------------------------------------
# 指標
# ---------------------------------------------------------------------------
def _ema(vals: list[float], n: int) -> list[float | None]:
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


def macd_series(closes: list[float]) -> tuple[list, list, list]:
    ef = _ema(closes, MACD_FAST)
    es = _ema(closes, MACD_SLOW)
    line = [(a - b) if a is not None and b is not None else None for a, b in zip(ef, es)]
    start = next((i for i, x in enumerate(line) if x is not None), None)
    if start is None:
        return line, [None] * len(line), [None] * len(line)
    sig_raw = _ema([x for x in line[start:]], MACD_SIGNAL)  # type: ignore
    # _ema 對已切好的序列；對齊回原索引
    signal: list[float | None] = [None] * start + sig_raw
    hist = [(a - b) if a is not None and b is not None else None for a, b in zip(line, signal)]
    return line, signal, hist


def _last_cross(line, signal) -> tuple[str, int] | None:
    ev = None
    for i in range(1, len(line)):
        a0, b0, a1, b1 = line[i - 1], signal[i - 1], line[i], signal[i]
        if None in (a0, b0, a1, b1):
            continue
        if a0 <= b0 and a1 > b1:
            ev = ("金叉", i)
        elif a0 >= b0 and a1 < b1:
            ev = ("死叉", i)
    return ev


def _last_zero(line) -> tuple[str, int] | None:
    ev = None
    for i in range(1, len(line)):
        a0, a1 = line[i - 1], line[i]
        if a0 is None or a1 is None:
            continue
        if a0 >= 0 and a1 < 0:
            ev = ("下穿零軸", i)
        elif a0 <= 0 and a1 > 0:
            ev = ("上穿零軸", i)
    return ev


def _sma(closes: list[float], n: int, end: int | None = None) -> float | None:
    end = len(closes) if end is None else end
    if end < n:
        return None
    return statistics.mean(closes[end - n:end])


def taipei_of(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC).astimezone(TAIPEI)


def fmt_px(x: float, nd: int = 0) -> str:
    if nd == 0:
        return f"{int(round(x)):,}"
    return f"{x:,.{nd}f}"


def fmt_time(dt: datetime) -> str:
    """JSON／內部用，含年份。"""
    return "台北時間 " + dt.strftime("%Y-%m-%d %H:%M")


def fmt_time_text(dt: datetime) -> str:
    """對外文案：台北時間 11/17 08:38，不嵌括號、不含來源。"""
    return f"台北時間 {dt.month}/{dt.day} {dt.strftime('%H:%M')}"


def closed_bars(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("closed")]


def macd_view(rows: list[dict]) -> dict:
    c = closed_bars(rows)
    closes = [r["close"] for r in c]
    line, sig, hist = macd_series(closes)
    if not line or line[-1] is None:
        return {"ok": False}
    amp = max((abs(x) for x in line[-80:] if x is not None), default=1.0)
    frac = abs(line[-1]) / amp if amp else 0.0
    side = "零軸下" if line[-1] < 0 else "零軸上"
    deep = line[-1] < 0 and frac >= MACD_DEEP_FRAC
    near = frac <= MACD_NEAR_ZERO_FRAC
    cross = _last_cross(line, sig)
    zero = _last_zero(line)
    # 快慢線關係
    rel = "快線在慢線上" if line[-1] > (sig[-1] or 0) else "快線在慢線下"
    if deep and line[-1] < 0:
        meaning = "MACD 在零軸下方很深；金叉也只算自然反彈，不能當反轉確認"
        if cross and cross[0] == "金叉":
            meaning = "MACD 在零軸下方很深且已金叉，只算自然反彈，不能當反轉確認"
    elif line[-1] < 0 and cross and cross[0] == "金叉":
        meaning = "零軸下金叉，短線止跌訊號，不是大週期轉多"
    elif near and cross and cross[0] == "金叉" and line[-1] >= 0:
        meaning = "零軸附近金叉，加分"
    elif line[-1] < 0:
        meaning = "在零軸下方"
    else:
        meaning = "在零軸上方"
    last = c[-1]
    out = {
        "ok": True,
        "line": round(line[-1], 2),
        "signal": round(sig[-1], 2) if sig[-1] is not None else None,
        "hist": round(hist[-1], 2) if hist[-1] is not None else None,
        "side": side,
        "relation": rel,
        "deep": deep,
        "near_zero": near,
        "amp_frac": round(frac, 3),
        "meaning": meaning,
        "asof": fmt_time(taipei_of(last["open_time"])),
        "close": last["close"],
    }
    if cross:
        out["last_cross"] = {"type": cross[0], "time": fmt_time(taipei_of(c[cross[1]]["open_time"]))}
    if zero:
        out["last_zero"] = {"type": zero[0], "time": fmt_time(taipei_of(c[zero[1]]["open_time"]))}
    return out


def ma120_view(rows: list[dict]) -> dict:
    c = closed_bars(rows)
    closes = [r["close"] for r in c]
    sma = _sma(closes, SMA_LEN)
    sma_ago = _sma(closes, SMA_LEN, len(closes) - SMA_FLAT_SPAN) if len(closes) >= SMA_LEN + SMA_FLAT_SPAN else None
    if sma is None:
        return {"ok": False, "bars": len(closes)}
    last = c[-1]
    change = (sma - sma_ago) if sma_ago is not None else None
    change_pct = (change / sma) if change is not None else None
    flatish = change_pct is not None and abs(change_pct) <= SMA_FLAT_PCT
    dist_pct = last["close"] / sma - 1.0
    below = last["close"] < sma
    hugging = abs(dist_pct) <= HUG_MA_PCT
    # 重倉合約多：要均線走平、價格貼著震盪後收盤向上突破。價格在下方且未滿足此條件 → 只准現貨或小倉反彈單。
    heavy_ok = (not below) and flatish and hugging
    if below and not (flatish and hugging):
        gate = "價格在 MA120 下方，均線未形成走平後突破，只准現貨或小倉反彈單，不准重倉合約多"
    elif below:
        gate = "價格在 MA120 下方，只准現貨或小倉反彈單，不准重倉合約多"
    elif heavy_ok:
        gate = "收盤在 MA120 上方且均線走平、價格靠近均線，才可考慮重倉合約多"
    else:
        gate = "收盤在 MA120 上方，但仍要看是否走平後突破"
    return {
        "ok": True,
        "sma": round(sma, 1),
        "change_5bars": round(change, 1) if change is not None else None,
        "change_5bars_pct": round(change_pct, 5) if change_pct is not None else None,
        "flatish": flatish,
        "price_below": below,
        "dist_pct": round(dist_pct, 4),
        "hugging": hugging,
        "heavy_long_ok": heavy_ok,
        "gate": gate,
        "asof": fmt_time(taipei_of(last["open_time"])),
        "close": last["close"],
    }


# ---------------------------------------------------------------------------
# 初始劇本（11/17 已確認結論）
# ---------------------------------------------------------------------------
def init_scenarios() -> list[dict]:
    return [
        {
            "id": "mid_wait",
            "name": "區間中段觀望",
            "status": "active",
            "trigger": {"kind": "in_zone", "low": INIT_VAL, "high": INIT_RANGE_HIGH - NEAR_UPPER,
                        "note": "現價在主區間中段"},
            "entry": "不開新單",
            "stop": None,
            "targets": [],
            "invalidation": {"kind": "leave_mid", "levels": [INIT_VAL, INIT_RANGE_HIGH],
                             "note": "進入下沿分批區或上沿減倉區，或收盤脫離主區間"},
            "text": {
                "觸發": "現價位於主區間中段",
                "進場": "不開新單，等待靠近下沿或上沿",
                "停損": "—",
                "目標": "—",
                "失效": "價格進入 61,800 以下分批區、靠近 67,000，或收盤脫離 60,000–67,000",
            },
        },
        {
            "id": "scale_in_low",
            "name": "下沿分批多",
            "status": "pending",
            "trigger": {"kind": "close_or_low_at_or_below", "levels": [INIT_VAL, INIT_FIB_0886, INIT_RANGE_LOW],
                        "zone_low": INIT_RANGE_LOW, "zone_high": INIT_VAL,
                        "note": "靠近 61,800／61,174／60,000"},
            "entry": "分批現貨或小倉多（因 MA120 門檻，不准重倉合約多）",
            "stop": INIT_STOP,
            "targets": [INIT_FIB_0786, INIT_RANGE_HIGH],
            "invalidation": {"kind": "close_below", "level": INIT_STOP, "tf": ["1d"],
                             "note": "日線收盤跌破 57,800"},
            "text": {
                "觸發": "靠近 61,800／61,174／60,000",
                "進場": "分批現貨或小倉多",
                "停損": "57,800",
                "目標": "先看最近密集區 64,134，再看 67,000，不看太遠",
                "失效": "已收完日線收盤跌破 57,800",
            },
        },
        {
            "id": "reduce_high",
            "name": "上沿減倉",
            "status": "pending",
            "trigger": {"kind": "close_or_high_at_or_above", "levels": [INIT_RANGE_HIGH],
                        "zone_low": INIT_RANGE_HIGH - NEAR_UPPER, "zone_high": INIT_RANGE_HIGH,
                        "note": "靠近 67,000"},
            "entry": "減倉或賣，不在中段追多",
            "stop": None,
            "targets": [],
            "invalidation": {"kind": "stand_firm", "zone": INIT_STAND_FIRM,
                             "note": "突破並站穩 67,000–67,500"},
            "text": {
                "觸發": "靠近 67,000",
                "進場": "減倉或賣",
                "停損": "—",
                "目標": "—",
                "失效": "突破並站穩 67,000–67,500，改看轉強劇本",
            },
        },
        {
            "id": "stand_firm_long",
            "name": "站穩上沿確認轉強",
            "status": "pending",
            "trigger": {"kind": "stand_firm", "zone": INIT_STAND_FIRM,
                        "note": "突破並站穩 67,000–67,500 才確認轉強，不用中段小高點"},
            "entry": "在突破框上方整理、實體不跌回才做多；MA120 未走平則只做小倉",
            "stop": "實體跌回框內（67,000 下）",
            "targets": ["最近壓力／密集區，不看太遠"],
            "invalidation": {"kind": "close_below", "level": INIT_RANGE_HIGH, "tf": ["1d"],
                             "note": "日線收盤跌回 67,000 下方"},
            "text": {
                "觸發": "突破並站穩 67,000–67,500",
                "進場": "框上方整理、實體不跌回才做多；只做小倉",
                "停損": "實體跌回框內",
                "目標": "最近壓力／密集區，不看太遠",
                "失效": "實體跌回 67,000 下方；若跌回可等反抽上沿做空，目標只看最近密集區",
            },
        },
        {
            "id": "range_break_short",
            "name": "區間破位或上沿回抽失敗",
            "status": "pending",
            "trigger": {"kind": "failed_retest_or_breakdown",
                        "levels": [INIT_RANGE_HIGH, INIT_RANGE_LOW],
                        "note": "只做長時間區間的破位或回抽失敗，不追單根 K 跌破，不在前低追空"},
            "entry": "等回抽失敗再空，不追單根、不在前低追空",
            "stop": "結構內（回抽高點外）",
            "targets": ["最近密集區，不直接看 57,800"],
            "invalidation": {"kind": "close_back_inside", "level": INIT_RANGE_LOW, "or_level": INIT_RANGE_HIGH,
                             "tf": ["1d", "4h"]},
            "text": {
                "觸發": "主區間破位後的回抽失敗，或靠近 67,000 回抽站不穩",
                "進場": "等回抽失敗再空，不追單根 K 跌破，不在前低追空",
                "停損": "結構內，回抽高點外",
                "目標": "最近密集區",
                "失效": "日線收盤重新站回區間",
            },
        },
    ]


def init_state() -> dict:
    return {
        "version": 1,
        "origin": "2026-11-17 已確認結論",
        "main_range": {
            "low": INIT_RANGE_LOW,
            "high": INIT_RANGE_HIGH,
            "upper_refs": INIT_UPPER_REFS,
            "lower_supports": [INIT_VAL, INIT_FIB_0886, INIT_RANGE_LOW],
            "val": INIT_VAL,
            "fib_0786": INIT_FIB_0786,
            "fib_0886": INIT_FIB_0886,
            "final_stop": INIT_STOP,
            "stand_firm_zone": INIT_STAND_FIRM,
            "rally_low": INIT_RALLY_LOW,
            "rally_high": INIT_RALLY_HIGH,
            "note": "上沿取急跌後第一個清楚停頓（67,292／67,568）再落到整數 67,000；下沿取整數關 60,000；63,905–65,696 只是中段小結構，不能取代主區間",
        },
        "scenarios": init_scenarios(),
        "last_recompute_reason": None,
        "last_recompute_bar": INIT_RECOMPUTE_BAR,
        "overhead_levels": [],
    }


# ---------------------------------------------------------------------------
# 檢查邏輯
# ---------------------------------------------------------------------------
def bar_date(r: dict) -> str:
    """日線的台北日期（開盤日），用來記觸發重算的那一根。"""
    return taipei_of(r["open_time"]).strftime("%Y-%m-%d")


def close_streak(closed_1d: list[dict], pred) -> dict:
    """最新已收日線往回數，連續符合 pred 的那一段。
    start＝這段的第一根；prev_close＝這段之前那一根的收盤（沒有就是 None）。"""
    n = len(closed_1d)
    i = n
    while i > 0 and pred(closed_1d[i - 1]["close"]):
        i -= 1
    if i == n:
        return {"on": False, "start": None, "len": 0, "prev_close": None}
    return {
        "on": True,
        "start": bar_date(closed_1d[i]),
        "len": n - i,
        "prev_close": closed_1d[i - 1]["close"] if i > 0 else None,
    }


def crossed_after(streak: dict, birth: str | None, strict: bool) -> bool:
    """這段是不是在框成立之後才從另一側收過來。
    strict＝True（重算用）：第一根必須晚於觸發上次重算的那根，同一根不能再用。
    strict＝False（判斷站穩用）：允許就是觸發重算的那根，但前一根必須在另一側。"""
    if not streak["on"] or streak["prev_close"] is None:
        return False
    if birth is None:
        return True
    return streak["start"] > birth if strict else streak["start"] >= birth


def resolve_recompute_bar(state: dict, closed_1d: list[dict]) -> str | None:
    """觸發上次重算的那根日線。舊狀態檔沒記：用重算原因裡的收盤價對回那一根，
    對不到就取狀態檔更新時已收完的最後一根；從沒重算過就是 11/17 結論的那根。"""
    v = state.get("last_recompute_bar")
    if v:
        return v
    reason = state.get("last_recompute_reason")
    if not reason:
        return INIT_RECOMPUTE_BAR
    upd_ms = None
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", str(state.get("updated_at") or ""))
    if m:
        upd_ms = int(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=TAIPEI).timestamp() * 1000)
    cand = [r for r in closed_1d if upd_ms is None or r["close_time"] <= upd_ms]
    m = re.search(r"日線收盤\s*([\d,]+(?:\.\d+)?)", str(reason))
    if m:
        px = float(m.group(1).replace(",", ""))
        hit = [r for r in cand if abs(r["close"] - px) < 0.05]
        if hit:
            return bar_date(hit[-1])
    if cand:
        return bar_date(cand[-1])
    return None


def resolve_overhead(state: dict) -> list[float]:
    """跌破後變成壓力的原主區間下緣。舊狀態檔沒記：從重算原因「低於主區間下沿 X」補。"""
    v = state.get("overhead_levels")
    if v is not None:
        return sorted(float(x) for x in v)
    m = re.search(r"低於主區間下沿\s*([\d,]+)", str(state.get("last_recompute_reason") or ""))
    return [float(m.group(1).replace(",", ""))] if m else []


def pressure_view(rng: dict, price: float, overhead: list[float], sf: dict) -> dict:
    """「先減倉」指的是哪個價位。
    edge：現價還在上沿下方，壓力就是上沿。
    overhead：現價已在上沿上方，上方還有跌破後變成壓力的原主區間下緣，壓力就是它。
    above_edge：現價在上沿上方，上方沒有記下的壓力。
    need_reclaim：最新日線是從上方跌下來收在框上，框不能再拿來站穩，要日線收回上方壓力才算轉強。"""
    hi = rng["high"]
    former = bool(rng.get("high_former_low"))
    above = [x for x in overhead if x > max(price, hi)]
    zone = rng.get("stand_firm_zone") or [None, None]
    ac = bool(zone[1] is not None and price > zone[1])
    if price <= hi:
        return {"kind": "edge", "level": hi, "former_low": former, "need_reclaim": False, "above_confirm": ac}
    if above:
        return {"kind": "overhead", "level": min(above), "former_low": True,
                "need_reclaim": bool(sf.get("came_from_above")), "above_confirm": ac}
    return {"kind": "above_edge", "level": hi, "former_low": former, "need_reclaim": False, "above_confirm": ac}


def known_levels(rng: dict, check: dict | None = None) -> list[float]:
    """模型自己寫出的價位：主區間上下沿、站穩確認帶、分批支撐、往上重算時被突破的框、跌破後記下的上方壓力。
    停損不算支撐；只供重算用的上沿參考價不算已知壓力。"""
    vals: set[float] = set()
    for x in ([rng.get("low"), rng.get("high")] + list(rng.get("stand_firm_zone") or [])
              + list(rng.get("lower_supports") or []) + list(rng.get("broken_zone") or [])
              + list((check or {}).get("overhead_levels") or [])):
        if x is not None:
            vals.add(float(round(float(x))))
    return sorted(vals)


def support_pressure_view(rng: dict, check: dict | None, price: float) -> dict:
    """現價下方（含等於）的已知價位全部是支撐，由近到遠；壓力只取現價上方最近的一檔，上方沒有就不列。"""
    lv = known_levels(rng, check)
    sup = sorted((x for x in lv if x <= price), reverse=True)
    above = [x for x in lv if x > price]
    return {"supports": sup, "pressure": min(above) if above else None}


def support_pressure_text(sp: dict) -> str:
    parts = []
    if sp.get("supports"):
        parts.append("支撐 " + "、".join(fmt_px(x) for x in sp["supports"]))
    if sp.get("pressure") is not None:
        parts.append(f"壓力 {fmt_px(sp['pressure'])}")
    return "｜".join(parts)


def support_pressure_errors(sp: dict, price: float, text: str) -> list[str]:
    """自檢：支撐都在現價下方、壓力在現價上方而且只有一檔，同一個數字不重複。"""
    errs = []
    if any(x > price for x in sp.get("supports") or []):
        errs.append("support_pressure：支撐裡有現價上方的價位")
    p = sp.get("pressure")
    if p is not None and p <= price:
        errs.append("support_pressure：壓力不在現價上方")
    nums = re.findall(r"\d[\d,]*", text)
    if len(nums) != len(set(nums)):
        errs.append("support_pressure：同一個數字重複")
    if text.count("壓力") > 1:
        errs.append("support_pressure：壓力不只一段")
    return errs


def stand_firm(closed_1d: list[dict], closed_4h: list[dict], zone: list[float], birth: str | None = None) -> dict:
    """站穩判定：只用已收完日線收盤，4h 不能當站穩。影線穿過與未收完的 K 不算。

    至少一根已收完日線收盤 >= 上沿（67,000），且最新已收完日線仍未收在上沿下方。
    收盤落在 67,000–67,500 或更高都算站穩。
    """
    lo, hi = zone[0], zone[1]
    # 結論只跟最新一根已收完日線走：它收在上沿以上才算站穩；收回上沿下方就不算，
    # 不再因為更早某一根曾收上去就沿用。4h 收盤與現價只算測試，另由 hold_side 標記。
    daily_hit = any(r["close"] >= lo for r in closed_1d) if closed_1d else False
    last = closed_1d[-1] if closed_1d else None
    daily_above = bool(last and last["close"] >= lo)
    # 站穩必須由下往上收上去：連續收在框下緣以上的這段，要在框成立之後才開始，
    # 而且前一根收在框下緣下方。從上方跌下來、收在新框上方的那根，不算站上。
    streak = close_streak(closed_1d, lambda c: c >= lo)
    from_below = daily_above and crossed_after(streak, birth, strict=False)
    daily_held = bool(daily_above and from_below)
    return {
        "ok": daily_held,
        "daily_close_above": daily_above,
        "from_below": from_below,
        "came_from_above": bool(daily_above and not from_below),
        "streak_start": streak["start"],
        "can_recompute": bool(daily_held and crossed_after(streak, birth, strict=True)),
        "daily_had_close_above": daily_hit,
        "last_close": last["close"] if last else None,
        "last_close_time": fmt_time(taipei_of(last["open_time"])) if last else None,
        "zone": zone,
        "rule": "只看已收完日線：最新日線收在上沿以上，而且是在框成立後由下往上收上去；從上方跌下來收在框上不算。4h 不能當站穩。影線穿過不算，未收完的 K 不算。",
    }


def _last_closed(rows: list[dict]) -> dict | None:
    c = closed_bars(rows)
    return c[-1] if c else None


def position_in_range(price: float, rng: dict) -> str:
    lo, hi = rng["low"], rng["high"]
    val = rng.get("val") or rng.get("fib_0886") or lo
    if price < lo:
        return "below"
    if price > hi:
        return "above"
    if price <= val:
        return "lower_zone"
    if price >= hi - NEAR_UPPER:
        return "upper_zone"
    return "mid"


def check_state(state: dict, market: dict) -> dict:
    rng = state["main_range"]
    lo, hi = rng["low"], rng["high"]
    stop = rng["final_stop"]
    zone = rng["stand_firm_zone"]
    h4 = market["klines"]["4h"]
    d1 = market["klines"]["1d"]
    last_4h = _last_closed(h4)
    last_1d = _last_closed(d1)
    c4 = last_4h["close"] if last_4h else None
    c1 = last_1d["close"] if last_1d else None
    cd1 = closed_bars(d1)
    birth = resolve_recompute_bar(state, cd1)
    sf = stand_firm(cd1, closed_bars(h4), zone, birth)

    # 站穩後現價與 4h 的位置：只算測試，不改日線結論，但文字要跟著現價走
    price_now = market["price"]
    if sf["ok"]:
        if price_now >= zone[1]:
            sf["hold_side"] = "above"
        elif price_now >= zone[0]:
            sf["hold_side"] = "in_box"
        else:
            sf["hold_side"] = "lost"
        sf["fourh_back_below"] = bool(c4 is not None and c4 < zone[0])
    else:
        sf["hold_side"] = None
        sf["fourh_back_below"] = False

    events = []
    departed = False
    guard = []  # 收在區間外但不重算的原因（同一根已用過、從反方向收過來）
    def _fresh(pred) -> bool:
        return crossed_after(close_streak(cd1, pred), birth, strict=True)
    # 脫離／重算只看已收完日線（R27：要撐過幾天才算）。4h 只標記試探。
    # 同一根日線不能重複用來重算；收在區間外的那一段要在框成立之後才從另一側收過來。
    if c1 is not None and c1 < lo:
        if _fresh(lambda c: c < lo):
            departed = True
            events.append({"type": "depart_below", "tf": "1d", "close": c1, "level": lo,
                           "reason": f"日線收盤 {fmt_px(c1, 1)} 低於主區間下沿 {fmt_px(lo)}"})
        else:
            guard.append(f"日線收盤 {fmt_px(c1, 1)} 在下沿下方，但不是框成立後才跌破的新一根，不重算")
    if c1 is not None and c1 > hi:
        if _fresh(lambda c: c > hi):
            departed = True
            events.append({"type": "depart_above", "tf": "1d", "close": c1, "level": hi,
                           "reason": f"日線收盤 {fmt_px(c1, 1)} 高於主區間上沿 {fmt_px(hi)}"})
        elif not sf.get("came_from_above"):
            guard.append(f"日線收盤 {fmt_px(c1, 1)} 在上沿上方，但這根就是觸發上次重算的那根，不再重算")
    if c1 is not None and c1 < stop:
        if _fresh(lambda c: c < stop):
            departed = True
            events.append({"type": "stop_break", "tf": "1d", "close": c1, "level": stop,
                           "reason": f"日線收盤 {fmt_px(c1, 1)} 跌破最終停損 {fmt_px(stop)}"})
        else:
            guard.append(f"日線收盤 {fmt_px(c1, 1)} 在停損下方，但不是框成立後才跌破的新一根，不重算")
    overhead = resolve_overhead(state)
    if c1 is not None and c1 > hi and not departed:
        reclaimed = [x for x in overhead if c1 > x and _fresh(lambda c, x=x: c > x)]
        if reclaimed:
            lvl = max(reclaimed)
            departed = True
            events.append({"type": "reclaim_overhead", "tf": "1d", "close": c1, "level": lvl,
                           "reason": f"日線收盤 {fmt_px(c1, 1)} 由下往上收回上方壓力 {fmt_px(lvl)}，主區間往上重算"})
    if sf["ok"] and sf.get("can_recompute"):
        departed = True
        events.append({"type": "stand_firm", "zone": zone,
                       "reason": f"日線已站穩 {fmt_px(zone[0])}–{fmt_px(zone[1])}，確認轉強，主區間需重算"})
    elif sf["ok"]:
        guard.append("站穩的那根就是觸發上次重算的那根，不再重算")
    elif sf.get("came_from_above") and not any(e["type"] == "reclaim_overhead" for e in events):
        guard.append(f"日線收盤 {fmt_px(c1, 1)} 在框上，但框成立時價格就已經在框上方，是從上方跌下來，不是由下往上收上去，不算站上，不重算")

    # 4h 試探：單根或連續兩根 4h 收盤越出區間，日線仍在區間內時只標記、不重算
    fourh_probe = None
    fourh_consecutive = 0
    daily_inside = c1 is not None and lo <= c1 <= hi
    if daily_inside and c4 is not None:
        if c4 > hi:
            fourh_probe = "upper"
        elif c4 < lo:
            fourh_probe = "lower"
        if fourh_probe:
            closed_4 = closed_bars(h4)
            for r in reversed(closed_4):
                if fourh_probe == "upper" and r["close"] > hi:
                    fourh_consecutive += 1
                elif fourh_probe == "lower" and r["close"] < lo:
                    fourh_consecutive += 1
                else:
                    break

    # 區間內觸發（不重算主區間，只標記）
    price = market["price"]
    raw_pos = position_in_range(price, rng)
    # 日線未脫離時，現價或 4h 越出只當作上沿／下沿區，不當作結構脫離
    if not departed and raw_pos == "above":
        pos = "upper_zone"
    elif not departed and raw_pos == "below":
        pos = "lower_zone"
    elif departed and c1 is not None and c1 < lo:
        pos = "below"
    elif departed and c1 is not None and c1 > hi:
        pos = "above"
    else:
        pos = raw_pos
    # 也用已收完 4h／日線的高低判斷是否碰到觸發帶
    intra = []
    def _touched_low(level):
        for name, bar in (("1d", last_1d), ("4h", last_4h)):
            if bar and (bar["close"] <= level or bar["low"] <= level):
                return name, bar
        return None
    def _touched_high(level):
        for name, bar in (("1d", last_1d), ("4h", last_4h)):
            if bar and (bar["close"] >= level or bar["high"] >= level):
                return name, bar
        return None

    if fourh_probe:
        label = "上沿" if fourh_probe == "upper" else "下沿"
        note = f"4h 試探{label}"
        if fourh_consecutive >= 2:
            note += f"（連續 {fourh_consecutive} 根收盤越出，仍不重算）"
        intra.append({"type": "fourh_probe", "side": fourh_probe,
                      "consecutive": fourh_consecutive, "close": c4, "note": note})
    if pos == "lower_zone" or (last_4h and last_4h["low"] <= (rng.get("val") or rng["fib_0886"])) or (last_1d and last_1d["low"] <= (rng.get("val") or rng["fib_0886"])):
        intra.append({"type": "scale_in_zone", "levels": rng["lower_supports"],
                      "note": "已進入或觸及下沿分批區，沿用原劇本，不重算主區間"})
    if pos == "upper_zone" or (last_4h and last_4h["high"] >= hi - NEAR_UPPER) or (last_1d and last_1d["high"] >= hi - NEAR_UPPER):
        if not sf["ok"]:
            intra.append({"type": "reduce_zone", "level": hi,
                          "note": "已靠近上沿減倉區但尚未站穩，沿用原劇本，不重算主區間"})

    distances = {
        "to_val": round(price - rng["val"], 1),
        "to_fib_0886": round(price - rng["fib_0886"], 1),
        "to_range_low": round(price - lo, 1),
        "to_range_high": round(hi - price, 1),
        "to_stand_firm": round(zone[0] - price, 1),
        "to_stop": round(price - stop, 1),
    }
    return {
        "departed": departed,
        "recompute": departed,
        "recompute_bar": birth,
        "recompute_guard": guard,
        "overhead_levels": overhead,
        "pressure": pressure_view(rng, market["price"], overhead, sf),
        "events": events,
        "intra_triggers": intra,
        "fourh_probe": fourh_probe,
        "fourh_consecutive": fourh_consecutive,
        "stand_firm": sf,
        "position": pos,
        "price": price,
        "closed_1d": {
            "time": fmt_time(taipei_of(last_1d["open_time"])) if last_1d else None,
            "open": last_1d["open"] if last_1d else None,
            "high": last_1d["high"] if last_1d else None,
            "low": last_1d["low"] if last_1d else None,
            "close": c1,
        },
        "closed_4h": {
            "time": fmt_time(taipei_of(last_4h["open_time"])) if last_4h else None,
            "open": last_4h["open"] if last_4h else None,
            "high": last_4h["high"] if last_4h else None,
            "low": last_4h["low"] if last_4h else None,
            "close": c4,
        },
        "distances": distances,
        "reason_text": (
            "；".join(e["reason"] for e in events) + (
                "；現價跌回框下，只算回測、框上沒守住，要等日線收盤再改判"
                if sf.get("hold_side") == "lost" else
                "；現價回到框裡回測，日線結論不變" if sf.get("hold_side") == "in_box" else "")
            if events else (
                f"日線未脫離主區間；{next((x['note'] for x in intra if x.get('type')=='fourh_probe'), '')}，沿用現有劇本"
                if fourh_probe else
                "未脫離主區間，也未打到失效價或站穩確認，沿用現有劇本"
            ) + ("；" + "；".join(guard) if guard else "")
        ),
    }


# ---------------------------------------------------------------------------
# 脫離後的重算（今日試跑預期不會走到；預留給之後自動更新主區間）
# ---------------------------------------------------------------------------
def _swings(rows: list[dict], left: int = SWING_LEFT, right: int = SWING_RIGHT) -> list[tuple[str, dict]]:
    out = []
    n = len(rows)
    for i in range(left, n - right):
        window = rows[i - left:i + right + 1]
        if rows[i]["high"] == max(x["high"] for x in window):
            out.append(("H", rows[i]))
        if rows[i]["low"] == min(x["low"] for x in window):
            out.append(("L", rows[i]))
    return out


def recompute_range(market: dict, depart_reason: str) -> dict:
    """結構脫離後依固定流程重算主區間。小結構不得取代主區間。

    完整主升＝近段日線最低點起、其後最高點止（起點是那段主升的絕對低點）。
    急跌＝主升高點之後、最近低點之前，最近一次「數根日線內大幅下跌」。
    上沿＝這段急跌後第一個清楚的 4h 停頓，再落到整數；下沿＝整數關。
    """
    d1 = closed_bars(market["klines"]["1d"])
    h4 = closed_bars(market["klines"]["4h"])
    if len(d1) < 30:
        raise RuntimeError("日線樣本不足，無法重算主區間")

    # 最近一段完整主升：樣本最低點 → 其後最高點
    lb = min(d1, key=lambda r: r["low"])
    after_low = [r for r in d1 if r["open_time"] >= lb["open_time"]]
    hb = max(after_low, key=lambda r: r["high"])
    after_high = [r for r in d1 if r["open_time"] > hb["open_time"]]
    if not after_high:
        # 主升剛創高、還沒回撤，暫用現價附近結構，避免空窗
        post_low = hb["low"]
        retr = 0.0
    else:
        post_bar = min(after_high, key=lambda r: r["low"])
        post_low = post_bar["low"]
        retr = (hb["high"] - post_low) / hb["high"] if hb["high"] else 0.0

    rally_low, rally_high = lb["low"], hb["high"]
    fib_0886 = rally_high - FIB_0886 * (rally_high - rally_low)
    fib_0786 = rally_high - FIB_0786 * (rally_high - rally_low)
    final_stop = round(rally_low, 0)

    # 最近急跌窗口：以最近擺動低點為終點，往前最多 8 根日線取起跌高點。
    # 必須涵蓋急跌「開頭」，不能只取最後兩天，否則會把中段小低點當成上沿。
    end_bar = after_high and post_bar or d1[-1]
    end_idx = next(i for i, r in enumerate(d1) if r["open_time"] == end_bar["open_time"])
    lookback = 8
    start_idx = max(0, end_idx - lookback + 1)
    # 起跌高點：窗口內最高根
    peak_idx = start_idx + max(range(end_idx - start_idx + 1), key=lambda k: d1[start_idx + k]["high"])
    drop_days = d1[peak_idx:end_idx + 1]
    peak = max(r["high"] for r in drop_days) if drop_days else 0
    trough = end_bar["low"]
    pct = (peak - trough) / peak if peak else 0
    sharp = None
    if drop_days and pct >= 0.08:
        sharp = (0, len(drop_days) - 1, pct, drop_days[0]["open_time"], drop_days[-1]["close_time"])

    pause_refs: list[dict] = []
    core_prices: list[float] = []  # 用來落到整數的停頓價（日線低點、4h 實體／收盤，不用影線）
    if sharp and drop_days:
        # 以窗口內「單日跌幅最大」那天為急跌日，並含前一天（停頓常出現在大陰前的最後低點）
        def _day_drop(r):
            return (r["high"] - r["low"]) / r["high"] if r["high"] else 0
        worst = max(drop_days, key=_day_drop)
        wi = next(i for i, r in enumerate(drop_days) if r["open_time"] == worst["open_time"])
        shelf_days = drop_days[max(0, wi - 1):wi + 1]
        # 日線停頓低點：明顯高於最終低點的那幾根（例如週五低點 67,568）。
        # 上沿整數只由這些日線低點決定，避免 4h 影線或大陰收盤把上沿多削一檔。
        for r in shelf_days:
            if r["low"] > post_low * 1.06:
                core_prices.append(r["low"])
                pause_refs.append({
                    "low": r["low"], "high": r["high"],
                    "time": fmt_time(taipei_of(r["open_time"])) + " 日線停頓低點",
                })
        start_ms = shelf_days[0]["open_time"]
        end_ms = shelf_days[-1]["close_time"]
        window_4h = [r for r in h4 if start_ms <= r["open_time"] <= end_ms]
        i = 0
        while i < len(window_4h) - 1:
            start = window_4h[i]
            j = i + 1
            while j < len(window_4h) and (start["high"] - window_4h[j]["low"]) / start["high"] < SHARP_LEG_PCT:
                j += 1
            if j >= len(window_4h):
                break
            leg_end = window_4h[j]
            drop_pct = (start["high"] - leg_end["low"]) / start["high"]
            if drop_pct >= SHARP_LEG_PCT:
                cluster = [leg_end]
                k = j + 1
                while k < len(window_4h):
                    r = window_4h[k]
                    if r["low"] < leg_end["low"] * 0.98:
                        break
                    cluster.append(r)
                    if len(cluster) >= PAUSE_MIN_4H + 8:
                        break
                    k += 1
                if len(cluster) >= PAUSE_MIN_4H:
                    closes = [r["close"] for r in cluster]
                    pause_refs.append({
                        "low": min(closes),
                        "high": max(r["high"] for r in cluster),
                        "time": fmt_time(taipei_of(cluster[0]["open_time"])),
                    })
                    # 若沒有合格的日線停頓低點，才退用整理段收盤來落到整數
                    if not core_prices:
                        core_prices.extend(closes)
                    break
            i = j if j > i else i + 1

    sw = _swings(d1)
    upper_refs = []
    if core_prices:
        core = min(core_prices)
        upper_refs = [round(x, 0) for x in core_prices]
        # 靠近停頓價的前高只作註記，不參與「落到整數」，以免略低的舊高把上沿多削一檔
        for typ, bar in sw:
            if typ == "H" and abs(bar["high"] - core) / core <= 0.012:
                upper_refs.append(round(bar["high"], 0))
        raw_upper = min(x for x in [round(x, 0) for x in core_prices] if x > post_low)
        range_high = float(math.floor(raw_upper / UPPER_ROUND) * UPPER_ROUND)
    else:
        range_high = float(math.floor(post_low * 1.08 / UPPER_ROUND) * UPPER_ROUND)
        upper_refs = [range_high]

    step = 10000 if post_low >= 50000 else INTEGER_STEP
    cand = math.floor(post_low / step) * step
    if post_low - cand < post_low * 0.015:
        cand -= step
    range_low = float(cand)

    stand = [range_high, range_high + STAND_FIRM_BAND]
    val_est = round(fib_0886, 0)
    return {
        "low": float(range_low),
        "high": float(range_high),
        "upper_refs": sorted(set(upper_refs)),
        "lower_supports": list(dict.fromkeys([val_est, round(fib_0886, 0), float(range_low)])),
        "val": val_est,
        "val_estimated": True,
        "fib_0786": round(fib_0786, 1),
        "fib_0886": round(fib_0886, 1),
        "final_stop": float(final_stop),
        "stand_firm_zone": stand,
        "rally_low": round(rally_low, 1),
        "rally_high": round(rally_high, 1),
        "retracement": round(retr, 4),
        "pause": pause_refs[0] if pause_refs else None,
        "note": f"因「{depart_reason}」依流程重算：上沿取急跌後第一個清楚停頓再落到整數，下沿取整數關，0.886＝{fmt_px(fib_0886, 1)}",
    }


def recompute_range_up(market: dict, old_rng: dict, support: list[float], overhead: list[float], depart_reason: str) -> dict:
    """往上突破後重算：被突破的框轉成支撐，新主區間下沿＝框下緣、分批看框上緣；
    上沿取突破收盤與框上緣以上最近的壓力：跌破後記下的原主區間下緣、原上沿參考價、近 10 根日線高點、日線擺動高點，
    都落到整數；都沒有才取收盤上方約 3% 的整數。新框一定在被突破的框上方。"""
    d1 = closed_bars(market["klines"]["1d"])
    if not d1:
        raise RuntimeError("沒有已收完日線，無法往上重算")
    last = d1[-1]
    s0, s1 = float(support[0]), float(support[1])
    base = max(last["close"], s1)
    cands = {float(x) for x in overhead if x > base}
    for x in (old_rng.get("upper_refs") or []):
        f = float(math.floor(x / UPPER_ROUND) * UPPER_ROUND)
        if f > base:
            cands.add(f)
    for bar in d1[-10:]:  # 近段日線高點：上方最近的密集區
        f = float(math.floor(bar["high"] / UPPER_ROUND) * UPPER_ROUND)
        if f > base:
            cands.add(f)
    for typ, bar in _swings(d1[-120:]):
        if typ == "H":
            f = float(math.floor(bar["high"] / UPPER_ROUND) * UPPER_ROUND)
            if f > base:
                cands.add(f)
    if not cands:
        cands.add(float(math.ceil(base * 1.03 / UPPER_ROUND) * UPPER_ROUND))
    hi = min(cands)
    stop = float(s0 - STAND_FIRM_BAND)  # 實體跌回被突破的框下方就認錯，停損放在框下緣再下去一個確認帶寬
    if not (s0 < hi and hi > s1 and stop < s0):
        raise RuntimeError("往上重算結果不合理")
    return {
        "low": s0,
        "high": hi,
        "upper_refs": sorted(cands),
        "lower_supports": [s1, s0],
        "val": s1,
        "val_estimated": False,
        "fib_0786": s1,
        "fib_0886": s1,
        "final_stop": stop,
        "stand_firm_zone": [hi, hi + STAND_FIRM_BAND],
        "pause": None,
        "direction": "up",
        "broken_zone": [s0, s1],
        "high_former_low": hi in {float(x) for x in overhead},
        "note": f"因「{depart_reason}」往上重算：被突破的 {fmt_px(s0)}–{fmt_px(s1)} 轉成支撐，上沿取上方最近壓力 {fmt_px(hi)}",
    }


def scenarios_from_range(rng: dict) -> list[dict]:
    lo, hi = rng["low"], rng["high"]
    stop = rng["final_stop"]
    fib886 = rng["fib_0886"]
    fib786 = rng["fib_0786"]
    val = rng.get("val") or fib886
    zone = rng["stand_firm_zone"]
    lower_levels = [x for x in [val, fib886, lo] if x is not None]
    return [
        {
            "id": "mid_wait",
            "name": "區間中段觀望",
            "status": "pending",
            "trigger": {"kind": "in_zone", "low": val, "high": hi - NEAR_UPPER},
            "entry": "不開新單",
            "stop": None,
            "targets": [],
            "invalidation": {"kind": "leave_mid", "levels": [val, hi]},
            "text": {
                "觸發": f"現價位於主區間 {fmt_px(lo)}–{fmt_px(hi)} 中段",
                "進場": "不開新單",
                "停損": "—",
                "目標": "—",
                "失效": "進入下沿分批區、靠近上沿，或收盤脫離主區間",
            },
        },
        {
            "id": "scale_in_low",
            "name": "下沿分批多",
            "status": "pending",
            "trigger": {"kind": "close_or_low_at_or_below", "levels": lower_levels,
                        "zone_low": lo, "zone_high": val},
            "entry": "分批現貨或小倉多",
            "stop": stop,
            "targets": [fib786, hi],
            "invalidation": {"kind": "close_below", "level": stop, "tf": ["1d"]},
            "text": {
                "觸發": "靠近 " + "／".join(fmt_px(x) for x in lower_levels),
                "進場": "分批現貨或小倉多",
                "停損": fmt_px(stop),
                "目標": f"先看最近密集區 {fmt_px(fib786, 1)}，再看 {fmt_px(hi)}",
                "失效": f"已收完日線收盤跌破 {fmt_px(stop)}",
            },
        },
        {
            "id": "reduce_high",
            "name": "上沿減倉",
            "status": "pending",
            "trigger": {"kind": "close_or_high_at_or_above", "levels": [hi],
                        "zone_low": hi - NEAR_UPPER, "zone_high": hi},
            "entry": "減倉或賣",
            "stop": None,
            "targets": [],
            "invalidation": {"kind": "stand_firm", "zone": zone},
            "text": {
                "觸發": f"靠近 {fmt_px(hi)}",
                "進場": "減倉或賣",
                "停損": "—",
                "目標": "—",
                "失效": f"突破並站穩 {fmt_px(zone[0])}–{fmt_px(zone[1])}",
            },
        },
        {
            "id": "stand_firm_long",
            "name": "站穩上沿確認轉強",
            "status": "pending",
            "trigger": {"kind": "stand_firm", "zone": zone},
            "entry": "框上方整理、實體不跌回才做多；MA120 未走平則只做小倉",
            "stop": "實體跌回框內",
            "targets": ["最近壓力／密集區"],
            "invalidation": {"kind": "close_below", "level": hi, "tf": ["1d"]},
            "text": {
                "觸發": f"突破並站穩 {fmt_px(zone[0])}–{fmt_px(zone[1])}",
                "進場": "框上方整理、實體不跌回才做多；只做小倉",
                "停損": "實體跌回框內",
                "目標": "最近壓力／密集區，不看太遠",
                "失效": f"實體跌回 {fmt_px(hi)} 下方",
            },
        },
        {
            "id": "range_break_short",
            "name": "區間破位或上沿回抽失敗",
            "status": "pending",
            "trigger": {"kind": "failed_retest_or_breakdown", "levels": [hi, lo]},
            "entry": "等回抽失敗再空，不追單根、不在前低追空",
            "stop": "結構內",
            "targets": ["最近密集區"],
            "invalidation": {"kind": "close_back_inside", "level": lo, "or_level": hi, "tf": ["1d", "4h"]},
            "text": {
                "觸發": "主區間破位後的回抽失敗，或上沿回抽站不穩",
                "進場": "等回抽失敗再空，不追單根 K 跌破，不在前低追空",
                "停損": "結構內，回抽高點外",
                "目標": "最近密集區",
                "失效": "日線收盤重新站回區間",
            },
        },
    ]


def update_scenario_status(scenarios: list[dict], check: dict) -> list[dict]:
    pos = check["position"]
    out = []
    for s in scenarios:
        t = dict(s)
        sid = t["id"]
        if sid == "mid_wait":
            t["status"] = "active" if pos == "mid" else "inactive"
        elif sid == "scale_in_low":
            t["status"] = "active" if pos == "lower_zone" else "pending"
        elif sid == "reduce_high":
            t["status"] = "active" if pos == "upper_zone" and not check["stand_firm"]["ok"] else "pending"
        elif sid == "stand_firm_long":
            side = check["stand_firm"].get("hold_side")
            if not check["stand_firm"]["ok"]:
                t["status"] = "pending"
            elif side == "lost":
                t["status"] = "retest_lost"
            elif side == "in_box":
                t["status"] = "retest"
            else:
                t["status"] = "active"
        elif sid == "range_break_short":
            t["status"] = "active" if pos == "below" else "pending"
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# 文案
# ---------------------------------------------------------------------------
def macd_sentence(macd_d: dict, macd_4: dict | None = None) -> str:
    """日線與 4h MACD 合成一句，不寫來源，不用「深處水下」。"""
    if not macd_d or not macd_d.get("ok"):
        return ""
    deep = macd_d.get("deep")
    line = macd_d.get("line") or 0
    d_cross = (macd_d.get("last_cross") or {}).get("type")
    if deep:
        head = "日線 MACD 在零軸下方很深"
    elif line < 0:
        head = "日線 MACD 在零軸下方"
    else:
        head = "日線 MACD 在零軸上方"
    d_gold = d_cross == "金叉"
    f = macd_4 if macd_4 and macd_4.get("ok") else None
    f_gold = bool(f and (f.get("last_cross") or {}).get("type") == "金叉")
    f_under = bool(f and (f.get("line") or 0) < 0)
    if line < 0 and not d_gold and f_gold and f_under:
        return f"{head}，尚未金叉；4h 已在零軸下金叉，都只算自然反彈，不能當反轉確認。"
    if line < 0 and d_gold:
        return f"{head}且已金叉，只算自然反彈，不能當反轉確認。"
    if line < 0:
        return f"{head}，尚未金叉。"
    if d_gold:
        return f"{head}，已金叉。"
    return f"{head}。"



def _strip_datetimes(text: str) -> str:
    """拿掉日期時間，避免把 11/17 09:23 或 2026.11.17 當成價位數字。"""
    text = re.sub(r"台北時間\s+\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}", " ", text)
    text = re.sub(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?", " ", text)
    text = re.sub(r"\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    return text


def collect_numbers(text: str) -> dict[str, int]:
    """正規化後的數字 → 出現次數（60,000 與 60000 視為同一個）。"""
    counts: dict[str, int] = {}
    for m in re.finditer(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+", _strip_datetimes(text)):
        key = m.group(0).replace(",", "")
        if "." in key:
            key = key.rstrip("0").rstrip(".")
        if re.fullmatch(r"\d+", key):
            key = str(int(key))
        counts[key] = counts.get(key, 0) + 1
    return counts


def duplicate_numbers(text: str) -> list[tuple[str, int]]:
    counts = collect_numbers(text)
    return sorted(((k, n) for k, n in counts.items() if n > 1), key=lambda x: (-x[1], x[0]))


def format_number_key(key: str) -> str:
    if re.fullmatch(r"\d+", key) and len(key) >= 4:
        return f"{int(key):,}"
    return key


def unique_number_errors(*parts: tuple[str, str]) -> list[str]:
    """檢查多段文字，各段內同一個數字只能出現一次。"""
    errs = []
    for label, text in parts:
        dups = duplicate_numbers(text)
        if dups:
            detail = "、".join(f"{format_number_key(k)} 出現 {n} 次" for k, n in dups)
            errs.append(f"{label} 數字重複：{detail}")
    return errs


def text_pressure_errors(sp: dict, *texts: str) -> list[str]:
    """短線文字、關鍵價位裡寫成「壓力」的數字，必須是現價上方那一檔。"""
    errs = []
    want = fmt_px(sp["pressure"]) if sp.get("pressure") is not None else None
    for t in texts:
        for m in re.finditer(r"壓力\s*(\d[\d,]*)", t):
            if m.group(1) != want:
                errs.append(f"文字把 {m.group(1)} 寫成壓力，但它不是現價上方最近的已知價位")
    return errs


def bias_line(check: dict, rng: dict, ma_d: dict) -> tuple[str, str, str]:
    """回傳 (方向, 目標句, 失效)，供 levels／摘要使用，不輸出成摘要行。"""
    st = voice_state(check, rng)
    stop = fmt_px(rng["final_stop"]) if rng and rng.get("final_stop") is not None else ""
    invalid = f"日線收盤跌破停損{(' ' + stop) if stop else ''}" if st != "invalid" else "這套已作廢，要重算"
    if st == "stand_firm":
        return ("偏多", "已經突破並站上框，實體還在框上面就做多", "實體跌回框裡就認錯停損")
    if st == "stand_firm_retest":
        return ("偏多", "現價回到框裡回測，守住才繼續偏多，先不追", "實體跌回框下就認錯停損")
    if st == "stand_firm_lost":
        return ("觀望", "現價跌回框下，這是回測、框上沒守住，先不做多", "日線收在框下就不算站穩")
    if st == "invalid":
        return ("觀望", "這套已作廢，認錯離場", invalid)
    if st == "lower":
        return ("偏多", "已經靠近下沿，可以分批做現貨或小倉多", invalid)
    if st == "upper":
        kind = ((check or {}).get("pressure") or {}).get("kind")
        if kind == "overhead":
            return ("減倉", "已經靠近上方壓力，這裡先減倉，不要追多", invalid)
        if kind == "above_edge":
            return ("減倉", "已經站到上沿上方，還沒站穩，這裡先減倉，不要追多", invalid)
        return ("減倉", "已經靠近上沿，這裡先減倉，不要追多", invalid)
    if st == "no_range":
        return ("觀望", "還在急跌，先等清楚停頓再定區間", "停頓前不抄底、也不追空")
    return ("觀望", "還在區間中間，這裡不開新單", invalid)


def voice_state(check: dict, rng: dict | None) -> str:
    """狀態：no_range / mid / lower / upper / stand_firm / stand_firm_retest / stand_firm_lost / invalid。
    stand_firm 只在最新已收完日線收在框上、現價也還在框上時成立；
    現價回到框裡＝stand_firm_retest，跌回框下＝stand_firm_lost（回測、框上沒守住）。"""
    if not rng or rng.get("low") is None or rng.get("high") is None:
        return "no_range"
    ev = {e.get("type") for e in (check.get("events") or [])}
    sf = check.get("stand_firm") or {}
    if sf.get("ok"):
        side = sf.get("hold_side")
        if side is None:
            zone = (rng or {}).get("stand_firm_zone") or [None, None]
            px = check.get("price")
            if px is not None and zone[0] is not None:
                side = "above" if px >= zone[1] else ("in_box" if px >= zone[0] else "lost")
        if side == "lost":
            return "stand_firm_lost"
        if side == "in_box":
            return "stand_firm_retest"
        return "stand_firm"
    if "stop_break" in ev or (check.get("departed") and check.get("position") == "below"):
        return "invalid"
    if check.get("departed") and check.get("position") == "above":
        return "upper"
    pos = check.get("position")
    if pos == "lower_zone":
        return "lower"
    if pos == "upper_zone":
        return "upper"
    if pos in ("below", "above") and check.get("departed"):
        return "invalid"
    return "mid"


def batch_levels_text(rng: dict) -> str:
    """列出尚未寫在主區間裡的分批層；最後一層若就是下沿，寫「與下沿」。"""
    lo = rng["low"]
    supports = [x for x in (rng.get("lower_supports") or []) if x is not None]
    nums, has_lo = [], False
    for x in supports:
        if abs(float(x) - float(lo)) < 0.51:
            has_lo = True
        else:
            nums.append(fmt_px(x))
    if nums and has_lo:
        return "、".join(nums) + " 與下沿"
    if nums:
        return "、".join(nums)
    return "下沿"


def ma120_voice(ma_d: dict | None, style: str = "btc") -> str:
    if not ma_d or not ma_d.get("ok"):
        return ""
    sma = fmt_px(ma_d["sma"])
    if ma_d.get("price_below") and not ma_d.get("heavy_long_ok"):
        if style == "btc":
            return f"價格還在 MA120（{sma}）下方，均線還沒走平後被站上，只做現貨或小倉的反彈單，重倉合約多先不要碰。"
        return f"價格還在 MA120（{sma}）下方，均線還沒走平後被站上，只做現貨或小倉反彈，重倉合約多先不要碰"
    if ma_d.get("price_below"):
        if style == "btc":
            return f"價格還在 MA120（{sma}）下方，只做現貨或小倉的反彈單，重倉合約多先不要碰。"
        return f"價格還在 MA120（{sma}）下方，只做現貨或小倉反彈，重倉合約多先不要碰"
    if ma_d.get("heavy_long_ok"):
        s = f"收盤已在 MA120（{sma}）上方且均線走平，可考慮重倉合約多"
        return s + "。" if style == "btc" else s
    s = f"收盤已在 MA120（{sma}）上方，但仍要看是否走平後被站上"
    return s + "。" if style == "btc" else s


def macd_voice(macd_d: dict | None, macd_4: dict | None = None, style: str = "btc") -> str:
    if not macd_d or not macd_d.get("ok"):
        return ""
    deep = macd_d.get("deep")
    line = macd_d.get("line") or 0
    d_gold = (macd_d.get("last_cross") or {}).get("type") == "金叉"
    f = macd_4 if macd_4 and macd_4.get("ok") else None
    f_under_gold = bool(f and (f.get("last_cross") or {}).get("type") == "金叉" and (f.get("line") or 0) < 0)
    if deep:
        head = "日線 MACD 還在零軸下方很深"
    elif line < 0:
        head = "日線 MACD 還在零軸下方"
    else:
        head = "日線 MACD 已在零軸上方"
    if line < 0 and not d_gold:
        mid = f"{head}，沒金叉"
    elif line < 0 and d_gold:
        mid = f"{head}且已金叉，只算自然反彈，不能當成反轉"
    elif d_gold:
        mid = f"{head}，已金叉"
    else:
        mid = head
    if f_under_gold and line < 0 and not d_gold:
        if style == "btc":
            mid += "；4 小時雖然已經在水下金叉，也只算自然反彈，不能當成反轉"
        else:
            mid += "；4 小時水下金叉也只算自然反彈，不能當成反轉"
    if style == "btc" and not mid.endswith("。"):
        mid += "。"
    return mid


def probe_voice(check: dict) -> str:
    if check.get("fourh_probe") == "upper":
        n = check.get("fourh_consecutive") or 0
        if n >= 2:
            return f"4 小時連續 {n} 根收盤試探上沿，日線還沒收出脫離，主區間先不重算。"
        return "4 小時收盤試探上沿，日線還沒收出脫離，主區間先不重算。"
    if check.get("fourh_probe") == "lower":
        n = check.get("fourh_consecutive") or 0
        if n >= 2:
            return f"4 小時連續 {n} 根收盤試探下沿，日線還沒收出脫離，主區間先不重算。"
        return "4 小時收盤試探下沿，日線還沒收出脫離，主區間先不重算。"
    return ""


def invalid_reason_voice(check: dict) -> str:
    ev = {e.get("type") for e in (check.get("events") or [])}
    if "stop_break" in ev:
        return "日線已經收盤跌破停損，這套就作廢，認錯離場，接下來要重算。"
    if "depart_below" in ev:
        return "日線已經收盤跌破下沿，這套就作廢，認錯離場，接下來要重算。"
    if check.get("departed"):
        return "日線已經收盤脫離原先的結構，這套就作廢，認錯離場，接下來要重算。"
    return "這套已作廢，認錯離場，接下來要重算。"


def render_btc_short(now: datetime, market: dict, rng: dict, check: dict, ma_d: dict, ma_w: dict, macd_d: dict, macd_4: dict, scenarios: list[dict]) -> str:
    """一段連貫文字：不加摘要行、不加劇本行，每個價位只出現一次。"""
    price = market["price"]
    st = voice_state(check, rng)
    time_s = fmt_time_text(now)
    ma = ma120_voice(ma_d, "btc")
    macd = macd_voice(macd_d, macd_4, "btc")
    probe = probe_voice(check)
    head = f"現價 {fmt_px(price)}，{time_s}，"
    if st == "no_range":
        body = (
            head + "還在急跌，先等日線長下影收回，或連續至少 3 根日線橫盤、最後一根不再創新低，再定區間；"
            "停頓前，不抄底、也不追空。"
        )
        text = body + ma + macd
        return text.strip() + "\n"
    lo, hi = fmt_px(rng["low"]), fmt_px(rng["high"])
    stop = fmt_px(rng["final_stop"])
    batch = batch_levels_text(rng)
    confirm = fmt_px(rng["stand_firm_zone"][1])
    if st == "invalid":
        body = head + invalid_reason_voice(check)
        body += f"原主區間是 {lo} 到 {hi}，原停損 {stop}。"
        return (body + ma + macd).strip() + "\n"
    if st == "stand_firm":
        z0, z1 = fmt_px(rng["stand_firm_zone"][0]), fmt_px(rng["stand_firm_zone"][1])
        body = (
            head + f"已經突破並站上框。前高框是 {z0} 到 {z1}："
            "實體還在框上面就做多，實體跌回框裡就認錯停損，必要時反手。"
            "目標先看最近的密集區，不要看太遠。"
        )
        return (body + ma + macd).strip() + "\n"
    if st == "stand_firm_retest":
        z0, z1 = fmt_px(rng["stand_firm_zone"][0]), fmt_px(rng["stand_firm_zone"][1])
        body = (
            head + f"日線收在框上，現價回到框裡回測。前高框是 {z0} 到 {z1}："
            "回測守住、實體沒跌回框下，才繼續偏多，這裡先不追；實體跌回框下就認錯停損。"
        )
        return (body + ma + macd).strip() + "\n"
    if st == "stand_firm_lost":
        z0, z1 = fmt_px(rng["stand_firm_zone"][0]), fmt_px(rng["stand_firm_zone"][1])
        body = (
            head + f"日線雖然收在框上，現價又跌回框下，這是回測、框上沒守住。前高框是 {z0} 到 {z1}："
            "這裡先不做多，等下一根日線收盤，收回框上才算守住，收在框下就不算站穩。"
        )
        return (body + ma + macd).strip() + "\n"
    if st == "upper":
        return (upper_btc_body(head, rng, check, lo, hi, stop, batch, confirm) + ma + macd + probe).strip() + "\n"
    if st == "lower":
        loc = "已經靠近下沿，可以分批做現貨或小倉多"
    elif st == "upper":
        loc = "已經靠近上沿，這裡先減倉，不要追多"
    else:
        loc = "還在主區間中間，這裡不開新單"
    body = (
        head + f"{loc}。主區間是 {lo} 到 {hi}：靠近 {batch}，再分批現貨或小倉多，停損 {stop}；"
    )
    if st == "upper":
        body += f"要突破並站穩 {confirm} 確認帶才算轉強，還沒站穩以前，中間也不開新單。"
    else:
        body += f"靠近上沿就減倉，要突破並站穩 {confirm} 確認帶才算轉強。"
    text = body + ma + macd + probe
    return text.strip() + "\n"


def _above_confirm(check: dict | None, rng: dict) -> bool:
    """現價已在站穩確認帶上緣上方（確認帶此時是支撐，不是要突破的價位）。"""
    px = (check or {}).get("price")
    zone = (rng or {}).get("stand_firm_zone") or [None, None]
    return px is not None and zone[1] is not None and float(px) > float(zone[1])


def _pressure(check: dict | None, rng: dict) -> dict:
    pv = (check or {}).get("pressure")
    return pv or {"kind": "edge", "level": rng["high"], "former_low": bool(rng.get("high_former_low")), "need_reclaim": False}


def upper_btc_body(head: str, rng: dict, check: dict, lo: str, hi: str, stop: str, batch: str, confirm: str) -> str:
    """靠近上沿／上方壓力的 /btc 段：寫明是哪個價位，每個數字只出現一次。"""
    pv = _pressure(check, rng)
    if _above_confirm(check, rng):
        tail_confirm = f"要日線由下往上站穩 {confirm} 確認帶才算轉強，還沒站穩以前，中間也不開新單。"
    else:
        tail_confirm = f"要突破並站穩 {confirm} 確認帶才算轉強，還沒站穩以前，中間也不開新單。"
    if pv["kind"] == "overhead":
        body = (head + f"已經靠近上方壓力 {fmt_px(pv['level'])}，這裡先減倉，不要追多。"
                "這裡原本是主區間下沿，跌破後變成壓力。"
                f"主區間是 {lo} 到 {hi}：靠近 {batch}，再分批現貨或小倉多，停損 {stop}；")
        if pv.get("need_reclaim"):
            body += "日線要由下往上收回上方壓力，才算轉強，還沒收上去以前，中間也不開新單。"
        else:
            body += tail_confirm
        return body
    former = "這個上沿原本是主區間下沿，跌破後變成壓力。" if pv.get("former_low") else ""
    if pv["kind"] == "above_edge":
        return (head + f"已經站到上沿 {hi} 上方，但日線還沒由下往上站穩，這裡先減倉，不要追多。" + former
                + f"主區間下沿是 {lo}：靠近 {batch}，再分批現貨或小倉多，停損 {stop}；"
                + f"要日線由下往上站穩 {confirm} 確認帶才算轉強，還沒站穩以前，中間也不開新單。")
    return (head + f"已經靠近上沿 {hi}，這裡先減倉，不要追多。" + former
            + f"主區間下沿是 {lo}：靠近 {batch}，再分批現貨或小倉多，停損 {stop}；" + tail_confirm)


def key_levels_voice(rng: dict, st: str, check: dict | None = None) -> str:
    if st == "no_range":
        return "尚未形成主區間"
    lo, hi = fmt_px(rng["low"]), fmt_px(rng["high"])
    stop = fmt_px(rng["final_stop"])
    if st == "invalid":
        return f"原主區間 {lo}–{hi}；原停損 {stop}"
    if st == "stand_firm":
        z0, z1 = fmt_px(rng["stand_firm_zone"][0]), fmt_px(rng["stand_firm_zone"][1])
        return f"前高框 {z0}–{z1}；目標先看最近的密集區；跌回後反手停損看框內"
    if st == "stand_firm_retest":
        z0, z1 = fmt_px(rng["stand_firm_zone"][0]), fmt_px(rng["stand_firm_zone"][1])
        return f"前高框 {z0}–{z1}；現價在框裡回測，守住才偏多"
    if st == "stand_firm_lost":
        z0, z1 = fmt_px(rng["stand_firm_zone"][0]), fmt_px(rng["stand_firm_zone"][1])
        return f"前高框 {z0}–{z1}；回測沒守住，等日線收盤再判"
    batch = batch_levels_text(rng)
    confirm = fmt_px(rng["stand_firm_zone"][1])
    if st == "upper":
        pv = _pressure(check, rng)
        if pv["kind"] == "overhead":
            return (f"上方壓力 {fmt_px(pv['level'])}；主區間 {lo}–{hi}；分批 {batch}；停損 {stop}"
                    + ("" if pv.get("need_reclaim") else f"；上沿確認到 {confirm}"))
        return f"主區間下沿 {lo}、上沿 {hi}；分批 {batch}；停損 {stop}；上沿確認到 {confirm}"
    return f"主區間 {lo}–{hi}；分批 {batch}；停損 {stop}；上沿確認到 {confirm}"


def short_direction_voice(st: str, check: dict) -> str:
    if st == "no_range":
        return "還在急跌，先等日線長下影收回，或連續至少 3 根日線橫盤、最後一根不再創新低，再定區間；停頓前，不抄底、也不追空。"
    if st == "invalid":
        return invalid_reason_voice(check)
    if st == "stand_firm":
        return "已經突破並站上框，實體還在框上面就做多；實體跌回框裡就認錯停損，必要時反手。目標先看最近的密集區，不要看太遠。"
    if st == "stand_firm_retest":
        return "日線收在框上，現價回到框裡回測；守住、實體沒跌回框下才繼續偏多，這裡先不追，實體跌回框下就認錯停損。"
    if st == "stand_firm_lost":
        return "現價跌回框下，這是回測、框上沒守住，先不做多；等下一根日線收盤，收回框上才算守住，收在框下就不算站穩。"
    if st == "lower":
        return "已經靠近下沿，可以分批做現貨或小倉多，停損放在已寫的位置。靠近上沿就減倉；要突破並站穩確認帶，才算轉強。"
    if st == "upper":
        pv = (check or {}).get("pressure") or {"kind": "edge"}
        if pv.get("kind") == "overhead":
            return ("已經靠近上方壓力，這裡先減倉，不要追多；這個壓力原本是主區間下沿，跌破後變成壓力。"
                    + ("日線要由下往上收回上方壓力，才算轉強；還沒收上去以前，中間也不開新單。" if pv.get("need_reclaim")
                       else "要日線由下往上站穩確認帶，才算轉強；還沒站穩以前，中間也不開新單。"
                       if (check or {}).get("price") is not None and pv.get("above_confirm")
                       else "要突破並站穩確認帶，才算轉強；還沒站穩以前，中間也不開新單。"))
        former = "這個上沿原本是主區間下沿，跌破後變成壓力。" if pv.get("former_low") else ""
        if pv.get("kind") == "above_edge":
            return ("已經站到上沿上方，但日線還沒由下往上站穩，這裡先減倉，不要追多。" + former
                    + "要日線由下往上站穩確認帶，才算轉強；還沒站穩以前，中間也不開新單。")
        return "已經靠近上沿，這裡先減倉，不要追多。" + former + "要突破並站穩確認帶，才算轉強；還沒站穩以前，中間也不開新單。"
    return "還在區間中間，這裡不開新單。靠近下沿再分批現貨或小倉多，靠近上沿就減倉；要突破並站穩確認帶，才算轉強。"


BANK_TARGET_FILE = Path("/workspace/big-players/bank-targets/briefing_lines.txt")


def load_btc_bank_target(path: Path | None = None) -> str | None:
    """只取加權目標與區間，不含命中率、偏差、樣本數或原始加權。"""
    path = path or BANK_TARGET_FILE
    try:
        text = path.read_text()
    except OSError:
        return None
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("BTC"):
            continue
        m = re.search(
            r"投行目標｜\s*(加權\s*[0-9,]+（區間\s*[0-9,]+[–-][0-9,]+)",
            s,
        )
        if m:
            return m.group(1).rstrip("；;）)") + "）"
        m = re.search(r"投行目標｜\s*(.+)$", s)
        if m:
            return m.group(1).strip()
    return None


def render_report(now: datetime, market: dict, rng: dict, check: dict, ma_d: dict, macd_d: dict, macd_4: dict | None = None) -> str:
    price = market["price"]
    st = voice_state(check, rng)
    lines = [f"現價｜{fmt_px(price)} 美元，{fmt_time_text(now)}"]
    bank = load_btc_bank_target()
    if bank:
        lines.append(f"投行目標｜{bank}")
    lines.append(f"關鍵價位｜{key_levels_voice(rng, st, check)}")
    obs = short_direction_voice(st, check)
    ma = ma120_voice(ma_d, "report")
    macd = macd_voice(macd_d, macd_4, "report")
    if ma:
        obs += ma if obs.endswith("。") else ("。" + ma if ma else "")
        if not obs.endswith("。"):
            obs += "。"
    if macd:
        obs += macd if macd.endswith("。") else macd + "。"
    probe = probe_voice(check)
    if probe:
        obs += probe
    lines.append("觀察｜" + obs)
    return "\n".join(lines) + "\n"


def render_report_slim(now: datetime, market: dict, rng: dict, check: dict, ma_d: dict, macd_d: dict, macd_4: dict | None = None) -> str:
    price = market["price"]
    st = voice_state(check, rng)
    bank = load_btc_bank_target() or ""
    lines = ["【/report 精簡版】"]
    lines.append(f"現價｜{fmt_px(price)} 美元，{fmt_time_text(now)}")
    lines.append(f"投行目標｜{bank}" if bank else "投行目標｜")
    lines.append(f"關鍵價位｜{key_levels_voice(rng, st, check)}")
    lines.append(f"短線方向｜{short_direction_voice(st, check).rstrip('。')}。")
    bullets = []
    ma = ma120_voice(ma_d, "report")
    if ma:
        bullets.append("・" + ma.rstrip("。"))
    macd = macd_voice(macd_d, macd_4, "report")
    if macd:
        bullets.append("・" + macd.rstrip("。"))
    if bullets:
        lines.append("")
        lines.extend(bullets[:2])
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return text


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def parse_when(s: str | None) -> datetime:
    """解析發布標題時間（台北時間 YYYY-MM-DD HH:MM）。"""
    if not s:
        return datetime.now(TAIPEI)
    raw = str(s).strip().replace("T", " ")
    # allow "2026.11.17 10:19" or "2026-11-17 10:19"
    raw = raw.replace(".", "-", 2) if raw[:4].isdigit() and "." in raw[:10] else raw
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw[:19] if fmt.endswith("%S") else raw[:16], fmt)
            return dt.replace(tzinfo=TAIPEI)
        except ValueError:
            continue
    raise ValueError(f"無法解析 --when：{s}")


def run(out_dir: Path, force_recompute: bool = False, seed: bool = False, market: dict | None = None,
        price: float | None = None, when: str | None = None, market_file: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "short_model_state.json"
    now = parse_when(when)

    if seed or not state_path.exists():
        state = init_state()
    else:
        state = json.loads(state_path.read_text())

    if market is None and market_file:
        market = json.loads(Path(market_file).read_text())
    if market is None:
        market = fetch_market(skip_ticker=price is not None)
    if price is not None:
        market = dict(market)
        market["price"] = float(price)
        market["price_override"] = True
    ma_d = ma120_view(market["klines"]["1d"])
    ma_w = ma120_view(market["klines"]["1w"])
    macd_d = macd_view(market["klines"]["1d"])
    macd_4 = macd_view(market["klines"]["4h"])

    check = check_state(state, market)
    if not state.get("last_recompute_bar") and check.get("recompute_bar"):
        state["last_recompute_bar"] = check["recompute_bar"]
    if state.get("overhead_levels") is None:
        state["overhead_levels"] = check.get("overhead_levels") or []
    did_recompute = False
    if force_recompute or check["recompute"]:
        reason = check["reason_text"] if check["recompute"] else "手動強制重算"
        ev_types = {e.get("type") for e in check.get("events") or []}
        overhead = resolve_overhead(state)
        old_rng = state.get("main_range", {})
        go_up = (not force_recompute and bool(ev_types & {"stand_firm", "depart_above", "reclaim_overhead"})
                 and not ev_types & {"depart_below", "stop_break"})
        if go_up:
            if "reclaim_overhead" in ev_types:
                lvl = max(e["level"] for e in check["events"] if e.get("type") == "reclaim_overhead")
                support = [lvl, lvl + STAND_FIRM_BAND]
            else:
                support = list(old_rng["stand_firm_zone"])
            # 同一根若也收上了記下的上方壓力，支撐改用收上去的那一個
            _c = (check.get("closed_1d") or {}).get("close")
            _over = [x for x in overhead if _c is not None and support[1] < x < _c]
            if _over:
                support = [max(_over), max(_over) + STAND_FIRM_BAND]
            new_rng = recompute_range_up(market, old_rng, support, overhead, reason)
        else:
            new_rng = recompute_range(market, reason)
            new_rng.update(direction="down", broken_zone=None, high_former_low=False)
            if ev_types & {"depart_below", "stop_break"} and old_rng.get("low") is not None:
                overhead = sorted(set(overhead) | {float(old_rng["low"])})
        state["overhead_levels"] = [x for x in overhead if x > new_rng["high"]]
        state["main_range"] = {**state.get("main_range", {}), **new_rng}
        state["scenarios"] = scenarios_from_range(state["main_range"])
        state["last_recompute_reason"] = reason
        _lb = _last_closed(market["klines"]["1d"])
        state["last_recompute_bar"] = bar_date(_lb) if _lb else state.get("last_recompute_bar")
        did_recompute = True
        # 重算後再對新區間定位一次（距離與 position 用新區間）
        check = check_state(state, market)
        check["departed"] = True if not force_recompute else check["departed"]
        check["recompute"] = True
        if force_recompute and not check.get("reason_text"):
            check["reason_text"] = reason

    state["scenarios"] = update_scenario_status(state["scenarios"], check)
    rng = state["main_range"]

    payload = {
        "generated_at": fmt_time(now),
        "kline_source": market["source"],
        "kline_source_note": market["source_note"],
        "fetch_errors": market.get("errors") or [],
        "price": market["price"],
        "main_range": rng,
        "check": check,
        "recomputed": did_recompute,
        "ma120": {"1d": ma_d, "1w": ma_w},
        "macd": {"1d": macd_d, "4h": macd_4},
        "scenarios": state["scenarios"],
        "last_recompute_reason": state.get("last_recompute_reason"),
        "last_recompute_bar": state.get("last_recompute_bar"),
        "origin": state.get("origin"),
    }
    btc_txt = render_btc_short(now, market, rng, check, ma_d, ma_w, macd_d, macd_4, state["scenarios"])
    report_txt = render_report(now, market, rng, check, ma_d, macd_d, macd_4)
    slim_txt = render_report_slim(now, market, rng, check, ma_d, macd_d, macd_4)

    (out_dir / "short_model.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    (out_dir / "btc_short.txt").write_text(btc_txt)
    (out_dir / "report_btc.txt").write_text(report_txt)
    (out_dir / "report_slim.txt").write_text(slim_txt)
    forecast_short = "【短線】\n" + btc_txt
    if not forecast_short.endswith("\n"):
        forecast_short += "\n"
    (out_dir / "forecast_short.txt").write_text(forecast_short)
    direction, target, invalid = bias_line(check, rng, ma_d)
    sp_view = support_pressure_view(rng, check, float(market["price"]))
    sp_text = support_pressure_text(sp_view)
    levels = {
        "bias": direction,
        "watch": f"短線{direction}；{target}。失效：{invalid}。",
        "support_pressure": sp_text,
        "supports": sp_view["supports"],
        "pressure": sp_view["pressure"],
        "forecast_invalid": invalid,
        "targets": rng.get("lower_supports"),
        "inv": rng.get("final_stop"),
        "stand_firm_zone": rng.get("stand_firm_zone"),
        "range": [rng.get("low"), rng.get("high")],
    }
    (out_dir / "levels.json").write_text(json.dumps(levels, ensure_ascii=False) + "\n")
    payload["levels"] = levels
    uniq_errs = unique_number_errors(("/btc 短線段", btc_txt), ("report_slim.txt", slim_txt))
    uniq_errs += support_pressure_errors(sp_view, float(market["price"]), sp_text)
    uniq_errs += text_pressure_errors(sp_view, btc_txt, slim_txt, report_txt)
    payload["unique_check"] = {"ok": not uniq_errs, "errors": uniq_errs}
    if uniq_errs:
        for e in uniq_errs:
            print(e, file=sys.stderr)
        raise SystemExit(1)
    # 狀態檔只保留劇本與主區間，供隔日比對；不把每次現價寫死成新劇本
    persist = {
        "version": state.get("version", 1),
        "origin": state.get("origin"),
        "main_range": rng,
        "scenarios": state["scenarios"],
        "last_recompute_reason": state.get("last_recompute_reason"),
        "last_recompute_bar": state.get("last_recompute_bar"),
        "overhead_levels": state.get("overhead_levels") or [],
        "updated_at": fmt_time(now),
    }
    state_path.write_text(json.dumps(persist, ensure_ascii=False, indent=2) + "\n")
    return payload


def demo_states(out_dir: Path) -> int:
    """六種狀態樣稿（假資料），供檢查口語模板與數字不重複。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 11, 17, 9, 30, tzinfo=TAIPEI)
    rng = {
        "low": 60000.0, "high": 67000.0, "final_stop": 57800.0,
        "lower_supports": [61800.0, 61174.0, 60000.0],
        "stand_firm_zone": [67000.0, 67500.0],
        "val": 61800.0, "fib_0886": 61174.0,
    }
    ma_d = {"ok": True, "sma": 75568.5, "price_below": True, "heavy_long_ok": False, "flatish": True}
    macd_d = {"ok": True, "deep": True, "line": -3000, "last_cross": {"type": "none"}}
    macd_4 = {"ok": True, "line": -100, "last_cross": {"type": "金叉"}}

    def chk(pos, departed=False, sf=False, events=None):
        return {
            "position": pos, "departed": departed,
            "stand_firm": {"ok": sf}, "events": events or [],
            "fourh_probe": None, "fourh_consecutive": 0,
            "recompute": departed, "reason_text": "",
        }

    cases = [
        ("1_no_range", {"price": 64655.0, "klines": {}}, {}, chk("mid")),
        ("2_mid", {"price": 64655.0, "klines": {}}, rng, chk("mid")),
        ("3_lower", {"price": 61500.0, "klines": {}}, rng, chk("lower_zone")),
        ("4_upper", {"price": 66500.0, "klines": {}}, rng, chk("upper_zone")),
        ("5_stand_firm", {"price": 67800.0, "klines": {}}, rng, chk("above", True, True, [{"type": "stand_firm"}])),
        ("6_invalid", {"price": 57000.0, "klines": {}}, rng, chk("below", True, False, [{"type": "stop_break"}, {"type": "depart_below"}])),
        ("7_stand_firm_retest", {"price": 67300.0, "klines": {}}, rng,
         {**chk("above", True, True, [{"type": "stand_firm"}]), "stand_firm": {"ok": True, "hold_side": "in_box"}}),
        ("8_stand_firm_lost", {"price": 66400.0, "klines": {}}, rng,
         {**chk("above", True, True, [{"type": "stand_firm"}]), "stand_firm": {"ok": True, "hold_side": "lost"}}),
    ]
    failed = 0
    for name, market, r, check in cases:
        btc = render_btc_short(now, market, r, check, ma_d, {}, macd_d, macd_4, [])
        slim = render_report_slim(now, market, r, check, ma_d, macd_d, macd_4)
        (out_dir / f"{name}_btc.txt").write_text(btc)
        (out_dir / f"{name}_slim.txt").write_text(slim)
        errs = unique_number_errors(("/btc 短線段", btc), ("report_slim.txt", slim))
        if r:
            _sp = support_pressure_view(r, check, float(market["price"]))
            _spt = support_pressure_text(_sp)
            errs += support_pressure_errors(_sp, float(market["price"]), _spt)
            errs += text_pressure_errors(_sp, btc, slim)
            (out_dir / f"{name}_support_pressure.txt").write_text(_spt + "\n")
        other = []
        blob = btc + slim
        if "劇本：" in blob or blob.lstrip().startswith("短線："):
            other.append("仍有摘要行或劇本行")
        if "數天" in blob or "數週" in blob or "數周" in blob:
            other.append("仍有週期標籤")
        if "以及下沿" in blob:
            other.append("分批仍寫「以及下沿」，應寫「與下沿」")
        if "4h" in blob or "4H" in blob:
            other.append("仍寫 4h，應寫 4 小時")
        if name.startswith(("7_", "8_")) and ("站上框" in blob or "突破並站" in blob):
            other.append("現價不在框上卻寫站上框")
        st = voice_state(check, r)
        status = "PASS" if not errs and not other else "FAIL"
        if status == "FAIL":
            failed += 1
        print(f"{status} {name} state={st}")
        print(btc.strip())
        if errs or other:
            print("  ", "；".join(errs + other))
        print()
    print("DEMO_FAILS", failed)
    return 1 if failed else 0



def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="BTC 短線模型每日檢查（試做，不上線）")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="輸出目錄")
    p.add_argument("--seed", action="store_true", help="重建初始狀態（11/17 已確認結論）")
    p.add_argument("--force-recompute", action="store_true", help="強制重算主區間（試做／除錯用）")
    p.add_argument("--forecast-short", action="store_true", help="只把【短線】段印到 stdout，供 publish 使用")
    p.add_argument("--levels", action="store_true", help="只把 levels JSON 印到 stdout，供 publish 使用")
    p.add_argument("--demo-states", metavar="DIR", help="用假資料輸出六種狀態樣稿並做數字自檢，不抓盤")
    p.add_argument("--price", type=float, default=None, help="沿用發布標題現價，不再自己抓 ticker")
    p.add_argument("--when", default=None, help="台北時間 YYYY-MM-DD HH:MM，沿用發布標題時間")
    p.add_argument("--market-file", default=None, help="回測用：讀已存好的 K 線 JSON，不抓盤")
    args = p.parse_args(argv)
    if args.demo_states:
        return demo_states(Path(args.demo_states))
    price = args.price
    when = args.when
    if price is None and os.environ.get("BTC_SPOT_PRICE"):
        price = float(os.environ["BTC_SPOT_PRICE"].replace(",", ""))
    if not when and os.environ.get("BTC_SPOT_WHEN"):
        when = os.environ["BTC_SPOT_WHEN"]
    payload = run(Path(args.out), force_recompute=args.force_recompute, seed=args.seed,
                  price=price, when=when, market_file=args.market_file)
    chk = payload["check"]
    status = (f"來源 {payload['kline_source']}  現價 {payload['price']:.1f}  "
              f"脫離={chk['departed']}  重算={payload['recomputed']}  "
              f"位置={chk['position']}")
    if args.forecast_short:
        print(status, file=sys.stderr)
        print(chk["reason_text"], file=sys.stderr)
        sys.stdout.write((Path(args.out) / "forecast_short.txt").read_text())
        return 0
    if args.levels:
        print(status, file=sys.stderr)
        sys.stdout.write((Path(args.out) / "levels.json").read_text())
        return 0
    print(status)
    print(chk["reason_text"])
    print(f"已寫入 {args.out}/short_model.json、btc_short.txt、report_btc.txt、report_slim.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

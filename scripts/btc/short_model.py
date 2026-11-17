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

import argparse
import json
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

BINANCE_SPOT = "https://api.binance.com/api/v3"
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


def fetch_market(limit_4h: int = 500, limit_1d: int = 400, limit_1w: int = 200) -> dict:
    """回傳 klines 與來源名稱。失敗才往下一個免費現貨來源。"""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    errors = []

    try:
        d1 = _fetch_binance("1d", limit_1d, now_ms)
        h4 = _fetch_binance("4h", limit_4h, now_ms)
        w1 = _fetch_binance("1w", limit_1w, now_ms)
        try:
            px = _fetch_binance_ticker()
        except Exception:
            px = (h4[-1]["close"] if h4 else d1[-1]["close"])
        return {"source": "binance_spot", "source_note": "幣安現貨 api.binance.com /api/v3/klines",
                "klines": {"4h": h4, "1d": d1, "1w": w1}, "price": px, "errors": errors}
    except Exception as e:
        errors.append(f"binance: {type(e).__name__}: {e}")

    try:
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
    }


# ---------------------------------------------------------------------------
# 檢查邏輯
# ---------------------------------------------------------------------------
def stand_firm(closed_1d: list[dict], closed_4h: list[dict], zone: list[float]) -> dict:
    """站穩判定：只用已收完日線收盤，4h 不能當站穩。影線穿過與未收完的 K 不算。

    至少一根已收完日線收盤 >= 上沿（67,000），且最新已收完日線仍未收在上沿下方。
    收盤落在 67,000–67,500 或更高都算站穩。
    """
    lo, hi = zone[0], zone[1]
    daily_hit = False
    daily_held = False
    if closed_1d:
        for r in closed_1d:
            if r["close"] >= lo:
                daily_hit = True
        if daily_hit and closed_1d[-1]["close"] >= lo:
            daily_held = True
        elif daily_hit and closed_1d[-1]["close"] < lo:
            daily_held = False  # 曾站上又收跌回
    return {
        "ok": daily_held,
        "daily_close_above": daily_held,
        "daily_had_close_above": daily_hit,
        "zone": zone,
        "rule": "只看已收完日線：收盤站上上沿且最新日線未再收跌回。4h 不能當站穩。影線穿過不算，未收完的 K 不算。",
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
    sf = stand_firm(closed_bars(d1), closed_bars(h4), zone)

    events = []
    departed = False
    # 脫離／重算只看已收完日線（R27：要撐過幾天才算）。4h 只標記試探。
    if c1 is not None and c1 < lo:
        departed = True
        events.append({"type": "depart_below", "tf": "1d", "close": c1, "level": lo,
                       "reason": f"日線收盤 {fmt_px(c1, 1)} 低於主區間下沿 {fmt_px(lo)}"})
    if c1 is not None and c1 > hi:
        departed = True
        events.append({"type": "depart_above", "tf": "1d", "close": c1, "level": hi,
                       "reason": f"日線收盤 {fmt_px(c1, 1)} 高於主區間上沿 {fmt_px(hi)}"})
    if c1 is not None and c1 < stop:
        departed = True
        events.append({"type": "stop_break", "tf": "1d", "close": c1, "level": stop,
                       "reason": f"日線收盤 {fmt_px(c1, 1)} 跌破最終停損 {fmt_px(stop)}"})
    if sf["ok"]:
        departed = True
        events.append({"type": "stand_firm", "zone": zone,
                       "reason": f"日線已站穩 {fmt_px(zone[0])}–{fmt_px(zone[1])}，確認轉強，主區間需重算"})

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
            "；".join(e["reason"] for e in events)
            if events else (
                f"日線未脫離主區間；{next((x['note'] for x in intra if x.get('type')=='fourh_probe'), '')}，沿用現有劇本"
                if fourh_probe else
                "未脫離主區間，也未打到失效價或站穩確認，沿用現有劇本"
            )
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
            t["status"] = "active" if check["stand_firm"]["ok"] else "pending"
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


def bias_line(check: dict, rng: dict, ma_d: dict) -> tuple[str, str, str]:
    """回傳 (方向, 目標, 失效) 供「短線：方向｜目標｜失效」。"""
    pos = check["position"]
    if check["stand_firm"]["ok"]:
        return ("偏多", f"站穩 {fmt_px(rng['stand_firm_zone'][0])}–{fmt_px(rng['stand_firm_zone'][1])} 後看最近壓力",
                f"實體跌回 {fmt_px(rng['high'])} 下方")
    if pos == "below":
        return ("觀望", "等回抽失敗再看空，不追跌", f"日線收盤重新站回 {fmt_px(rng['low'])}")
    if pos == "lower_zone":
        return ("偏多", f"{fmt_px(rng['fib_0786'])}、{fmt_px(rng['high'])}",
                f"收盤跌破 {fmt_px(rng['final_stop'])}")
    if pos == "upper_zone":
        return ("減倉", f"靠近 {fmt_px(rng['high'])} 減倉或賣",
                f"突破並站穩 {fmt_px(rng['stand_firm_zone'][0])}–{fmt_px(rng['stand_firm_zone'][1])}")
    # mid
    return ("觀望",
            f"靠近 {fmt_px(rng['high'])} 減倉；下沿 {fmt_px(rng['val'] or rng['fib_0886'])}／{fmt_px(rng['fib_0886'])}／{fmt_px(rng['low'])} 分批",
            f"收盤跌破 {fmt_px(rng['final_stop'])}")


def render_btc_short(now: datetime, market: dict, rng: dict, check: dict, ma_d: dict, ma_w: dict, macd_d: dict, macd_4: dict, scenarios: list[dict]) -> str:
    price = market["price"]
    direction, target, invalid = bias_line(check, rng, ma_d)
    lines = []
    lines.append(f"短線：{direction}｜目標 {target}｜失效：{invalid}")
    pos_zh = {
        "mid": "主區間中段，不開新單",
        "lower_zone": "下沿分批區，可分批現貨或小倉多",
        "upper_zone": "靠近上沿，減倉或賣",
        "below": "已收盤脫離下沿",
        "above": "已收盤脫離上沿",
    }[check["position"]]
    probe_txt = ""
    if check.get("fourh_probe") == "upper":
        probe_txt = "4h 試探上沿，日線尚未收出脫離，不重算主區間。"
        if check.get("fourh_consecutive", 0) >= 2:
            probe_txt = f"4h 連續 {check['fourh_consecutive']} 根收盤試探上沿，日線尚未收出脫離，不重算主區間。"
    elif check.get("fourh_probe") == "lower":
        probe_txt = "4h 試探下沿，日線尚未收出脫離，不重算主區間。"
        if check.get("fourh_consecutive", 0) >= 2:
            probe_txt = f"4h 連續 {check['fourh_consecutive']} 根收盤試探下沿，日線尚未收出脫離，不重算主區間。"
    lines.append(
        f"現價 {fmt_px(price)}，{fmt_time_text(now)}，"
        f"位於 {fmt_px(rng['low'])}–{fmt_px(rng['high'])} {pos_zh}。"
        + probe_txt
    )
    gate = (ma_d.get("gate") + "。") if ma_d.get("ok") and ma_d.get("gate") else ""
    macd_txt = macd_sentence(macd_d, macd_4)
    extra = (gate + macd_txt).strip()
    if extra:
        lines.append(extra)
    # 精簡劇本行（固定 2–3 條要點，不把中段小結構寫成主區間）
    lines.append(
        f"劇本：靠近 {fmt_px(rng['val'] or rng['fib_0886'])}／{fmt_px(rng['fib_0886'])}／{fmt_px(rng['low'])} "
        f"分批現貨或小倉多，停損 {fmt_px(rng['final_stop'])}；靠近 {fmt_px(rng['high'])} 減倉；"
        f"要突破並站穩 {fmt_px(rng['stand_firm_zone'][0])}–{fmt_px(rng['stand_firm_zone'][1])} 才確認轉強。"
    )
    if check["departed"]:
        lines.append(f"結構脫離：{check['reason_text']}。以上為重算後的主區間與劇本。")
    return "\n".join(lines) + "\n"


def render_report(now: datetime, market: dict, rng: dict, check: dict, ma_d: dict, macd_d: dict, macd_4: dict | None = None) -> str:
    price = market["price"]
    supports = "、".join(fmt_px(x) for x in rng["lower_supports"])
    lines = []
    lines.append(f"現價｜{fmt_px(price)} 美元，{fmt_time_text(now)}")
    lines.append(
        f"關鍵價位｜主區間 {fmt_px(rng['low'])}–{fmt_px(rng['high'])}；"
        f"下方 {supports}；最終停損 {fmt_px(rng['final_stop'])}；"
        f"上沿確認 {fmt_px(rng['stand_firm_zone'][0])}–{fmt_px(rng['stand_firm_zone'][1])}"
    )
    pos_zh = {
        "mid": "現價在主區間中段，不開新單",
        "lower_zone": "現價已靠近下沿，可分批現貨或小倉多",
        "upper_zone": "現價靠近上沿，減倉或賣",
        "below": "收盤已脫離主區間下沿",
        "above": "收盤已脫離主區間上沿",
    }[check["position"]]
    obs = (
        f"觀察｜{pos_zh}。"
        f"靠近下沿才分批現貨或小倉多，靠近 {fmt_px(rng['high'])} 減倉；"
        f"要突破並站穩 {fmt_px(rng['stand_firm_zone'][0])}–{fmt_px(rng['stand_firm_zone'][1])} 才確認轉強。"
    )
    if check.get("fourh_probe") == "upper":
        obs += "4h 試探上沿，日線尚未收出脫離，不重算主區間。"
    elif check.get("fourh_probe") == "lower":
        obs += "4h 試探下沿，日線尚未收出脫離，不重算主區間。"
    if ma_d.get("ok"):
        obs += f"日線 MA120 在 {fmt_px(ma_d['sma'])}，{ma_d['gate']}。"
    macd_txt = macd_sentence(macd_d, macd_4)
    if macd_txt:
        obs += macd_txt
    if check["departed"]:
        obs += f"結構脫離原因：{check['reason_text']}。"
    lines.append(obs)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(out_dir: Path, force_recompute: bool = False, seed: bool = False, market: dict | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "short_model_state.json"
    now = datetime.now(TAIPEI)

    if seed or not state_path.exists():
        state = init_state()
    else:
        state = json.loads(state_path.read_text())

    if market is None:
        market = fetch_market()
    ma_d = ma120_view(market["klines"]["1d"])
    ma_w = ma120_view(market["klines"]["1w"])
    macd_d = macd_view(market["klines"]["1d"])
    macd_4 = macd_view(market["klines"]["4h"])

    check = check_state(state, market)
    did_recompute = False
    if force_recompute or check["recompute"]:
        reason = check["reason_text"] if check["recompute"] else "手動強制重算"
        new_rng = recompute_range(market, reason)
        state["main_range"] = {**state.get("main_range", {}), **new_rng}
        state["scenarios"] = scenarios_from_range(state["main_range"])
        state["last_recompute_reason"] = reason
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
        "origin": state.get("origin"),
    }
    btc_txt = render_btc_short(now, market, rng, check, ma_d, ma_w, macd_d, macd_4, state["scenarios"])
    report_txt = render_report(now, market, rng, check, ma_d, macd_d, macd_4)

    (out_dir / "short_model.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    (out_dir / "btc_short.txt").write_text(btc_txt)
    (out_dir / "report_btc.txt").write_text(report_txt)
    forecast_short = "【短線】數天\n" + btc_txt
    if not forecast_short.endswith("\n"):
        forecast_short += "\n"
    (out_dir / "forecast_short.txt").write_text(forecast_short)
    direction, target, invalid = bias_line(check, rng, ma_d)
    supports = "、".join(fmt_px(x) for x in rng["lower_supports"])
    levels = {
        "bias": direction,
        "watch": f"短線{direction}；目標 {target}。失效：{invalid}。",
        "support_pressure": f"支撐 {supports}｜壓力 {fmt_px(rng['stand_firm_zone'][0])}、{fmt_px(rng['stand_firm_zone'][1])}",
        "forecast_invalid": invalid,
        "targets": rng.get("lower_supports"),
        "inv": rng.get("final_stop"),
        "stand_firm_zone": rng.get("stand_firm_zone"),
        "range": [rng.get("low"), rng.get("high")],
    }
    (out_dir / "levels.json").write_text(json.dumps(levels, ensure_ascii=False) + "\n")
    payload["levels"] = levels
    # 狀態檔只保留劇本與主區間，供隔日比對；不把每次現價寫死成新劇本
    persist = {
        "version": state.get("version", 1),
        "origin": state.get("origin"),
        "main_range": rng,
        "scenarios": state["scenarios"],
        "last_recompute_reason": state.get("last_recompute_reason"),
        "updated_at": fmt_time(now),
    }
    state_path.write_text(json.dumps(persist, ensure_ascii=False, indent=2) + "\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="BTC 短線模型每日檢查（試做，不上線）")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="輸出目錄")
    p.add_argument("--seed", action="store_true", help="重建初始狀態（11/17 已確認結論）")
    p.add_argument("--force-recompute", action="store_true", help="強制重算主區間（試做／除錯用）")
    p.add_argument("--forecast-short", action="store_true", help="只把【短線】段印到 stdout，供 publish 使用")
    p.add_argument("--levels", action="store_true", help="只把 levels JSON 印到 stdout，供 publish 使用")
    args = p.parse_args(argv)
    payload = run(Path(args.out), force_recompute=args.force_recompute, seed=args.seed)
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
    print(f"已寫入 {args.out}/short_model.json、btc_short.txt、report_btc.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

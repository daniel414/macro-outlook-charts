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

import argparse, os, hashlib
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

# 規則庫同步點（2026-11-23：R79–R92 與既有規則補丁）。每次執行都比對 rules.md 的 hash，
# 不一致就在 log、狀態行、state 與 short_model_internal.json 標「規則庫已更新，待同步」，不默默跳過。
# 這些內部標記不進 levels.json、short_model.json（網站、發布程式會讀）。
RULES_FILE = Path(os.environ.get("BTC_RULES_FILE") or "/workspace/wallstreet-skill/kb/rules.md")
RULES_REVISION = "2026-11-23/R92"
RULES_SHA256 = "2be86ca8a1ce1eca75f0baaddad6aa29c37a9c32cb87da7f21f5f41363968fc9"
RULES_MTIME = "2026-11-23 21:44"
RULES_STALE_TEXT = "規則庫已更新，待同步"
RULES_APPLIED = ["R03", "R25", "R79", "R80", "R81", "R87", "R88", "R89", "R90", "R92"]
RULES_NOT_APPLIED = {"R82": "執行紀律", "R83": "執行紀律", "R84": "不是 BTC", "R86": "不是 BTC",
                     "R85": "中長線", "R91": "中長線", "待確認": "報告第 5 節 11 項不編碼"}
REGIME_ER_LEN = 10       # R90：近 10 根日線的效率比，先分震盪或趨勢
REGIME_ER_TREND = 0.30   # 效率比 >= 0.30 算趨勢，以下算震盪
RSI_LEN, BOLL_LEN, BOLL_K = 14, 20, 2.0  # R90：預設參數，不擬合
DUMP_LOOKBACK = 10       # R25：近 10 根日線高點
DUMP_NO_CHASE_PCT = 0.11 # R25：已經跌約 11% 且還貼著低點，不再追空
LEFT_MAX_TRIES = 2       # R88：左側最多試兩次
FALSE_BREAK_BARS = 3     # R80：最近 3 根已收日線內的插針假突破
DIV_LOOKBACK = 40        # R81：背離只看近 40 根日線的擺動點
PARABOLIC_BARS = 25      # R89：25 根日線內
PARABOLIC_RISE = 0.35    # R89：低到高漲 35% 以上算拋物線主升
MOTHER_BODY_X = 2.0      # R89／R63：大實體＝前 20 根實體中位數的 2 倍
R89_ACTIVE_BARS = 30     # R89：母線在近 30 根日線內才提
RULES_MAX_SENTENCES = 3  # /btc 短線段新增的規則句最多 3 句，超過就報錯
R80_CONFIRM_CLOSES = 2   # R80：熊市裡往上的事件，n＝連續收在該價位上方的已收日線根數；n = 1 觀望、n ≥ 2 重算
RANGE_MIN_ATR = 1.0      # 重算後主區間最小寬度 W＝max(1.0 × 日線 ATR14, 下沿 × 3%)
RANGE_MIN_PCT = 0.03
ATR_LEN = 14
NEAR_FRAC = 0.30         # 「靠近下沿／上沿」門檻＝區間寬度 × 30%；上沿另以 NEAR_UPPER 為上限
LEGACY_LEVELS = [INIT_FIB_0886, INIT_VAL, INIT_FIB_0786, INIT_RANGE_HIGH]  # 11/17 那套的已知價位

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
        try:
            h1 = _fetch_binance("1h", 200, now_ms)
        except Exception as e:
            h1 = []
            errors.append(f"binance 1h: {type(e).__name__}: {e}")
        if skip_ticker:
            px = (h4[-1]["close"] if h4 else d1[-1]["close"])
        else:
            try:
                px = _fetch_binance_ticker()
            except Exception:
                px = (h4[-1]["close"] if h4 else d1[-1]["close"])
        return {"source": "binance_spot", "source_note": "幣安現貨 api.binance.com /api/v3/klines",
                "klines": {"4h": h4, "1d": d1, "1w": w1, "1h": h1}, "price": px, "errors": errors}
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
        out["last_cross"] = {"type": cross[0], "time": fmt_time(taipei_of(c[cross[1]]["open_time"])),
                             "age": len(c) - 1 - cross[1]}
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
    ac = bool(zone[1] is not None and price >= zone[1])
    if price <= hi:
        return {"kind": "edge", "level": hi, "former_low": former, "need_reclaim": False, "above_confirm": ac}
    if above:
        # 現價還在確認帶上緣下方：最近的壓力是確認帶上緣，原主區間下緣是站上之後的下一道壓力
        ic = bool(zone[1] is not None and price < zone[1] and zone[1] < min(above))
        return {"kind": "overhead", "level": min(above), "former_low": True,
                "need_reclaim": bool(sf.get("came_from_above")), "above_confirm": ac,
                "in_confirm": ic, "near_level": float(zone[1]) if ic else min(above)}
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
    # 分批價是進場價位，不是壓力：現價在區間裡時，上方的分批價不拿來當壓力
    batch = {float(round(float(x))) for x in (rng.get("lower_supports") or []) if x is not None}
    above = [x for x in lv if x > price and not (price >= float(rng.get("low") or 0) and x in batch)]
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
    if price >= hi - upper_band(rng):
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
    # R88：回踩的停損至少等 1 小時實體收破框下緣；影線不算。沒有 1h 資料就是 None。
    h1_rows = market["klines"].get("1h")
    sf["h1_body_break"] = None
    if sf["ok"] and h1_rows and last_1d:
        sf["h1_body_break"] = any(r["close"] < zone[0] for r in closed_bars(h1_rows)
                                  if r["open_time"] > last_1d["close_time"])

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
    if pos == "upper_zone" or (last_4h and last_4h["high"] >= hi - upper_band(rng)) or (last_1d and last_1d["high"] >= hi - upper_band(rng)):
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


# ---------------------------------------------------------------------------
# 規則庫 2026-11-23（R03、R25、R79、R80、R81、R87、R88、R89、R90、R92）
# ---------------------------------------------------------------------------
def rules_sync_check(prev_state: dict | None = None) -> dict:
    """比對 rules.md 的 hash。一致＝synced；不一致＝stale（規則庫已更新，待同步）；讀不到＝missing。"""
    out = {"revision": RULES_REVISION, "sha256_expected": RULES_SHA256, "mtime_expected": RULES_MTIME,
           "file": str(RULES_FILE)}
    try:
        raw = RULES_FILE.read_bytes()
        mt = datetime.fromtimestamp(RULES_FILE.stat().st_mtime, TAIPEI).strftime("%Y-%m-%d %H:%M")
    except OSError:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # 備援發布（公開 repo 的 Actions）本來就沒有規則庫：註明沿用版本，不寫「待確認」
            out.update(ok=True, status="no_rules_file_ci", sha256_now=None, mtime_now=None,
                       flag=None, note=f"這個環境沒有規則庫，沿用 {RULES_REVISION}")
        else:
            out.update(ok=False, status="missing", sha256_now=None, mtime_now=None,
                       flag="規則庫讀不到，無法比對，待確認")
        return out
    sha = hashlib.sha256(raw).hexdigest()
    ok = sha == RULES_SHA256
    out.update(ok=ok, status="synced" if ok else "stale", sha256_now=sha, mtime_now=mt,
               flag=None if ok else RULES_STALE_TEXT)
    seen = (prev_state or {}).get("rules_sha256_seen")
    out["changed_since_last_run"] = bool(seen and seen != sha)
    return out


_RULES_STATUS: dict = {}


def write_rules_internal(out_dir: Path, now: datetime, rs: dict, rv: dict, errs: list[str]) -> None:
    """內部用：規則同步狀態、規則判斷、用到哪幾句。網站、發布程式、中長線模型都不讀這個檔。"""
    data = {"generated_at": fmt_time(now), "rules_revision": RULES_REVISION, "rules_sync": rs,
            "rules": rv, "rules_sentence_check": {"ok": not errs, "max": RULES_MAX_SENTENCES, "errors": errs}}
    try:
        (out_dir / "short_model_internal.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        pass


def append_rules_log(out_dir: Path, now: datetime, rs: dict) -> None:
    line = (f"{fmt_time(now)} 規則 {rs['revision']} {rs['status']}"
            + ("" if rs["ok"] else f" {rs['flag']} 現在 hash {str(rs.get('sha256_now'))[:12]} 修改時間 {rs.get('mtime_now')}")
            + "\n")
    try:
        with open(out_dir / "rules_sync.log", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _rsi(closes: list[float], n: int = RSI_LEN) -> list[float | None]:
    """Wilder RSI，與 closes 對齊。"""
    if len(closes) < n + 1:
        return [None] * len(closes)
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    val = lambda g, l: 100.0 if l == 0 else 100.0 - 100.0 / (1 + g / l)
    out: list[float | None] = [None] * n + [val(ag, al)]
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out.append(val(ag, al))
    return out


def cycle_view(ma_d: dict | None, cd1: list[dict] | None = None) -> str | None:
    """R80 的大週期。熊市：已從一年高點跌超過 20%（R04 技術性熊市），或收在 MA120 下方且均線往下。
    牛市：MA120 往上、收在上方、離一年高點不到 20%。其他不套濾網。"""
    if not ma_d or not ma_d.get("ok") or ma_d.get("change_5bars") is None:
        return None
    top = max(r["high"] for r in cd1[-365:]) if cd1 else None
    dd = (top - ma_d["close"]) / top if top else 0.0
    if dd >= DROP_BEAR_PCT or (ma_d["price_below"] and ma_d["change_5bars"] < 0):
        return "bear"
    if ma_d["change_5bars"] > 0 and not ma_d["price_below"] and dd < DROP_BEAR_PCT:
        return "bull"
    return None


def regime_view(cd1: list[dict]) -> dict:
    """R90：先分震盪或趨勢，震盪時才用 RSI14、布林 20 當高低參考；趨勢裡只提醒鈍化，不逆勢用。"""
    closes = [r["close"] for r in cd1]
    if len(closes) < max(REGIME_ER_LEN + 1, BOLL_LEN, RSI_LEN + 1):
        return {"ok": False}
    seg = closes[-REGIME_ER_LEN - 1:]
    net = seg[-1] - seg[0]
    path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    er = abs(net) / path if path else 0.0
    kind = "trend" if er >= REGIME_ER_TREND else "range"
    direction = "down" if net < 0 else "up"
    rsi = _rsi(closes)[-1]
    win = closes[-BOLL_LEN:]
    mid = statistics.mean(win)
    sd = statistics.pstdev(win)
    up, dn = mid + BOLL_K * sd, mid - BOLL_K * sd
    last = closes[-1]
    signal = None
    if kind == "range":
        if rsi >= 70 or last >= up:
            signal = "overbought"
        elif rsi <= 30 or last <= dn:
            signal = "oversold"
    elif direction == "down" and (rsi <= 30 or last <= dn):
        signal = "trend_oversold"
    elif direction == "up" and (rsi >= 70 or last >= up):
        signal = "trend_overbought"
    return {"ok": True, "kind": kind, "direction": direction, "er": round(er, 3), "rsi14": round(rsi, 1),
            "boll20": [round(dn, 1), round(mid, 1), round(up, 1)], "signal": signal}


def false_break_view(cd1: list[dict], rng: dict, birth: str | None) -> dict | None:
    """R80：框成立後最近 3 根已收日線，影線插出上沿（下沿）但收盤收回區間＝假突破（假跌破）。"""
    lo, hi = rng.get("low"), rng.get("high")
    if lo is None or hi is None or not cd1:
        return None
    recent = [r for r in cd1[-FALSE_BREAK_BARS:] if birth is None or bar_date(r) > birth]
    if not recent or not (lo <= cd1[-1]["close"] <= hi):
        return None
    # 要從區間裡面往外插：開盤在區間內、影線出去、收盤收回；從外面跌回來的那根不算
    ups = [r for r in recent if r["open"] <= hi and r["high"] > hi and r["close"] <= hi]
    dns = [r for r in recent if r["open"] >= lo and r["low"] < lo and r["close"] >= lo]
    if ups and (not dns or ups[-1]["open_time"] >= dns[-1]["open_time"]):
        return {"side": "up", "level": hi, "extreme": max(r["high"] for r in ups), "bar": bar_date(ups[-1])}
    if dns:
        return {"side": "down", "level": lo, "extreme": min(r["low"] for r in dns), "bar": bar_date(dns[-1])}
    return None


def left_tries_view(cd1: list[dict], rng: dict, birth: str | None, price: float) -> int:
    """R88：框成立後，價格進到下沿分批區（<= 價值區下沿）幾次；連續幾根算同一次，現價正在裡面也算。"""
    val = rng.get("val") or rng.get("fib_0886") or rng.get("low")
    if val is None:
        return 0
    tries, inside = 0, False
    after = [r for r in cd1 if birth is None or bar_date(r) > birth]
    for r in after:
        touch = r["low"] <= val
        if touch and not inside:
            tries += 1
        inside = touch
    if price <= val and not inside:
        tries += 1
    return tries


def dump_view(cd1: list[dict], price: float) -> dict:
    """R25 補丁／R92 恐慌：從近 10 根日線高點跌了約 11% 以上、而且還貼著低點。"""
    look = cd1[-DUMP_LOOKBACK:]
    if not look:
        return {"on": False}
    top = max(r["high"] for r in look)
    low = min(min(r["low"] for r in look), price)
    drop = (top - price) / top if top else 0.0
    return {"on": bool(drop >= DUMP_NO_CHASE_PCT and price <= low * 1.03), "drop": round(drop, 4),
            "top": top, "low": low}


def divergence_view(cd1: list[dict]) -> dict | None:
    """R81：日線 RSI 背離（只用來順勢平倉）。最近兩個擺動高點價高、RSI 低＝頂背離；低點反之＝底背離。"""
    if len(cd1) < DIV_LOOKBACK + RSI_LEN:
        return None
    rsi = _rsi([r["close"] for r in cd1])
    n = len(cd1)
    start = n - DIV_LOOKBACK
    sw = _swings(cd1[start:], 3, 2)
    idx = {id(r): start + k for k, r in enumerate(cd1[start:])}
    highs = [idx[id(b)] for t, b in sw if t == "H"]
    lows = [idx[id(b)] for t, b in sw if t == "L"]
    found = []
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if n - 1 - b <= 10 and cd1[b]["high"] > cd1[a]["high"] and rsi[b] is not None and rsi[a] is not None and rsi[b] < rsi[a]:
            found.append(("top", b))
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if n - 1 - b <= 10 and cd1[b]["low"] < cd1[a]["low"] and rsi[b] is not None and rsi[a] is not None and rsi[b] > rsi[a]:
            found.append(("bottom", b))
    if not found:
        return None
    kind, b = max(found, key=lambda x: x[1])
    return {"kind": kind, "bar": bar_date(cd1[b])}


def _body(r: dict) -> tuple[float, float]:
    return min(r["open"], r["close"]), max(r["open"], r["close"])


def double_inside_view(cd1: list[dict]) -> dict | None:
    """R89（定義見 R63）：拋物線主升後，大實體日線後面至少兩根小實體被包在它的實體內。
    只看母線在近 30 根日線內；回報下破、回抽實體次數、重心上移。"""
    n = len(cd1)
    if n < PARABOLIC_BARS + 25:
        return None
    best = None
    for i in range(max(PARABOLIC_BARS, 20, n - R89_ACTIVE_BARS), n - 2):
        m = cd1[i]
        b0, b1 = _body(m)
        med = statistics.median(abs(x["close"] - x["open"]) for x in cd1[i - 20:i])
        if not med or b1 - b0 < MOTHER_BODY_X * med:
            continue
        w = cd1[i - PARABOLIC_BARS + 1:i + 1]
        hk = max(range(len(w)), key=lambda k: w[k]["high"])
        lk = min(range(hk + 1), key=lambda k: w[k]["low"])
        rise = w[hk]["high"] / w[lk]["low"] - 1 if w[lk]["low"] else 0.0
        if rise < PARABOLIC_RISE or len(w) - 1 - hk > 3:
            continue
        j, group = i + 1, []
        while j < n and b0 <= _body(cd1[j])[0] and _body(cd1[j])[1] <= b1:
            group.append(cd1[j])
            j += 1
        if len(group) >= 2:
            best = (i, group, j, rise)
    if not best:
        return None
    i, group, j, rise = best
    b0, b1 = _body(cd1[i])
    g_lo = min(_body(r)[0] for r in group)
    g_hi = max(_body(r)[1] for r in group)
    rest = cd1[j:]
    status, retests = "waiting", 0
    brk = next((k for k, r in enumerate(rest) if r["close"] < g_lo or r["close"] > g_hi), None)
    if brk is not None:
        if rest[brk]["close"] > g_hi:
            status = "broke_up"
        else:
            status = "broke_down"
            after = rest[brk + 1:]
            inside = False
            for r in after:
                touch = r["high"] >= b0
                if touch and not inside:
                    retests += 1
                inside = touch
            last3 = cd1[-3:]
            if len(after) >= 3 and all(b0 <= r["close"] <= b1 for r in last3) \
                    and last3[0]["close"] < last3[1]["close"] < last3[2]["close"]:
                status = "absorbed"
    return {"mother": bar_date(cd1[i]), "body": [b0, b1], "group": [g_lo, g_hi], "inside_bars": len(group),
            "rise": round(rise, 3), "status": status, "retests": retests}


def rules_view(market: dict, rng: dict | None, check: dict, ma_d: dict | None) -> dict:
    cd1 = closed_bars(market["klines"]["1d"])
    price = market["price"]
    birth = check.get("recompute_bar")
    out = {"revision": RULES_REVISION, "applied": RULES_APPLIED, "not_applied": RULES_NOT_APPLIED,
           "cycle": cycle_view(ma_d, cd1), "regime": regime_view(cd1), "dump": dump_view(cd1, price),
           "divergence": divergence_view(cd1), "double_inside": double_inside_view(cd1),
           "false_break": None, "left_tries": 0, "left_exhausted": False}
    if rng and rng.get("low") is not None:
        out["false_break"] = false_break_view(cd1, rng, birth)
        out["left_tries"] = left_tries_view(cd1, rng, birth, price)
        out["left_exhausted"] = out["left_tries"] > LEFT_MAX_TRIES
    return out


_CN = {1: "一", 2: "兩", 3: "三", 4: "四", 5: "五"}


def r03_sentence(macd_d: dict | None, macd_w: dict | None) -> str | None:
    """R03 補丁：金叉要先看零軸與大週期。只在 /btc 段用，/report 不動。"""
    if not macd_d or not macd_d.get("ok") or macd_w is None:
        return None
    line = macd_d.get("line") or 0
    cross = macd_d.get("last_cross") or {}
    if cross.get("type") != "金叉":
        return None
    w_line = macd_w.get("line") if macd_w.get("ok") else None
    age = cross.get("age")
    if line >= 0 and w_line is not None and w_line < 0:
        return "週線 MACD 還在零軸下，大跌後的零軸上金叉常常就是反彈的頂，先別追。"
    if line < 0 and age is not None and age <= 1:
        return "水下金叉剛出來的頭兩根是反彈最強的時候，手上的多單可以趁這裡減倉。"
    return None


def rules_candidates(st: str, rv: dict | None, check: dict, rng: dict | None,
                     macd_d: dict | None = None, macd_w: dict | None = None) -> list[tuple[int, str, str, str]]:
    """當天盤勢觸發到的規則句：(優先順序, 位置 pre/post, 規則, 句子)。數字越小越重要。
    每句都要跟今天的位置有關才出現；不寫數字參數，避免和價位重複。"""
    if not rv:
        return []
    out: list[tuple[int, str, str, str]] = []
    cyc = rv.get("cycle")
    reg = rv.get("regime") or {}
    fb = rv.get("false_break")
    dump = rv.get("dump") or {}
    sf = (check or {}).get("stand_firm") or {}
    pv_kind = ((check or {}).get("pressure") or {}).get("kind")
    above_hi = st == "upper" and pv_kind in ("above_edge", "overhead")
    px = (check or {}).get("price")
    val = (rng or {}).get("val") or (rng or {}).get("fib_0886") or (rng or {}).get("low")
    near_batch = st == "lower" or (st == "mid" and px is not None and val is not None
                                   and float(px) <= float(val) + NEAR_UPPER)
    left_ok = st == "lower" and not rv.get("left_exhausted") and not dump.get("on")
    entry = False  # R79：結論可以進場（分批、跟進、假突破反向單）時才提倉位
    # R88：站穩後回踩的停損、左側最多兩次
    if st == "stand_firm":
        out.append((1, "pre", "R88", "突破確認才跟進主倉，回抽守住框可以再加一次；回踩的停損要等 1 小時實體收破框下緣，影線不算。"))
        entry = True
    elif st in ("stand_firm_retest", "stand_firm_lost"):
        hb = sf.get("h1_body_break")
        if hb is True:
            out.append((1, "pre", "R88", "1 小時已經實體收破框下緣，手上的多單先停損。"))
        elif hb is False and st == "stand_firm_lost":
            out.append((1, "pre", "R88", "1 小時還沒實體收破框下緣，影線不算，先不急著砍。"))
        else:
            out.append((1, "pre", "R88", "回踩的停損要等 1 小時實體收破框下緣才算，影線不算。"))
    elif left_ok:
        t = rv.get("left_tries") or 0
        out.append((1, "pre", "R88", "左側只開底倉，最多試兩次" + ("，這已經是第二次，不對就停。" if t == LEFT_MAX_TRIES else "，不對就停。")))
        entry = True
    # R80＋R87：假突破不追、高倍等完全脫離區間，合成一句
    if fb and fb["side"] == "up" and cyc == "bear" and st in ("mid", "upper", "lower"):
        out.append((2, "pre", "R80/R87", "上沿剛被插針假突破又收回，大週期還在熊市，不追多，反抽上沿附近可以低倍空，"
                    f"停損放在 {fmt_px(fb['extreme'])} 上方；高倍要等日線完全脫離區間才追。"))
        entry = True
    elif fb and fb["side"] == "down" and cyc == "bull" and st in ("mid", "upper", "lower"):
        out.append((2, "pre", "R80/R87", "下沿剛被插針假跌破又收回，大週期偏多，不追空，下沿附近可以低倍做多，"
                    f"停損放在 {fmt_px(fb['extreme'])} 下方；高倍要等日線完全脫離區間才追。"))
        entry = True
    elif st == "upper" and cyc == "bear":
        where = "上沿上方先當假突破看" if above_hi else "靠近上沿先防假突破"
        out.append((2, "pre", "R80/R87", f"大週期還在熊市，{where}，不追多；高倍要等日線完全脫離區間、站穩以後才追。"))
    elif st == "upper":
        out.append((2, "pre", "R87", "高倍要等日線完全脫離區間、站穩以後才追，停損放回區間內。"))
    elif st in ("stand_firm", "stand_firm_retest") and cyc == "bear":
        out.append((2, "pre", "R80", "大週期還在熊市，這種突破先防假突破，多單只用低倍小倉。"))
    elif st == "invalid" and cyc == "bull":
        out.append((2, "pre", "R80", "大週期還在牛市，下沿跌破先當假的看，不追空。"))
    elif st == "mid" and reg.get("kind") == "range":
        out.append((2, "pre", "R87", "區間還在震盪，不要來回做；高倍要等日線完全脫離區間再追。"))
    # R25 補丁：已經大跌約 11% 不再追空
    if dump.get("on") and st in ("invalid", "lower", "stand_firm_lost", "no_range"):
        out.append((3, "pre", "R25", "已經從近期高點跌了一成多，不再追空；等止跌、走出一段震盪，再找反彈的次高點空。"))
    # R92：分批不預掛滿，只在現價靠近分批區時寫
    if near_batch and not rv.get("left_exhausted"):
        if dump.get("on"):
            out.append((4, "pre", "R92", "恐慌時價值區下沿也可能直接被打穿。"))
        else:
            out.append((4, "pre", "R92", "分批不要一次先掛滿，恐慌時可能直接打穿，看到止跌反應再接。"))
    # R89 拋物線後日線雙孕線
    di = rv.get("double_inside") or {}
    s = di.get("status")
    r89 = {
        "waiting": "拋物線上漲後日線出現雙孕線，等收盤破小實體群哪一邊再跟。",
        "broke_up": "拋物線上漲後的日線雙孕線往上破，空單不做。",
        "absorbed": "價格一直圍著雙孕線實體、重心往上移，拋壓被吸收，空單放棄。",
    }.get(s)
    if s == "broke_down":
        n = di.get("retests") or 0
        if n == 0:
            r89 = "拋物線上漲後的日線雙孕線已經往下破，空單順勢拿；跌到位以後，回到雙孕線實體附近可以再空。"
        elif n <= 2:
            r89 = f"雙孕線往下破之後已經回抽實體{_CN[n]}次，前兩次回抽都可以空，第三次就不做。"
        else:
            r89 = "雙孕線實體已經被回測第三次以上，結構可能變了，不再空。"
    if r89:
        out.append((5, "post", "R89", r89))
    # R81 背離只順勢平倉：手上有對應方向的單才有關
    dv = (rv.get("divergence") or {}).get("kind")
    if dv == "top" and st in ("upper", "stand_firm", "stand_firm_retest"):
        out.append((6, "post", "R81", "日線出現頂背離，只拿來讓手上的多單減倉，不拿來摸頂做空。"))
    elif dv == "bottom" and st in ("lower", "invalid", "no_range", "stand_firm_lost"):
        out.append((6, "post", "R81", "日線出現底背離，只拿來讓手上的空單減倉，不拿來抄底。"))
    # R03 補丁
    r03 = r03_sentence(macd_d, macd_w)
    if r03:
        out.append((7, "post", "R03", r03))
    # R90：RSI／布林只在相關位置提
    sig = reg.get("signal")
    near_top = st == "upper"
    if sig == "overbought" and near_top:
        out.append((8, "post", "R90", "現在是震盪盤，日線 RSI 或布林通道已經到超買那一側，可以當上沿附近減倉的參考。"))
    elif sig == "oversold" and near_batch:
        out.append((8, "post", "R90", "現在是震盪盤，日線 RSI 或布林通道已經到超賣那一側，可以當下沿附近分批的參考。"))
    elif sig == "trend_oversold" and st in ("lower", "invalid", "no_range", "stand_firm_lost"):
        out.append((8, "post", "R90", "現在是下跌趨勢，RSI 超賣會一直鈍化，不拿來抄底。"))
    elif sig == "trend_overbought" and st in ("upper", "stand_firm", "stand_firm_retest"):
        out.append((8, "post", "R90", "現在是上漲趨勢，RSI 超買會一直鈍化，不拿來摸頂。"))
    # R79：結論可以進場或追進時才提倉位
    if entry:
        out.append((9, "post", "R79", "單筆別超過總資金一成，大多數時間空倉也沒關係，賺了先提出來。"))
    return out


def rules_sentences(st: str, rv: dict | None, check: dict, rng: dict | None,
                    macd_d: dict | None = None, macd_w: dict | None = None) -> tuple[str, str, list[str]]:
    """依優先順序最多取 RULES_MAX_SENTENCES 句。回傳 (接在結論後, 接在 MACD 後, 用到的規則)。"""
    cand = sorted(rules_candidates(st, rv, check, rng, macd_d, macd_w), key=lambda x: x[0])
    pick = cand[:RULES_MAX_SENTENCES]
    if rv is not None:
        rv["sentences_all"] = [c[3] for c in cand]
        rv["sentences_used"] = [c[2] for c in pick]
        rv["sentences_dropped"] = [c[2] for c in cand[RULES_MAX_SENTENCES:]]
    pre = "".join(c[3] for c in pick if c[1] == "pre")
    post = "".join(c[3] for c in pick if c[1] == "post")
    return pre, post, [c[2] for c in pick]


def rules_sentence_errors(text: str, rv: dict | None) -> list[str]:
    """自檢：輸出裡實際出現的規則句超過 RULES_MAX_SENTENCES 就報錯。"""
    if not rv:
        return []
    n = sum(text.count(x) for x in rv.get("sentences_all") or [])
    if n > RULES_MAX_SENTENCES:
        return [f"執行失敗：/btc 短線段新增規則句 {n} 句，超過上限 {RULES_MAX_SENTENCES} 句"]
    return []


# ---------------------------------------------------------------------------
# 11/26 Manager 08:01：R80 收回還沒確認、主區間最小寬度、分批價和壓力分開、靠近門檻跟寬度成比例
# ---------------------------------------------------------------------------
def r80_reclaim_gate(check: dict, market: dict, ma_d: dict | None) -> dict | None:
    """R80：大週期是熊市時，往上的事件（收回上方壓力、收在上沿上方、站上確認帶）先當假突破看。
    L＝事件的價位（收回壓力＝那道壓力；脫離上沿＝上沿；站穩＝確認帶下緣）。
    n＝從最新一根已收日線往回數，連續收在 L 上方的根數（站穩用 >=，其他用 >）。
    n = 1 → 收回還沒確認：不重算主區間，方向最多觀望／不追。
    n ≥ 2（R80_CONFIRM_CLOSES）→ 延續確認（第二根也收在上方；中間回踩到 L 附近沒收破也算回踩不破）→ 照原流程往上重算。
    n = 0 只會在事件是更早的日線觸發、最新一根已收回 L 下方時出現，同樣不重算（等同假突破）。
    程式寫法：n >= R80_CONFIRM_CLOSES 才放行；其餘（n = 1，或 n = 0）攔下。
    牛市或大週期不明確：不攔，照原流程。"""
    evs = check.get("events") or []
    if any(e.get("type") in ("depart_below", "stop_break") for e in evs):
        return None
    ups = [e for e in evs if e.get("type") in ("reclaim_overhead", "depart_above", "stand_firm")]
    if not ups:
        return None
    cd1 = closed_bars(market["klines"]["1d"])
    cyc = cycle_view(ma_d, cd1)
    if cyc != "bear" or not cd1:
        return None
    lvl = lambda e: float(e["zone"][0]) if e["type"] == "stand_firm" else float(e["level"])
    e = max(ups, key=lvl)
    L = lvl(e)
    n = 0
    for r in reversed(cd1):
        if (r["close"] >= L) if e["type"] == "stand_firm" else (r["close"] > L):
            n += 1
        else:
            break
    if n >= R80_CONFIRM_CLOSES:
        return None
    last = cd1[-1]
    return {"level": L, "event": e["type"], "close": last["close"], "bar": bar_date(last),
            "closes_above": n, "need": R80_CONFIRM_CLOSES, "cycle": cyc,
            "rule": "熊市裡往上的事件，要連續兩根已收日線收在該價位上方才算延續確認；只有一根算收回還沒確認，不重算主區間"}


def _atr(cd1: list[dict], n: int = ATR_LEN) -> float | None:
    """日線 ATR14（Wilder），只用已收完日線。"""
    if len(cd1) < n + 1:
        return None
    tr = [max(cd1[i]["high"] - cd1[i]["low"], abs(cd1[i]["high"] - cd1[i - 1]["close"]),
              abs(cd1[i]["low"] - cd1[i - 1]["close"])) for i in range(1, len(cd1))]
    a = sum(tr[:n]) / n
    for t in tr[n:]:
        a = (a * (n - 1) + t) / n
    return a


def range_min_width(market: dict, lo: float) -> tuple[float, float | None]:
    """最小寬度 W＝max(RANGE_MIN_ATR × 日線 ATR14, RANGE_MIN_PCT × 下沿)。"""
    atr = _atr(closed_bars(market["klines"]["1d"]))
    return max(RANGE_MIN_ATR * atr if atr else 0.0, RANGE_MIN_PCT * float(lo)), atr


def enforce_min_width(rng: dict, market: dict, overhead: list[float]) -> dict:
    """重算出來的區間寬度不到 W：上沿往上取「下沿＋W」以上最近的已知壓力。
    候選：重算時的上沿參考價、跌破後記下的壓力、11/17 那套的 61,174／61,800／64,134／67,000、
    近 10 根日線高點與近 120 根日線擺動高點（落到千位）。都沒有才取下沿＋W 往上的千位整數。"""
    rng = dict(rng)
    lo, hi = float(rng["low"]), float(rng["high"])
    w, atr = range_min_width(market, lo)
    info = {"atr14": round(atr, 1) if atr else None, "min_width": round(w, 1), "width_before": round(hi - lo, 1),
            "formula": f"W = max({RANGE_MIN_ATR} × ATR14, {RANGE_MIN_PCT} × 下沿)", "widened": False}
    if hi - lo >= w:
        rng["min_width"] = info
        return rng
    target = lo + w
    cd1 = closed_bars(market["klines"]["1d"])
    cands = {float(round(float(x))) for x in list(rng.get("upper_refs") or []) + list(overhead) + LEGACY_LEVELS}
    for bar in cd1[-10:]:
        cands.add(float(math.floor(bar["high"] / UPPER_ROUND) * UPPER_ROUND))
    for typ, bar in _swings(cd1[-120:]):
        if typ == "H":
            cands.add(float(math.floor(bar["high"] / UPPER_ROUND) * UPPER_ROUND))
    above = sorted(x for x in cands if x >= target)
    new_hi = above[0] if above else float(math.ceil(target / UPPER_ROUND) * UPPER_ROUND)
    info.update(widened=True, high_before=hi, high_after=new_hi, target=round(target, 1), candidates=above[:6])
    rng.update(high=new_hi, stand_firm_zone=[new_hi, new_hi + STAND_FIRM_BAND],
               upper_refs=sorted({float(x) for x in (rng.get("upper_refs") or [])} | {new_hi}),
               high_former_low=new_hi in {float(x) for x in overhead}, min_width=info)
    rng["note"] = (rng.get("note") or "") + f"；寬度 {fmt_px(hi - lo)} 不到最小寬度 {fmt_px(w)}，上沿改取 {fmt_px(new_hi)}"
    return rng


def upper_band(rng: dict) -> float:
    """「靠近上沿」門檻：區間寬度 × NEAR_FRAC，最多 NEAR_UPPER。"""
    return min(float(NEAR_UPPER), NEAR_FRAC * (float(rng["high"]) - float(rng["low"])))


def near_lower_edge(price, rng: dict) -> bool:
    """「靠近下沿」：現價離下沿不到區間寬度 × NEAR_FRAC。分批區（<= 價值區下沿）裡但離下沿還遠，就寫「進到分批區」。"""
    if price is None:
        return True
    return float(price) - float(rng["low"]) <= NEAR_FRAC * (float(rng["high"]) - float(rng["low"]))


def lower_word(check: dict | None, rng: dict) -> str:
    return "已經靠近下沿" if near_lower_edge((check or {}).get("price"), rng) else "已經進到分批區"


def level_role_errors(rng: dict, sp: dict, price: float, *texts: str) -> list[str]:
    """自檢（中止）：分批價同時是壓力；不靠近卻寫「已經靠近下沿／上沿」；重算出來的區間比最小寬度窄。"""
    errs = []
    lo, hi = float(rng["low"]), float(rng["high"])
    batch = {float(round(float(x))) for x in (rng.get("lower_supports") or []) if x is not None}
    if price >= lo:
        p = sp.get("pressure")
        if p is not None and float(round(float(p))) in batch:
            errs.append(f"執行失敗：{fmt_px(p)} 同時是分批價和壓力")
        near, nxt = text_pressures(*texts)
        for x in near + nxt:
            if float(x.replace(",", "")) in batch:
                errs.append(f"執行失敗：文字把分批價 {x} 寫成壓力")
    for t in texts:
        if "已經靠近下沿" in t and not near_lower_edge(price, rng):
            errs.append("執行失敗：現價離下沿超過區間寬度的門檻，卻寫成靠近下沿")
        if "已經靠近上沿" in t and not (0 <= hi - price <= upper_band(rng)):
            errs.append("執行失敗：現價離上沿超過區間寬度的門檻，卻寫成靠近上沿")
    mw = rng.get("min_width") or {}
    if mw.get("min_width") and hi - lo < float(mw["min_width"]) - 0.5:
        errs.append(f"執行失敗：主區間寬度 {fmt_px(hi - lo)} 小於最小寬度 {fmt_px(mw['min_width'])}")
    return list(dict.fromkeys(errs))


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


def text_pressures(*texts: str) -> tuple[list[str], list[str]]:
    """(文字裡寫成「壓力 X」的數字, 寫成「下一道壓力 X」的數字)。"""
    near, nxt = [], []
    for t in texts:
        nxt += re.findall(r"下一道壓力\s*(\d[\d,]*)", t)
        near += re.findall(r"(?<!下一道)壓力\s*(\d[\d,]*)", t)
    return near, nxt


def text_pressure_errors(sp: dict, *texts: str, levels: list[float] | None = None) -> list[str]:
    """短線文字、關鍵價位裡寫成「壓力」的數字，必須是 levels 那一檔；「下一道壓力」必須是它上方最近的已知價位。"""
    errs = []
    want = fmt_px(sp["pressure"]) if sp.get("pressure") is not None else None
    near, nxt = text_pressures(*texts)
    for x in near:
        if x != want:
            errs.append(f"文字把 {x} 寫成壓力，但 levels 的壓力是 {want or '無'}")
    if nxt:
        above = [v for v in (levels or []) if sp.get("pressure") is not None and v > sp["pressure"]]
        want2 = fmt_px(min(above)) if above else None
        for x in nxt:
            if x != want2:
                errs.append(f"文字把 {x} 寫成下一道壓力，但它不是 levels 壓力上方最近的已知價位")
    return errs


def align_pressure_to_text(sp: dict, price: float, *texts: str) -> dict | None:
    """文字和 levels 的壓力對不上時，以文字的壓力為準重算 levels 那行（支撐不動）。文字沒寫壓力就不列壓力。"""
    near, _ = text_pressures(*texts)
    vals = sorted({float(x.replace(",", "")) for x in near})
    above = [v for v in vals if v > price]
    if len(above) > 1:
        return None
    return {"supports": [x for x in sp["supports"] if not above or x < above[0]],
            "pressure": above[0] if above else None}


def bias_line(check: dict, rng: dict, ma_d: dict) -> tuple[str, str, str]:
    """回傳 (方向, 目標句, 失效)，供 levels／摘要使用，不輸出成摘要行。"""
    st = voice_state(check, rng)
    stop = fmt_px(rng["final_stop"]) if rng and rng.get("final_stop") is not None else ""
    invalid = f"日線收盤跌破停損{(' ' + stop) if stop else ''}" if st != "invalid" else "這套已作廢，要重算"
    if st == "reclaim_pending":
        lv_ = fmt_px(check["reclaim_pending"]["level"])
        return ("觀望", "日線只收回一根，大週期還在熊市，先當假突破看，不追", f"日線收回 {lv_} 下方就當假突破")
    if st == "stand_firm":
        return ("偏多", "已經突破並站上框，實體還在框上面就做多", "實體跌回框裡就認錯停損")
    if st == "stand_firm_retest":
        return ("偏多", "現價回到框裡回測，守住才繼續偏多，先不追", "實體跌回框下就認錯停損")
    if st == "stand_firm_lost":
        return ("觀望", "現價跌回框下，這是回測、框上沒守住，先不做多", "日線收在框下就不算站穩")
    if st == "invalid":
        return ("觀望", "這套已作廢，認錯離場", invalid)
    if st == "lower":
        return ("偏多", lower_word(check, rng) + "，可以分批做現貨或小倉多", invalid)
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
    if (check or {}).get("reclaim_pending"):
        return "reclaim_pending"
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


def render_btc_short(now: datetime, market: dict, rng: dict, check: dict, ma_d: dict, ma_w: dict, macd_d: dict, macd_4: dict, scenarios: list[dict],
                     rv: dict | None = None, macd_w: dict | None = None) -> str:
    """一段連貫文字：不加摘要行、不加劇本行，每個價位只出現一次。"""
    price = market["price"]
    st = voice_state(check, rng)
    time_s = fmt_time_text(now)
    r_pre, r_post, _r_used = rules_sentences(st, rv, check, rng, macd_d, macd_w)
    ma = r_pre + ma120_voice(ma_d, "btc")
    macd = macd_voice(macd_d, macd_4, "btc") + r_post
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
    if st == "reclaim_pending":
        pd = check["reclaim_pending"]
        back = check.get("price") is not None and float(check["price"]) < float(pd["level"])
        body = (head + f"日線收回 {fmt_px(pd['level'])} 上方，但大週期還在熊市，只收回一根"
                + ("，現價又回到這個價位下方" if back else "") + "，先當假突破看，不追。"
                "要再一根日線收在這個價位上方，或回踩不破，才算轉強；日線收回下方就當假突破。")
        if abs(float(pd["level"]) - float(rng["high"])) < 0.51:
            body += f"主區間先不重算，下沿 {lo}，停損 {stop}。"
        else:
            body += f"主區間先不重算，還是 {lo} 到 {hi}，停損 {stop}。"
        return (body + ma + macd).strip() + "\n"
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
    if st == "lower" and (rv or {}).get("left_exhausted"):
        loc = lower_word(check, rng) + "，但左側已經試過兩次，這次先不接，等站回來再說"
    elif st == "lower" and ((rv or {}).get("dump") or {}).get("on"):
        loc = "已經跌進下沿分批區，但現在是恐慌急跌，分批先不預掛，看到止跌反應再接"
    elif st == "lower":
        loc = lower_word(check, rng) + "，可以分批做現貨或小倉多"
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
    return px is not None and zone[1] is not None and float(px) >= float(zone[1])


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
    if pv["kind"] == "overhead" and pv.get("in_confirm"):
        body = (head + f"已經站到上沿 {hi} 上方，靠近上方壓力 {confirm}，這裡先減倉，不要追多。"
                f"主區間下沿是 {lo}：靠近 {batch}，再分批現貨或小倉多，停損 {stop}；")
        nxt = fmt_px(pv["level"])
        if pv.get("need_reclaim"):
            body += (f"站上之後的下一道壓力 {nxt}，這裡原本是主區間下沿，跌破後變成壓力；"
                     "日線要由下往上收回這道壓力，才算轉強，還沒收上去以前，中間也不開新單。")
        else:
            body += (f"要突破並站穩這道壓力才算轉強；站上之後的下一道壓力 {nxt}，這裡原本是主區間下沿，跌破後變成壓力；"
                     "還沒站穩以前，中間也不開新單。")
        return body
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
    if st == "reclaim_pending":
        pl = (check or {}).get("reclaim_pending") or {}
        if abs(float(pl.get("level", 0)) - float(rng["high"])) < 0.51:
            return f"主區間 {lo}–{hi}；收回還沒確認；停損 {stop}"
        return f"收回還沒確認 {fmt_px(pl.get('level', 0))}；主區間 {lo}–{hi}；停損 {stop}"
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
        if pv["kind"] == "overhead" and pv.get("in_confirm"):
            return f"上方壓力 {confirm}；下一道壓力 {fmt_px(pv['level'])}；主區間 {lo}–{hi}；分批 {batch}；停損 {stop}"
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
    if st == "reclaim_pending":
        return "日線收回這道價位，但大週期還在熊市、只收回一根，先當假突破看，不追；要再一根日線收在這道價位上方，或回踩不破，才算轉強。"
    if st == "stand_firm":
        return "已經突破並站上框，實體還在框上面就做多；實體跌回框裡就認錯停損，必要時反手。目標先看最近的密集區，不要看太遠。"
    if st == "stand_firm_retest":
        return "日線收在框上，現價回到框裡回測；守住、實體沒跌回框下才繼續偏多，這裡先不追，實體跌回框下就認錯停損。"
    if st == "stand_firm_lost":
        return "現價跌回框下，這是回測、框上沒守住，先不做多；等下一根日線收盤，收回框上才算守住，收在框下就不算站穩。"
    if st == "lower":
        return ("已經靠近下沿" if (check or {}).get("near_lower_edge", True) else "已經進到分批區") + "，可以分批做現貨或小倉多，停損放在已寫的位置。靠近上沿就減倉；要突破並站穩確認帶，才算轉強。"
    if st == "upper":
        pv = (check or {}).get("pressure") or {"kind": "edge"}
        if pv.get("kind") == "overhead" and pv.get("in_confirm"):
            return ("已經站到上沿上方，靠近上方壓力，這裡先減倉，不要追多。"
                    + ("站上之後還有一道原本是主區間下沿的壓力，日線要由下往上收回那道壓力，才算轉強；還沒收上去以前，中間也不開新單。"
                       if pv.get("need_reclaim") else
                       "要突破並站穩這道壓力，才算轉強；站上之後還有一道原本是主區間下沿的壓力；還沒站穩以前，中間也不開新單。"))
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
    # R80：熊市裡只有一根日線往上收，算收回還沒確認，不重算主區間
    _gate = None if force_recompute else r80_reclaim_gate(check, market, ma_d)
    if _gate:
        check["reclaim_pending"] = _gate
        check["events_pending"] = check.get("events") or []
        check["events"] = []
        check["recompute"] = False
        check["departed"] = False
        if check.get("position") == "above":
            check["position"] = "upper_zone"
        check["reason_text"] = (f"日線收盤 {fmt_px(_gate['close'], 1)} 由下往上收回 {fmt_px(_gate['level'])}，"
                                f"但大週期還在熊市、連續收在上方只有 {_gate['closes_above']} 根，算收回還沒確認，主區間先不重算")
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
        new_rng = enforce_min_width(new_rng, market, overhead)
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
        check["after_recompute_note"] = check.get("reason_text")
        check["reason_text"] = ("已重算主區間：" + reason) if not force_recompute else "手動強制重算主區間"

    state["scenarios"] = update_scenario_status(state["scenarios"], check)
    rng = state["main_range"]
    check["near_lower_edge"] = near_lower_edge(market["price"], rng)

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
    macd_w = macd_view(market["klines"]["1w"]) if market["klines"].get("1w") else {"ok": False}
    payload["macd"]["1w"] = macd_w
    rv = rules_view(market, rng, check, ma_d)
    rs = rules_sync_check(state)
    _RULES_STATUS.clear()
    _RULES_STATUS.update(rs)
    append_rules_log(out_dir, now, rs)
    if not rs["ok"]:
        print(f"[規則] {rs['flag']}：程式同步到 {RULES_REVISION}，rules.md 現在 hash {str(rs.get('sha256_now'))[:12]}、修改時間 {rs.get('mtime_now')}",
              file=sys.stderr)
    btc_txt = render_btc_short(now, market, rng, check, ma_d, ma_w, macd_d, macd_4, state["scenarios"], rv=rv, macd_w=macd_w)
    rules_errs = rules_sentence_errors(btc_txt, rv)
    report_txt = render_report(now, market, rng, check, ma_d, macd_d, macd_4)
    slim_txt = render_report_slim(now, market, rng, check, ma_d, macd_d, macd_4)
    forecast_short = "【短線】\n" + btc_txt
    if not forecast_short.endswith("\n"):
        forecast_short += "\n"
    direction, target, invalid = bias_line(check, rng, ma_d)
    if rv.get("left_exhausted") and voice_state(check, rng) == "lower":  # R88：左側第三次不接
        direction, target = "觀望", "左側已經試過兩次，這次先不接，等站回來再說"
    elif (rv.get("dump") or {}).get("on") and voice_state(check, rng) == "lower":  # R92：恐慌時不預掛
        direction, target = "觀望", "恐慌急跌，分批先不預掛，看到止跌反應再接"
    px_f = float(market["price"])
    sp_view = support_pressure_view(rng, check, px_f)
    warns = []
    tp_errs = text_pressure_errors(sp_view, btc_txt, slim_txt, report_txt, levels=known_levels(rng, check))
    if tp_errs:
        fixed = align_pressure_to_text(sp_view, px_f, btc_txt, slim_txt, report_txt)
        if fixed is not None:
            warns.append("支撐壓力行和文字的壓力對不上，已以文字為準重算：" + "；".join(dict.fromkeys(tp_errs))
                         + f"；原本「{support_pressure_text(sp_view)}」，改成「{support_pressure_text(fixed)}」")
            sp_view = fixed
        else:
            warns.append("支撐壓力行和文字的壓力對不上，文字裡有兩檔以上的壓力，無法自動對齊：" + "；".join(dict.fromkeys(tp_errs)))
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
    payload["levels"] = levels
    # 中止的只有：數字重複、支撐壓力放錯邊。中止時不寫任何輸出檔，發布端會看到失敗並標記沿用舊段。
    uniq_errs = unique_number_errors(("/btc 短線段", btc_txt), ("report_slim.txt", slim_txt))
    uniq_errs += support_pressure_errors(sp_view, px_f, sp_text)
    uniq_errs += rules_errs
    uniq_errs += level_role_errors(rng, sp_view, px_f, btc_txt, slim_txt, levels["watch"])
    payload["unique_check"] = {"ok": not uniq_errs, "errors": uniq_errs}
    if uniq_errs:
        for e in uniq_errs:
            print(e, file=sys.stderr)
        raise SystemExit(1)
    if warns:
        stamp = fmt_time(now)
        with open(out_dir / "short_model_warn.log", "a", encoding="utf-8") as fh:
            for w in warns:
                print(f"WARN short_model: {w}", file=sys.stderr)
                fh.write(f"{stamp} WARN {w}\n")
    (out_dir / "short_model.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    (out_dir / "btc_short.txt").write_text(btc_txt)
    (out_dir / "report_btc.txt").write_text(report_txt)
    (out_dir / "report_slim.txt").write_text(slim_txt)
    (out_dir / "forecast_short.txt").write_text(forecast_short)
    (out_dir / "levels.json").write_text(json.dumps(levels, ensure_ascii=False) + "\n")
    write_rules_internal(out_dir, now, rs, rv, rules_errs)
    # 狀態檔只保留劇本與主區間，供隔日比對；不把每次現價寫死成新劇本
    persist = {
        "version": state.get("version", 1),
        "origin": state.get("origin"),
        "main_range": rng,
        "scenarios": state["scenarios"],
        "last_recompute_reason": state.get("last_recompute_reason"),
        "last_recompute_bar": state.get("last_recompute_bar"),
        "overhead_levels": state.get("overhead_levels") or [],
        "rules_revision": RULES_REVISION,
        "rules_sha256": RULES_SHA256,
        "rules_sha256_seen": rs.get("sha256_now"),
        "rules_sync_status": rs["status"],
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
    rv0 = {"cycle": "bear", "regime": {"ok": True, "kind": "trend", "direction": "down", "signal": None},
           "dump": {"on": False}, "false_break": None, "left_tries": 0, "left_exhausted": False,
           "divergence": None, "double_inside": None}
    rvs = {name: rv0 for name, *_ in cases}
    rule_cases = [
        ("9_r80_false_break", {"price": 66300.0, "klines": {}}, rng, chk("upper_zone"),
         {**rv0, "false_break": {"side": "up", "extreme": 67420.0}}),
        ("10_r87_range_mid", {"price": 64655.0, "klines": {}}, rng, chk("mid"),
         {**rv0, "regime": {"ok": True, "kind": "range", "signal": "oversold"}}),
        ("11_r88_left_third", {"price": 61500.0, "klines": {}}, rng, chk("lower_zone"),
         {**rv0, "left_tries": 3, "left_exhausted": True}),
        ("12_r92_r25_panic", {"price": 59500.0, "klines": {}}, rng, chk("lower_zone"),
         {**rv0, "dump": {"on": True}, "left_tries": 1, "regime": {"ok": True, "kind": "trend", "direction": "down", "signal": "trend_oversold"}}),
        ("13_r88_h1_lost", {"price": 66400.0, "klines": {}}, rng,
         {**chk("above", True, True, [{"type": "stand_firm"}]), "stand_firm": {"ok": True, "hold_side": "lost", "h1_body_break": True}}, rv0),
        ("14_r81_r89", {"price": 64655.0, "klines": {}}, rng, chk("mid"),
         {**rv0, "divergence": {"kind": "bottom"}, "double_inside": {"status": "broke_down", "retests": 1}}),
        ("15_cap_six_rules", {"price": 61500.0, "klines": {}}, rng, chk("lower_zone"),
         {**rv0, "regime": {"ok": True, "kind": "range", "signal": "oversold"}, "divergence": {"kind": "bottom"},
          "double_inside": {"status": "broke_down", "retests": 1}}),
    ]
    for name, market, r, check, rv in rule_cases:
        cases.append((name, market, r, check))
        rvs[name] = rv
    failed = 0
    for name, market, r, check in cases:
        btc = render_btc_short(now, market, r, check, ma_d, {}, macd_d, macd_4, [], rv=rvs[name], macd_w={"ok": True, "line": -500})
        slim = render_report_slim(now, market, r, check, ma_d, macd_d, macd_4)
        (out_dir / f"{name}_btc.txt").write_text(btc)
        (out_dir / f"{name}_slim.txt").write_text(slim)
        errs = unique_number_errors(("/btc 短線段", btc), ("report_slim.txt", slim))
        if r:
            _sp = support_pressure_view(r, check, float(market["price"]))
            _spt = support_pressure_text(_sp)
            errs += support_pressure_errors(_sp, float(market["price"]), _spt)
            errs += text_pressure_errors(_sp, btc, slim, levels=known_levels(r, check))
            (out_dir / f"{name}_support_pressure.txt").write_text(_spt + "\n")
        other = []
        blob = btc + slim
        if "劇本：" in blob or blob.lstrip().startswith("短線："):
            other.append("仍有摘要行或劇本行")
        if "數天" in blob or "數週" in blob or "數周" in blob:
            other.append("仍有週期標籤")
        if "以及下沿" in blob:
            other.append("分批仍寫「以及下沿」，應寫「與下沿」")
        other += rules_sentence_errors(btc, rvs.get(name))
        for bad in ("OKX", "CryptoQuant", "CQ", "已移除", "來源", "http", "@"):
            if bad in blob:
                other.append(f"出現「{bad}」")
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
              f"位置={chk['position']}  "
              f"規則={(_RULES_STATUS.get('note') or ('已同步 ' + RULES_REVISION)) if _RULES_STATUS.get('ok') else _RULES_STATUS.get('flag')}")
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

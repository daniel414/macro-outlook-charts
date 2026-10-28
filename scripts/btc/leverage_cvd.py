#!/usr/bin/env python3
"""槓桿與買賣力道堆疊（CoinGlass 風格）。供 positioning_grid 與獨立繪圖使用。

圖層（上→下）：價格 → OI K 線（多所合計）→ 合約/現貨 CVD（多所合計單線）→ 資金費率（OI 加權）
配色對齊 CoinGlass：合約 CVD 藍、現貨 CVD 橙。
禁用 Binance fapi。合約 CVD 僅原始 taker（OKX 合約分頁＋Bitget）；Gate/Bybit 推估移除；Bybit 無公開歷史 taker 則跳過。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter, MaxNLocator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TZ8 = timezone(timedelta(hours=8))
UA = {"User-Agent": "btc-sentiment-trial/1.0"}

# CoinGlass-ish palette
TH = dict(
    bg="#0b0e11",
    panel="#0b0e11",
    grid="#1e2329",
    text="#eaecef",
    muted="#848e9c",
    title="#ffffff",
    up="#0ecb81",
    down="#f6465d",
    fut="#4c82fb",      # CoinGlass futures CVD blue
    spot="#f0b90b",     # CoinGlass-ish spot orange/gold
    oi_up="#0ecb81",
    oi_dn="#f6465d",
    fund_pos="#0ecb81",
    fund_neg="#f6465d",
    candle_up="#0ecb81",
    candle_dn="#f6465d",
    tag_bg="#1a1d24",
)


def _get(url: str, timeout: int = 25) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _try_get(url: str, timeout: int = 25) -> tuple[Optional[Any], Optional[str]]:
    try:
        return _get(url, timeout=timeout), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _okx_ct_val() -> float:
    try:
        d = _get("https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP")
        return float(d["data"][0]["ctVal"])
    except Exception:
        return 0.01


# ── Price: Binance USDT perp via www basis (non-fapi) ─────────────────────────
def fetch_bn_perp_price_basis(hours: int = 250) -> tuple[list[dict], dict]:
    """www.binance.com/futures/data/basis — futuresPrice（非 fapi）。
    無官方 OHLC：開＝前收、高/低＝max/min(開,收)。
    """
    d = _get(
        f"https://www.binance.com/futures/data/basis?pair=BTCUSDT&contractType=PERPETUAL"
        f"&period=1h&limit={min(max(hours + 5, 100), 500)}"
    )
    rows = sorted(
        [{"ts": int(r["timestamp"]) / 1000, "c": float(r["futuresPrice"]), "index": float(r["indexPrice"])} for r in d],
        key=lambda x: x["ts"],
    )
    candles = []
    for i, r in enumerate(rows):
        o = rows[i - 1]["c"] if i else r["c"]
        c = r["c"]
        candles.append({"ts": r["ts"], "o": o, "h": max(o, c), "l": min(o, c), "c": c})
    candles = candles[-hours:]
    meta = {
        "source": "Binance www/futures/data/basis futuresPrice（非 fapi）",
        "note": "無公開永續 OHLC；影線由相鄰收盤合成",
        "n": len(candles),
        "start_ts": candles[0]["ts"] if candles else None,
        "end_ts": candles[-1]["ts"] if candles else None,
    }
    return candles, meta



# ── Price: full OHLC（禁 fapi；不合成影線）───────────────────────────────────
def fetch_okx_perp_candles(hours: int = 250) -> tuple[list[dict], dict]:
    """OKX BTC-USDT-SWAP 1H 完整 OHLC（history-candles 分頁）。"""
    need = hours + 5
    by: dict[int, dict] = {}
    after = None
    for _ in range(20):
        url = "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=1H&limit=100"
        if after:
            url += f"&after={after}"
        d = _get(url)
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            ts = int(r[0]) // 1000
            by[ts] = {
                "ts": ts,
                "o": float(r[1]),
                "h": float(r[2]),
                "l": float(r[3]),
                "c": float(r[4]),
            }
        after = rows[-1][0]
        if len(by) >= need:
            break
        time.sleep(0.08)
    candles = [by[k] for k in sorted(by)][-hours:]
    meta = {
        "source": "OKX market/history-candles BTC-USDT-SWAP 1H（完整 OHLC）",
        "note": "非 Binance 永續；禁 fapi，改 OKX 永續真實影線",
        "n": len(candles),
        "start_ts": candles[0]["ts"] if candles else None,
        "end_ts": candles[-1]["ts"] if candles else None,
        "ohlc": "full",
    }
    return candles, meta


def fetch_bn_spot_ohlc(hours: int = 250) -> tuple[list[dict], dict]:
    """Binance 現貨 api/v3/klines BTCUSDT 完整 OHLC（備援）。"""
    d = _get(
        f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit={min(hours + 5, 1000)}"
    )
    candles = [
        {
            "ts": int(r[0]) / 1000,
            "o": float(r[1]),
            "h": float(r[2]),
            "l": float(r[3]),
            "c": float(r[4]),
        }
        for r in d
    ][-hours:]
    meta = {
        "source": "Binance spot api/v3/klines BTCUSDT（完整 OHLC）",
        "note": "現貨價，非永續",
        "n": len(candles),
        "start_ts": candles[0]["ts"] if candles else None,
        "end_ts": candles[-1]["ts"] if candles else None,
        "ohlc": "full",
    }
    return candles, meta


# ── Futures CVD per venue ─────────────────────────────────────────────────────
def fetch_okx_futures_cvd(hours: int = 240, price_by_ts: Optional[dict] = None) -> tuple[list[dict], dict]:
    ct = _okx_ct_val()
    need = hours + 5
    by: dict[int, dict] = {}
    now_ms = int(time.time() * 1000)
    pages = max(3, (need // 90) + 2)
    for i in range(pages):
        end_ms = now_ms - i * 100 * 3600 * 1000
        begin_ms = end_ms - 100 * 3600 * 1000
        url = (
            "https://www.okx.com/api/v5/rubik/stat/taker-volume-contract"
            f"?instId=BTC-USDT-SWAP&period=1H&begin={begin_ms}&end={end_ms}"
        )
        d = _get(url)
        for r in d.get("data") or []:
            ts = int(r[0]) // 1000
            sell, buy = float(r[1]), float(r[2])
            px = (price_by_ts or {}).get(ts) or 0.0
            by[ts] = {"ts": ts, "delta": (buy - sell) * ct * px, "buy": buy, "sell": sell, "px": px}
        time.sleep(0.1)
        if len(by) >= need:
            break
    if any(not r.get("px") for r in by.values()):
        # fill from OKX candles
        got = {}
        cursor = str(int(time.time() * 1000))
        for _ in range(6):
            d = _get(f"https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=1H&after={cursor}&limit=100")
            batch = d.get("data") or []
            if not batch:
                break
            for r in batch:
                got[int(r[0]) // 1000] = float(r[4])
            cursor = batch[-1][0]
            time.sleep(0.08)
        for ts, r in by.items():
            if not r.get("px"):
                px = got.get(ts) or (got[min(got, key=lambda k: abs(k - ts))] if got else 0)
                r["px"] = px
                r["delta"] = (r["buy"] - r["sell"]) * ct * px
    rows = [by[k] for k in sorted(by)][-hours:]
    return rows, {
        "source": "OKX taker-volume-contract（分頁）",
        "kind": "real",
        "n": len(rows),
        "start_ts": rows[0]["ts"] if rows else None,
        "end_ts": rows[-1]["ts"] if rows else None,
    }


def fetch_gate_futures_cvd(hours: int = 240) -> tuple[list[dict], dict]:
    end = int(time.time())
    start = end - (hours + 6) * 3600
    stats = _get(
        f"https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=BTC_USDT&interval=1h&limit={min(hours + 20, 300)}"
    )
    candles = _get(
        f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=BTC_USDT&interval=1h&from={start}&to={end}"
    )
    c_by = {int(c["t"]): c for c in candles}
    rows = []
    for s in stats:
        ts = int(float(s["time"]))
        c = c_by.get(ts)
        if not c:
            continue
        r = float(s.get("lsr_taker") or 0)
        if r <= 0:
            continue
        vol = float(c.get("sum") or 0)
        rows.append({"ts": ts, "delta": vol * (r - 1.0) / (r + 1.0)})
    rows.sort(key=lambda x: x["ts"])
    rows = rows[-hours:]
    return rows, {
        "source": "Gate lsr_taker×K線sum（推估）",
        "kind": "estimate",
        "n": len(rows),
        "start_ts": rows[0]["ts"] if rows else None,
        "end_ts": rows[-1]["ts"] if rows else None,
        "note": "無絕對 taker 歷史",
    }


def fetch_bitget_futures_cvd(hours: int = 240, price_by_ts: Optional[dict] = None) -> tuple[list[dict], dict]:
    """Bitget taker-buy-sell 僅約 30 根 1H；有則用真實 delta（幣數×價→USDT）。"""
    d, err = _try_get(
        "https://api.bitget.com/api/v2/mix/market/taker-buy-sell?symbol=BTCUSDT&productType=USDT-FUTURES&period=1H"
    )
    if err or not d:
        return [], {"source": "Bitget taker-buy-sell", "kind": "real", "error": err or "empty", "n": 0}
    rows = []
    for r in d.get("data") or []:
        ts = int(r["ts"]) // 1000
        buy, sell = float(r["buyVolume"]), float(r["sellVolume"])
        px = (price_by_ts or {}).get(ts) or (price_by_ts or {}).get(ts - ts % 3600) or 0.0
        rows.append({"ts": ts, "delta": (buy - sell) * px})
    rows.sort(key=lambda x: x["ts"])
    rows = rows[-hours:]
    return rows, {
        "source": "Bitget mix taker-buy-sell",
        "kind": "real",
        "n": len(rows),
        "start_ts": rows[0]["ts"] if rows else None,
        "end_ts": rows[-1]["ts"] if rows else None,
        "note": f"API 僅回約 {len(d.get('data') or [])} 根 1H，無法單獨滿 10 天",
    }


def fetch_bybit_futures_cvd(hours: int = 240) -> tuple[list[dict], dict]:
    """Bybit：account-ratio.buyRatio × kline turnover 推估 delta。直連失敗則空。"""
    d, err = _try_get(
        f"https://api.bybit.com/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=1h&limit={min(hours + 10, 500)}"
    )
    if err or not d or d.get("retCode") not in (0, "0", None):
        return [], {
            "source": "Bybit account-ratio×turnover（推估）",
            "kind": "estimate",
            "error": err or str(d)[:120],
            "n": 0,
            "note": "直連失敗則跳過（不用 proxy）",
        }
    ratios = {int(r["timestamp"]) // 1000: float(r["buyRatio"]) for r in (d.get("result") or {}).get("list") or []}
    # klines paginated
    kl: dict[int, float] = {}
    end = int(time.time() * 1000)
    while len(kl) < hours + 5:
        kd, kerr = _try_get(
            f"https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&end={end}&limit=1000"
        )
        if kerr or not kd:
            break
        batch = (kd.get("result") or {}).get("list") or []
        if not batch:
            break
        for r in batch:
            # [start, open, high, low, close, volume, turnover]
            kl[int(r[0]) // 1000] = float(r[6])
        end = int(batch[-1][0]) - 1
        if len(batch) < 1000:
            break
        time.sleep(0.08)
    rows = []
    for ts in sorted(set(ratios) & set(kl)):
        br = ratios[ts]
        # buyRatio of accounts ≠ taker；推估 delta≈turnover*(2*br-1)
        rows.append({"ts": ts, "delta": kl[ts] * (2 * br - 1)})
    rows = rows[-hours:]
    return rows, {
        "source": "Bybit account-ratio×kline.turnover（推估）",
        "kind": "estimate",
        "n": len(rows),
        "start_ts": rows[0]["ts"] if rows else None,
        "end_ts": rows[-1]["ts"] if rows else None,
        "note": "buyRatio 為帳戶比非 taker；標註推估",
    }


def merge_delta_series(parts: list[tuple[str, list[dict], dict]]) -> tuple[list[dict], dict]:
    """同小時 delta 相加 → 單線合計。"""
    maps = {name: {int(r["ts"]): r["delta"] for r in rows} for name, rows, _ in parts if rows}
    metas = {name: meta for name, rows, meta in parts}
    included = [n for n, rows, _ in parts if rows]
    skipped = {n: m for n, m in metas.items() if n not in included or not maps.get(n)}
    keys = sorted(set().union(*(m.keys() for m in maps.values()))) if maps else []
    out = []
    for ts in keys:
        d = 0.0
        used = []
        for n, m in maps.items():
            if ts in m:
                d += m[ts]
                used.append(n)
        out.append({"ts": ts, "delta": d, "parts": "+".join(used)})
    return out, {
        "included": included,
        "per_venue": metas,
        "n": len(out),
        "start_ts": out[0]["ts"] if out else None,
        "end_ts": out[-1]["ts"] if out else None,
        "skipped": {k: v for k, v in metas.items() if not maps.get(k)},
    }


# ── Spot CVD ──────────────────────────────────────────────────────────────────
def fetch_bn_spot_delta(hours: int = 240) -> tuple[list[dict], dict]:
    d = _get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit={min(hours + 5, 1000)}")
    rows = []
    for r in d:
        vol = float(r[7])  # quote volume USDT
        buy = float(r[10])  # taker buy quote
        rows.append({"ts": int(r[0]) / 1000, "delta": buy - (vol - buy)})
    rows = rows[-hours:]
    return rows, {
        "source": "Binance spot klines taker buy quote",
        "kind": "real",
        "n": len(rows),
        "start_ts": rows[0]["ts"] if rows else None,
        "end_ts": rows[-1]["ts"] if rows else None,
    }


def fetch_okx_spot_delta(hours: int = 240, price_by_ts: Optional[dict] = None) -> tuple[list[dict], dict]:
    d = _get("https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy=BTC&instType=SPOT&period=1H")
    rows = []
    for r in d.get("data") or []:
        ts = int(r[0]) // 1000
        sell, buy = float(r[1]), float(r[2])  # BTC
        px = (price_by_ts or {}).get(ts) or 0.0
        rows.append({"ts": ts, "delta": (buy - sell) * px})
    rows.sort(key=lambda x: x["ts"])
    rows = rows[-hours:]
    return rows, {
        "source": "OKX rubik taker-volume SPOT（BTC×價→USDT）",
        "kind": "real",
        "n": len(rows),
        "start_ts": rows[0]["ts"] if rows else None,
        "end_ts": rows[-1]["ts"] if rows else None,
        "note": "此端點約 72 根 1H，無法單獨滿 10 天",
    }


def fetch_coinbase_spot_delta(hours: int = 240) -> tuple[list[dict], dict]:
    """Coinbase Exchange candles 無 taker buy/sell → 無法做真 CVD。"""
    return [], {
        "source": "Coinbase Exchange candles",
        "kind": "unavailable",
        "n": 0,
        "error": "公開 K 線無 taker buy/sell 欄位；彙總成交歷史成本過高且不在本模組範圍",
    }


# ── OI aggregate + candles ────────────────────────────────────────────────────
def fetch_oi_venues(hours: int = 240) -> tuple[dict[str, list[dict]], dict]:
    out: dict[str, list[dict]] = {}
    meta: dict = {}

    # OKX paginate
    okx_by: dict[int, float] = {}
    now_ms = int(time.time() * 1000)
    for i in range(max(3, hours // 90 + 2)):
        end_ms = now_ms - i * 100 * 3600 * 1000
        begin_ms = end_ms - 100 * 3600 * 1000
        d = _get(
            "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
            f"?instId=BTC-USDT-SWAP&period=1H&begin={begin_ms}&end={end_ms}&limit=100"
        )
        for r in d.get("data") or []:
            okx_by[int(r[0]) // 1000] = float(r[3])
        time.sleep(0.1)
        if len(okx_by) >= hours:
            break
    out["okx"] = [{"ts": k, "usd": okx_by[k]} for k in sorted(okx_by)][-hours:]
    meta["okx"] = {"n": len(out["okx"]), "start_ts": out["okx"][0]["ts"] if out["okx"] else None,
                   "end_ts": out["okx"][-1]["ts"] if out["okx"] else None, "source": "OKX OI history"}

    gate = _get(
        f"https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=BTC_USDT&interval=1h&limit={min(max(hours, 240), 300)}"
    )
    out["gate"] = sorted(
        [{"ts": float(r["time"]), "usd": float(r.get("open_interest_usd") or 0)} for r in gate],
        key=lambda x: x["ts"],
    )[-hours:]
    meta["gate"] = {"n": len(out["gate"]), "start_ts": out["gate"][0]["ts"] if out["gate"] else None,
                    "end_ts": out["gate"][-1]["ts"] if out["gate"] else None, "source": "Gate contract_stats"}

    bn = _get(
        f"https://www.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit={min(max(hours, 240), 500)}"
    )
    out["bn"] = sorted(
        [{"ts": int(r["timestamp"]) / 1000, "usd": float(r["sumOpenInterestValue"])} for r in bn],
        key=lambda x: x["ts"],
    )[-hours:]
    meta["bn"] = {"n": len(out["bn"]), "start_ts": out["bn"][0]["ts"] if out["bn"] else None,
                  "end_ts": out["bn"][-1]["ts"] if out["bn"] else None, "source": "BN www openInterestHist（非 fapi）"}

    # Bybit OI (BTC) × price
    bd, berr = _try_get(
        f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=1h&limit=200"
    )
    bybit_rows = []
    if bd and not berr and (bd.get("retCode") in (0, "0") or bd.get("result")):
        # paginate cursor
        cursor = None
        got: dict[int, float] = {}
        for _ in range(5):
            url = f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=1h&limit=200"
            if cursor:
                url += f"&cursor={cursor}"
            bd2, err2 = _try_get(url)
            if err2 or not bd2:
                break
            lst = (bd2.get("result") or {}).get("list") or []
            for r in lst:
                ts = int(r["timestamp"]) // 1000
                got[ts] = float(r["openInterest"])
            cursor = (bd2.get("result") or {}).get("nextPageCursor")
            if not cursor or not lst:
                break
            time.sleep(0.08)
        # convert BTC → USD with BN basis price map later; store btc for now
        bybit_rows = [{"ts": k, "btc": got[k], "usd": 0.0} for k in sorted(got)][-hours:]
        meta["bybit"] = {"n": len(bybit_rows), "start_ts": bybit_rows[0]["ts"] if bybit_rows else None,
                         "end_ts": bybit_rows[-1]["ts"] if bybit_rows else None, "source": "Bybit v5 open-interest",
                         "unit": "btc→usd via price"}
    else:
        meta["bybit"] = {"n": 0, "error": berr or "blocked", "note": "直連失敗則跳過"}
    out["bybit"] = bybit_rows

    # Bitget: only snapshot — no usable history endpoint found
    bg, bgerr = _try_get(
        "https://api.bitget.com/api/v2/mix/market/open-interest?symbol=BTCUSDT&productType=USDT-FUTURES"
    )
    if bg and not bgerr:
        try:
            sz = float(bg["data"]["openInterestList"][0]["size"])  # BTC
            ts = int(bg["data"]["ts"]) // 1000
            out["bitget"] = [{"ts": ts, "btc": sz, "usd": 0.0}]
            meta["bitget"] = {"n": 1, "source": "Bitget OI snapshot only", "note": "無可用 1H 歷史，不納入合計時間序列"}
        except Exception as e:
            out["bitget"] = []
            meta["bitget"] = {"n": 0, "error": str(e)}
    else:
        out["bitget"] = []
        meta["bitget"] = {"n": 0, "error": bgerr or "fail"}

    return out, meta


def aggregate_oi_candles(venues: dict[str, list[dict]], price_by_ts: dict, hours: int) -> tuple[list[dict], dict]:
    """多所 OI USD 合計，再做成 OI K 線（開＝前收、高/低＝max/min）。"""
    maps = {}
    used = []
    for name, rows in venues.items():
        if name == "bitget":
            continue  # snapshot only
        if not rows:
            continue
        m = {}
        for r in rows:
            ts = int(r["ts"])
            if r.get("usd"):
                m[ts] = float(r["usd"])
            elif r.get("btc"):
                px = price_by_ts.get(ts) or price_by_ts.get(ts - ts % 3600) or 0
                m[ts] = float(r["btc"]) * px
            else:
                continue
        if m:
            maps[name] = m
            used.append(name)
    # union hours where at least 2 venues or all available
    # 前向填補各所，並要求核心三所（bn/okx/gate）皆有值，避免尾端缺所造成合計跳水
    core = [n for n in ("bn", "okx", "gate") if n in maps]
    all_keys = sorted(set().union(*(m.keys() for m in maps.values())))
    filled = {n: {} for n in maps}
    for n, m in maps.items():
        last = None
        for ts in all_keys:
            if ts in m and m[ts] > 0:
                last = m[ts]
            if last is not None:
                filled[n][ts] = last
    keys = [ts for ts in all_keys if all(ts in filled[n] for n in core)][-hours:]
    series = []
    for ts in keys:
        s = 0.0
        n = 0
        for name, m in filled.items():
            if ts in m:
                s += m[ts]
                n += 1
        if n:
            series.append({"ts": ts, "usd": s, "n_venues": n})
    candles = []
    for i, r in enumerate(series):
        o = series[i - 1]["usd"] if i else r["usd"]
        c = r["usd"]
        candles.append({"ts": r["ts"], "o": o, "h": max(o, c), "l": min(o, c), "c": c, "n_venues": r["n_venues"]})
    meta = {
        "included": used,
        "n": len(candles),
        "start_ts": candles[0]["ts"] if candles else None,
        "end_ts": candles[-1]["ts"] if candles else None,
        "latest_usd": candles[-1]["c"] if candles else None,
    }
    return candles, meta


# ── Funding OI-weighted ───────────────────────────────────────────────────────
def fetch_funding_oi_weighted(oi_maps: dict[str, dict[int, float]], n: int = 60) -> tuple[list[dict], dict]:
    """各所 funding × 同時點 OI，再加權平均。"""
    funds: dict[str, list[dict]] = {}
    meta = {}

    okx = _get("https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=100")
    funds["okx"] = sorted(
        [{"ts": int(r["fundingTime"]) // 1000, "rate": float(r.get("realizedRate") or r["fundingRate"])}
         for r in okx["data"]],
        key=lambda x: x["ts"],
    )
    meta["okx"] = {"n": len(funds["okx"])}

    gate = _get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=BTC_USDT&limit=100")
    funds["gate"] = sorted([{"ts": int(float(r["t"])), "rate": float(r["r"])} for r in gate], key=lambda x: x["ts"])
    meta["gate"] = {"n": len(funds["gate"])}

    bd, berr = _try_get("https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=100")
    if bd and not berr:
        funds["bybit"] = sorted(
            [{"ts": int(r["fundingRateTimestamp"]) // 1000, "rate": float(r["fundingRate"])}
             for r in (bd.get("result") or {}).get("list") or []],
            key=lambda x: x["ts"],
        )
        meta["bybit"] = {"n": len(funds["bybit"])}
    else:
        meta["bybit"] = {"n": 0, "error": berr or "skip"}

    bg, bgerr = _try_get(
        "https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=BTCUSDT&productType=USDT-FUTURES&pageSize=100"
    )
    if bg and not bgerr:
        funds["bitget"] = sorted(
            [{"ts": int(r["fundingTime"]) // 1000, "rate": float(r["fundingRate"])} for r in bg.get("data") or []],
            key=lambda x: x["ts"],
        )
        meta["bitget"] = {"n": len(funds["bitget"])}
    else:
        meta["bitget"] = {"n": 0, "error": bgerr or "skip"}

    # all funding timestamps
    all_ts = sorted(set(r["ts"] for rows in funds.values() for r in rows))
    # nearest OI helper
    def nearest_oi(venue: str, ts: int) -> float:
        m = oi_maps.get(venue) or {}
        if ts in m:
            return m[ts]
        if not m:
            return 0.0
        k = min(m.keys(), key=lambda x: abs(x - ts))
        if abs(k - ts) <= 8 * 3600:
            return m[k]
        return 0.0

    fund_by = {v: {r["ts"]: r["rate"] for r in rows} for v, rows in funds.items()}
    out = []
    for ts in all_ts:
        num = den = 0.0
        used = []
        for v, fmap in fund_by.items():
            if ts not in fmap:
                continue
            oi = nearest_oi(v if v != "bn" else "bn", ts)
            # map venue names: funding venues okx/gate/bybit/bitget — oi maps use same
            if v == "bitget" and oi <= 0:
                # no hist oi — equal weight skip or tiny
                continue
            if oi <= 0:
                continue
            num += fmap[ts] * oi
            den += oi
            used.append(v)
        if den > 0:
            out.append({"ts": ts, "pct": num / den * 100, "parts": "+".join(used)})
    out = out[-n:]
    meta["weighted"] = {
        "n": len(out),
        "start_ts": out[0]["ts"] if out else None,
        "end_ts": out[-1]["ts"] if out else None,
        "included": sorted({p for r in out for p in r["parts"].split("+") if p}),
    }
    return out, meta


# ── Drawing helpers ───────────────────────────────────────────────────────────
def to_num(ts_list):
    return mdates.date2num([datetime.fromtimestamp(t, timezone.utc) for t in ts_list])


def cum_delta(rows: list[dict], start_ts: float) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    c = 0.0
    for r in rows:
        if r["ts"] < start_ts:
            continue
        c += r["delta"]
        xs.append(r["ts"])
        ys.append(c)
    return np.array(xs), np.array(ys)


def right_tag(ax, x, y, text, color, fontsize=11):
    ax.annotate(
        text, xy=(x, y), xytext=(8, 0), textcoords="offset points",
        fontsize=fontsize, fontweight="bold", color=color, va="center", ha="left",
        bbox=dict(boxstyle="round,pad=0.25", fc=TH["tag_bg"], ec=color, lw=0.8, alpha=0.92),
        clip_on=False, zorder=20,
    )


def draw_candles(ax, candles, width_days=1 / 24 * 0.7, up=None, dn=None):
    up = up or TH["candle_up"]
    dn = dn or TH["candle_dn"]
    for c in candles:
        x = mdates.date2num(datetime.fromtimestamp(c["ts"], timezone.utc))
        col = up if c["c"] >= c["o"] else dn
        ax.plot([x, x], [c["l"], c["h"]], color=col, lw=1.0, solid_capstyle="round", zorder=3)
        o, cl = c["o"], c["c"]
        bottom, height = min(o, cl), abs(cl - o) or (c["h"] - c["l"]) * 0.02 or 1e-6
        ax.bar(x, height, bottom=bottom, width=width_days, color=col, align="center", zorder=4, linewidth=0)


def style_ax(ax):
    ax.set_facecolor(TH["panel"])
    for sp in ax.spines.values():
        sp.set_color(TH["grid"]); sp.set_linewidth(0.5)
    ax.grid(True, axis="both", color=TH["grid"], lw=0.6, alpha=0.85)
    ax.tick_params(colors=TH["muted"], labelsize=10)


def _ft(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, TZ8).strftime("%m/%d %H:%M")


def build_chart(days: float = 10.0, out: Optional[str] = None, dpi: float = 140) -> str:
    hours = int(days * 24)
    # v4：完整 OHLC（OKX 永續；失敗則 BN 現貨）
    try:
        candles, price_meta = fetch_okx_perp_candles(hours + 5)
    except Exception as e:
        candles, price_meta = fetch_bn_spot_ohlc(hours + 5)
        price_meta["fallback_from"] = f"OKX candles failed: {type(e).__name__}: {e}"
    price_by_ts = {int(c["ts"]): float(c["c"]) for c in candles}
    start_ts = candles[0]["ts"]
    end_ts = candles[-1]["ts"] + 3600
    xlim = (
        mdates.date2num(datetime.fromtimestamp(start_ts, timezone.utc)),
        mdates.date2num(datetime.fromtimestamp(end_ts, timezone.utc)),
    )

    # v4：合約 CVD 僅原始 taker（OKX＋Bitget）；Gate/Bybit 推估不納入
    okx_f, okx_fm = fetch_okx_futures_cvd(hours + 10, price_by_ts=price_by_ts)
    bitget_f, bitget_fm = fetch_bitget_futures_cvd(hours + 10, price_by_ts=price_by_ts)
    # Bybit：無公開歷史 taker-buy-sell；僅 recent-trade／kline 無拆分 → 跳過（不繞過）
    bybit_skip = {
        "source": "Bybit public v5",
        "kind": "unavailable",
        "n": 0,
        "error": "無公開歷史 taker buy/sell（僅 recent-trade 快照與未拆分 kline）；不採用 account-ratio 推估",
    }
    gate_skip = {
        "source": "Gate lsr_taker×sum",
        "kind": "excluded_estimate",
        "n": 0,
        "error": "v4 移除推估；Gate 無絕對 taker 歷史",
    }
    fut, fut_meta = merge_delta_series([
        ("OKX", okx_f, okx_fm),
        ("Bitget", bitget_f, bitget_fm),
        ("Gate", [], gate_skip),
        ("Bybit", [], bybit_skip),
    ])
    # 單位／符號核對摘要（寫入 meta）
    fut_meta["unit_sign_audit"] = {
        "OKX": {
            "endpoint": "rubik/stat/taker-volume-contract BTC-USDT-SWAP",
            "raw_fields": "[ts, sellVol, buyVol] 張數（contracts）",
            "conversion": "delta_usd=(buy-sell)*ctVal(0.01 BTC)*close",
            "sign": "buy-sell（與 CoinGlass taker buy−sell 同向）",
            "crosscheck_3d_20261025_27": "cum≈負向，對齊 CoinGlass 合約 CVD 下滑",
        },
        "Bitget": {
            "endpoint": "mix/market/taker-buy-sell BTCUSDT USDT-FUTURES 1H",
            "raw_fields": "buyVolume/sellVolume（幣數 BTC）",
            "conversion": "delta_usd=(buy-sell)*close",
            "sign": "buy-sell",
            "history": "API 約 30 根 1H，不足 10 天如實標示",
        },
        "Gate": "excluded_estimate",
        "Bybit": "no_raw_taker_history_skipped",
    }

    bn_s, bn_sm = fetch_bn_spot_delta(hours + 10)
    okx_s, okx_sm = fetch_okx_spot_delta(hours + 10, price_by_ts=price_by_ts)
    cb_s, cb_sm = fetch_coinbase_spot_delta(hours + 10)
    spot, spot_meta = merge_delta_series([
        ("Binance", bn_s, bn_sm),
        ("OKX", okx_s, okx_sm),
        ("Coinbase", cb_s, cb_sm),
    ])

    oi_venues, oi_vmeta = fetch_oi_venues(hours + 10)
    # fill bybit usd
    for r in oi_venues.get("bybit") or []:
        if not r.get("usd"):
            px = price_by_ts.get(int(r["ts"])) or price_by_ts.get(int(r["ts"]) - int(r["ts"]) % 3600) or 0
            r["usd"] = float(r.get("btc") or 0) * px
    oi_candles, oi_meta = aggregate_oi_candles(oi_venues, price_by_ts, hours + 2)

    oi_maps = {}
    for name, rows in oi_venues.items():
        oi_maps[name] = {int(r["ts"]): float(r.get("usd") or 0) for r in rows if r.get("usd") or r.get("btc")}
        if name == "bybit":
            oi_maps[name] = {int(r["ts"]): float(r.get("usd") or 0) for r in rows}

    fund, fund_meta = fetch_funding_oi_weighted(oi_maps, n=80)

    fx, fy = cum_delta(fut, start_ts)
    sx, sy = cum_delta(spot, start_ts)

    nowt = datetime.now(TZ8)
    out = out or os.path.join(ROOT, "out/positioning/leverage_cvd_standalone.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    with plt.rc_context({"font.sans-serif": ["Noto Sans CJK TC", "Noto Sans CJK JP", "DejaVu Sans"],
                         "axes.unicode_minus": False, "text.color": TH["text"], "axes.labelcolor": TH["text"]}):
        fig = plt.figure(figsize=(16, 16.5), dpi=dpi, facecolor=TH["bg"])
        gs = fig.add_gridspec(4, 1, left=0.08, right=0.90, top=0.88, bottom=0.06, hspace=0.14,
                              height_ratios=[1.35, 1.15, 1.35, 1.0])
        axes = [fig.add_subplot(gs[i]) for i in range(4)]
        for a in axes:
            style_ax(a)

        # 1 Price
        ax = axes[0]
        draw_candles(ax, candles)
        ax.set_xlim(*xlim)
        lo = min(c["l"] for c in candles); hi = max(c["h"] for c in candles)
        pad = (hi - lo) * 0.08 or 100
        ax.set_ylim(lo - pad, hi + pad)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.yaxis.set_major_locator(MaxNLocator(4))
        right_tag(ax, xlim[1], candles[-1]["c"], f"{candles[-1]['c']:,.1f}", TH["title"], 12)
        ax.set_ylabel("價格", color=TH["muted"], fontsize=11)
        price_title = "BTC 價格 · OKX 永續完整 OHLC（禁 fapi）" if "OKX" in str(price_meta.get("source")) else "BTC 價格 · Binance 現貨完整 OHLC（禁 fapi）"
        ax.set_title(price_title, fontsize=13, fontweight="bold",
                     color=TH["title"], loc="left", pad=8)
        ax.tick_params(labelbottom=False)

        # 2 OI candles aggregated
        ax = axes[1]
        draw_candles(ax, oi_candles, up=TH["oi_up"], dn=TH["oi_dn"])
        ax.set_xlim(*xlim)
        if oi_candles:
            lo = min(c["l"] for c in oi_candles); hi = max(c["h"] for c in oi_candles)
            pad = (hi - lo) * 0.10 or 1e8
            ax.set_ylim(lo - pad, hi + pad)
            right_tag(ax, xlim[1], oi_candles[-1]["c"] / 1e9, f"${oi_candles[-1]['c']/1e9:.2f}B", TH["fut"], 11)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v/1e9:.1f}B"))
        oi_labs = "+".join(oi_meta.get("included") or [])
        ax.set_ylabel("合計 OI", color=TH["muted"], fontsize=11)
        ax.set_title(f"未平倉量 K 線 · 合計（{oi_labs}）", fontsize=13, fontweight="bold",
                     color=TH["title"], loc="left", pad=8)
        ax.tick_params(labelbottom=False)

        # 3 CVD futures + spot
        ax = axes[2]
        fut_lab = "合約 CVD（" + "+".join(fut_meta.get("included") or []) + "）"
        spot_lab = "現貨 CVD（" + "+".join(spot_meta.get("included") or []) + "）"
        if len(fx):
            ax.plot(to_num(fx), fy / 1e6, color=TH["fut"], lw=2.2, label=fut_lab, zorder=5)
            right_tag(ax, xlim[1], fy[-1] / 1e6, f"合約 {fy[-1]/1e6:+.1f}M", TH["fut"])
        if len(sx):
            ax.plot(to_num(sx), sy / 1e6, color=TH["spot"], lw=2.2, label=spot_lab, zorder=4)
            right_tag(ax, xlim[1], sy[-1] / 1e6, f"現貨 {sy[-1]/1e6:+.1f}M", TH["spot"])
        ax.axhline(0, color=TH["muted"], lw=0.7, alpha=0.7)
        ax.set_xlim(*xlim)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}M"))
        ax.set_ylabel("CVD USDT", color=TH["muted"], fontsize=11)
        ax.set_title("合約 CVD vs 現貨 CVD（多所合計單線）", fontsize=13, fontweight="bold",
                     color=TH["title"], loc="left", pad=8)
        ax.legend(loc="upper left", fontsize=9, frameon=False, labelcolor=TH["text"])
        ax.tick_params(labelbottom=False)

        # 4 Funding OI-weighted
        ax = axes[3]
        fo = [r for r in fund if r["ts"] >= start_ts - 8 * 3600]
        if fo:
            xs = to_num([r["ts"] for r in fo])
            ys = [r["pct"] for r in fo]
            cols = [TH["fund_pos"] if y >= 0 else TH["fund_neg"] for y in ys]
            ax.bar(xs, ys, width=7.5 / 24, align="center", color=cols, alpha=0.9)
            right_tag(ax, xlim[1], ys[-1], f"加權 {ys[-1]:+.4f}%",
                      TH["fund_pos"] if ys[-1] >= 0 else TH["fund_neg"])
        ax.axhline(0, color=TH["muted"], lw=0.7)
        ax.set_xlim(*xlim)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.3f}%"))
        fund_labs = "+".join((fund_meta.get("weighted") or {}).get("included") or [])
        ax.set_ylabel("Funding", color=TH["muted"], fontsize=11)
        ax.set_title(f"資金費率 · OI 加權（{fund_labs or '—'}）", fontsize=13, fontweight="bold",
                     color=TH["title"], loc="left", pad=8)
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1, tz=TZ8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=TZ8))

        # Header — no overlap with panel titles
        fig.text(0.08, 0.975, "槓桿與買賣力道", fontsize=20, fontweight="bold",
                 color=TH["title"], va="top")
        fig.text(0.90, 0.975, f"{nowt:%Y/%m/%d %H:%M} UTC+8", fontsize=12, color=TH["muted"], ha="right", va="top")
        fig.text(
            0.08, 0.945,
            f"1H · {days:g}天 · 圖層：價→OI合計K→CVD合計→費率OI加權 · 配色：合約藍/現貨金 · 非fapi",
            fontsize=11, color=TH["muted"], va="top",
        )
        fig.text(
            0.08, 0.915,
            f"CVD {_ft(fut_meta.get('start_ts'))}→{_ft(fut_meta.get('end_ts'))} · "
            f"OI {_ft(oi_meta.get('start_ts'))}→{_ft(oi_meta.get('end_ts'))} · "
            f"合約 CVD＝原始 taker（無推估）· Gate/Bybit 未納入 · Coinbase 現貨無 taker 未納入",
            fontsize=10, color=TH["muted"], va="top",
        )

        fig.savefig(out, facecolor=TH["bg"], dpi=dpi)
        plt.close(fig)

    meta_path = os.path.splitext(out)[0] + "_meta.json"
    full_meta = {
        "price": price_meta,
        "fut_cvd": fut_meta,
        "spot_cvd": spot_meta,
        "oi": oi_meta,
        "oi_venues": oi_vmeta,
        "funding": fund_meta,
        "layer_order": ["price", "oi_candles_agg", "fut_spot_cvd", "funding_oi_weighted"],
        "colors": {"fut_cvd": TH["fut"], "spot_cvd": TH["spot"]},
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(full_meta, f, ensure_ascii=False, indent=2, default=str)
    print("META", meta_path)
    print(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=10.0)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--dpi", type=float, default=140)
    a = ap.parse_args()
    build_chart(days=a.days, out=a.out, dpi=a.dpi)


if __name__ == "__main__":
    main()

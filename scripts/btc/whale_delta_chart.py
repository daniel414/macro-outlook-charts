#!/usr/bin/env python3
"""比特幣巨鯨 vs 散戶差值 (Coinglass-style), OKX daily.

delta = OKX top-trader POSITION L/S ratio − OKX all-ACCOUNT L/S ratio (BTC-USDT-SWAP, 1D), plotted as a fraction like
Coinglass's right axis (±1.2). Coinglass's exact formula isn't published; its scale matches a ratio difference
(long%-difference would stay within ±0.3). Coinglass uses Binance (451 from the box), so values differ.
  python whale_delta_chart.py [--since 2024-01-01] [--out x.png]
"""
import argparse, io, os, sqlite3, sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, MaxNLocator
ROOT = os.path.dirname(os.path.abspath(__file__))
PDB = os.path.join(ROOT, "data", "positioning2.db")
C = dict(bg="#0e1016", grid="#2a2e39", text="#d1d4dc", muted="#8a8f9c", pos="#3fb68b", neg="#e5374a", price="#e9b949")


def delta_daily():
    c = sqlite3.connect(PDB)
    g = lambda m: pd.read_sql_query(f"SELECT ts,value FROM series WHERE src='okx' AND metric='{m}' AND tf='1D' ORDER BY ts", c).set_index("ts").value
    d = (g("top_ls_position") - g("ls_account")).dropna()
    # OKX 1D bars start 00:00 UTC+8 (16:00 UTC prev day) -> label by Taipei date
    d.index = pd.to_datetime(d.index, unit="s").tz_localize("UTC").tz_convert("Asia/Taipei").normalize().tz_localize(None)
    return d


def price_daily():
    from positioning_grid import ahr999_series
    return ahr999_series().price


def draw(ax, since="2024-01-01", fs=1.0, legend=True):
    """Same theme as positioning_grid (TH colors, panel bg, grid, white price line on right axis, green/red delta bars)."""
    from positioning_grid import TH, style, legend as glegend
    d = delta_daily(); d = d[d.index >= since]
    p = price_daily(); p = p[(p.index >= d.index[0])]
    style(ax)
    x = mdates.date2num(d.index.to_pydatetime())
    ax.bar(x, d.values, width=0.85, color=np.where(d.values >= 0, TH["up"], TH["down"]), alpha=0.85, lw=0, zorder=3)
    ax.axhline(0, color=TH["muted"], lw=0.9, zorder=2)
    m = max(0.1, float(np.abs(d.values).max()) * 1.15)
    ax.set_ylim(-m, m); ax.yaxis.set_major_locator(MaxNLocator(5))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:+.2f}" if abs(v) > 1e-9 else "0"))
    axp = ax.twinx(); axp.set_facecolor("none")
    for sp in axp.spines.values():
        sp.set_visible(False)
    axp.plot(mdates.date2num(p.index.to_pydatetime()), p.values, color=TH["price"], lw=1.4, alpha=0.9, zorder=9)
    lo, hi = p.min(), p.max(); pad = (hi - lo) * 0.08; axp.set_ylim(lo - pad, hi + pad)
    axp.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v/1000:.0f}K")); axp.yaxis.set_major_locator(MaxNLocator(5))
    axp.tick_params(axis="y", labelsize=9 * fs, colors=TH["muted"])
    ax.set_xlim(x[0] - 3, x[-1] + 3)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2)); ax.xaxis.set_major_formatter(mdates.DateFormatter("%y/%m"))
    if legend:
        glegend(ax, [("差值（柱）", TH["up"]), ("BTC 價格", TH["price"])])
    return d


def main():
    from positioning_grid import TH, head
    ap = argparse.ArgumentParser(); ap.add_argument("--since", default="2024-01-01"); ap.add_argument("--out")
    a = ap.parse_args()
    plt.rcParams.update({"font.family": ["Noto Sans CJK TC", "DejaVu Sans"], "font.size": 10.5,
                         "text.color": TH["text"], "xtick.color": TH["muted"], "ytick.color": TH["muted"]})
    fig = plt.figure(figsize=(3840 / 240, 1900 / 240), dpi=240, facecolor=TH["bg"])
    ax = fig.add_axes([0.05, 0.14, 0.9, 0.66])
    d = draw(ax, a.since)
    head(ax, "比特幣巨鯨 vs 散戶差值（日線）", f"OKX {d.iloc[-1]:+.3f} · {d.index[-1]:%m/%d}",
         "看法：大戶持倉多空比 − 全部帳戶多空比。綠＝大戶比散戶偏多；紅＝散戶比大戶偏多。Coinglass 用 Binance，數字會不同",
         "來源：OKX rubik top-trader position ratio − all-account ratio（BTC-USDT-SWAP，日線）· 價格：FRED CBBTCUSD＋Kraken")
    out = a.out or os.path.join(ROOT, "out", "positioning", f"btc_whale_vs_retail_{datetime.now(ZoneInfo('Asia/Taipei')):%Y%m%d_%H%M}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=TH["bg"]); plt.close(fig)
    from PIL import Image
    Image.open(io.BytesIO(buf.getvalue())).convert("RGB").save(out, optimize=True)
    print(os.path.abspath(out)); print(f"WHALE_DELTA {d.index[-1]:%Y-%m-%d} {d.iloc[-1]:.4f} pos_share={(d>0).mean():.2f}")


if __name__ == "__main__":
    main()

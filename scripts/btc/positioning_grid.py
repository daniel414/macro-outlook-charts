#!/usr/bin/env python3
"""「BTC 持倉數據」hourly overview (replaces the retail-sentiment grid for Daniel, 2026-10-14; grid.py kept).

  python positioning_grid.py                 # 4H, 180 bars, ~3840 px wide; prints PNG path + POSGRID_JSON line
  python positioning_grid.py --tf 1H --bars 180 --out x.png

Panels (BTC price on each; L/S ratios shown as long−short deltas in %, Daniel 2026-10-14): 1 多空差值（全部帳戶）OKX+Gate · 2 未平倉量 OKX+Gate (+Hyperliquid snapshot) ·
3 OKX 大戶多空比 (account/position) · 4 Gate 大戶多空比 (Binance substitute; Binance 451) · 5 巨鯨 vs 散戶差值 ·
6 Bitfinex 槓桿多空 · 7 Ahr999 (daily, computed from FRED CBBTCUSD + Kraken) · 8 資金費率.
Data: data/positioning2.db (posdata.py, run every 15 min by run_collect.sh); OKX funding from sentiment.db.
Descriptive only, no zones, no circles.
"""
import argparse, io, json, os, sqlite3, sys, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter, MaxNLocator
from PIL import Image
from btcsent.common import connect, OUT_DIR
from btcsent.price import bucket_start, get_klines
from btcsent.positioning import load as load_okx_pos

ROOT = os.path.dirname(os.path.abspath(__file__))
PDB = os.path.join(ROOT, "data", "positioning2.db"); MDB = os.path.join(ROOT, "data", "macro.db")
TZ = ZoneInfo("Asia/Taipei")
STEP = {"1H": 3600, "4H": 14400, "1D": 86400}
TH = dict(bg="#0e1117", panel="#0e1117", grid="#27292f", text="#cbced3", muted="#9b9ea4", price="#dfbb68",
          a="#5d82ea", b="#26a69a", c="#5d82ea", up="#2ebd85", down="#f6465d", chip="#0e1117", title="#ffffff", red="#e34668", green="#6fbf73", white="#f3f3f3")


def num(ts):
    return mdates.date2num([datetime.fromtimestamp(int(t), timezone.utc) for t in ts])


def series(con, src, metric, tf, start=None, end=None):
    q = "SELECT ts,value FROM series WHERE src=? AND metric=? AND tf=?"; p = [src, metric, tf]
    if start is not None:
        q += " AND ts>=?"; p.append(int(start))
    if end is not None:
        q += " AND ts<?"; p.append(int(end))
    df = pd.read_sql_query(q + " ORDER BY ts", con, params=p)
    return df.set_index("ts")["value"]


def longpct(r):
    return r / (1 + r) * 100


def ahr999_series():
    """Daily ahr999 = (P / 200d geometric-mean cost) * (P / 10^(5.84*log10(age days since 2009-01-03) - 17.01))."""
    m = sqlite3.connect(MDB)
    f = pd.read_sql_query("SELECT date,value FROM series WHERE id='CBBTCUSD' AND value IS NOT NULL", m)
    s = pd.Series(f.value.values, index=pd.to_datetime(f.date))
    b = pd.read_sql_query("SELECT date,value FROM series WHERE id='BCINFO' AND value>0", m)
    if not b.empty:                                        # blockchain.info fills history before FRED (2010-08→)
        bs = pd.Series(b.value.values, index=pd.to_datetime(b.date))
        s = pd.concat([bs[bs.index < s.index[0]], s])
    k = series(sqlite3.connect(PDB), "kraken", "close", "1D")
    if not k.empty:
        ks = pd.Series(k.values, index=pd.to_datetime(k.index, unit="s"))
        s = pd.concat([s[s.index < ks.index[0]], ks])        # Kraken overrides where available (fresher)
    s = s[~s.index.duplicated(keep="last")].sort_index().asfreq("D").ffill()
    gm = np.exp(np.log(s).rolling(200).mean())
    age = (s.index - pd.Timestamp("2009-01-03")).days.values
    gv = 10 ** (5.84 * np.log10(age) - 17.01)
    return pd.DataFrame({"price": s, "gm": gm, "ahr": (s / gm) * (s / gv)}).dropna()


def style(ax):
    ax.set_facecolor(TH["panel"])
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.grid(axis="y", color=TH["grid"], lw=0.7); ax.grid(axis="x", visible=False)
    ax.tick_params(labelsize=9.5)


def add_price(ax, kl, w, label=True):
    axp = ax.twinx(); axp.set_facecolor("none")
    for sp in axp.spines.values():
        sp.set_visible(False)
    axp.plot(num(kl.index) + w / 2, kl.c, color=TH["price"], lw=1.4, alpha=0.9, zorder=9)
    lo, hi = kl.l.min(), kl.h.max(); pad = (hi - lo) * 0.08
    axp.set_ylim(lo - pad, hi + pad)
    axp.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v/1000:.0f}K" if hi > 2e5 else f"{v/1000:.1f}K"))
    axp.yaxis.set_major_locator(MaxNLocator(5)); axp.tick_params(axis="y", labelsize=9, colors=TH["muted"])
    return axp


def head(ax, title, value, expl, src):
    ax.text(0, 1.17, title, transform=ax.transAxes, fontsize=16, fontweight="bold", color=TH["title"], va="bottom")
    ax.text(1, 1.17, value, transform=ax.transAxes, fontsize=13, fontweight="bold", color=TH["text"], ha="right", va="bottom")
    ax.text(0, 1.085, expl, transform=ax.transAxes, fontsize=10.5, color=TH["muted"], va="bottom")
    ax.text(0, -0.11, src, transform=ax.transAxes, fontsize=8.5, color=TH["muted"], va="top")


def _cglegend(ax, items, fs=10):
    n = len(items); step = 0.2 if n > 3 else 0.26; x0 = 0.5 - step * (n - 1) / 2
    for i, (lab, col) in enumerate(items):
        x = x0 + i * step
        ax.text(x, 1.005, "■", transform=ax.transAxes, fontsize=fs + 1, color=col, ha="right", va="bottom", zorder=10)
        ax.text(x + 0.004, 1.005, lab, transform=ax.transAxes, fontsize=fs, color=TH["text"], ha="left", va="bottom", zorder=10)


def legend(ax, items):
    for i, (lab, col) in enumerate(items):
        pass
    _cglegend(ax, items)


def stepline(ax, s, w, col, lw=1.6, fill=False):
    if s is None or s.empty:
        return
    x = num(s.index); xe = np.append(x, x[-1] + w); y = np.append(s.values, s.values[-1])
    ax.step(xe, y, where="post", color=col, lw=lw, zorder=4)
    if fill:
        ax.fill_between(xe, y, np.nanmin(y), step="post", color=col, alpha=0.14, lw=0, zorder=3)


def lsdelta(r):
    """long% − short% in percentage points, long% = r/(1+r): 56/44 -> +12."""
    return (r - 1) / (r + 1) * 100


def deltabars(ax, s, w, line=None, line_col=None):
    """Green/red bars around 0 (whale-vs-retail style) + optional comparison line."""
    if s is not None and not s.empty:
        ax.bar(num(s.index), s.values, width=w * 0.85, align="edge", color=np.where(s.values >= 0, TH["up"], TH["down"]), alpha=0.85, lw=0, zorder=3)
    if line is not None and not line.empty:
        stepline(ax, line, w, line_col, lw=1.5)
    ax.axhline(0, color=TH["muted"], lw=0.9, zorder=2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:+.0f}" if v else "0"))
    vals = pd.concat([x for x in (s, line) if x is not None and not x.empty])
    m = max(5.0, float(np.nanmax(np.abs(vals.values))) * 1.15) if len(vals) else 10
    ax.set_ylim(-m, m)


def binance_tf(con, step, start, end):
    """Binance 5m metrics -> last value per grid bucket (ts = bucket start). Empty DataFrame if no table/data."""
    try:
        df = pd.read_sql_query("SELECT * FROM binance WHERE ts>=? AND ts<? ORDER BY ts", con, params=[int(start), int(end)])
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    df["b"] = (df.ts // step) * step
    out = df.groupby("b").last(); out.attrs["last_ts"] = int(df.ts.iloc[-1]); return out


BN_COL = "#c77dff"; BN_LAB = "Binance（延遲 1–2 天）"


def bn_mark(ax, s, w, col=BN_COL):
    """Dot + Taipei-time label at the end of the (lagged) Binance series."""
    if s is None or s.empty:
        return
    x = num([s.index[-1]])[0] + w; y = float(s.iloc[-1])
    ax.plot([x], [y], "o", color=col, ms=5, zorder=6)
    ax.annotate(f"Binance 截至 {datetime.fromtimestamp(BN_LAST, TZ):%m/%d %H:%M}", (x, y), xytext=(-6, 10), textcoords="offset points",
                ha="right", fontsize=9, color=col, zorder=7, bbox=dict(fc=TH["chip"], ec="none", alpha=0.85, pad=1.5))


BN_LAST = None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tf", default="4H", choices=list(STEP)); ap.add_argument("--bars", type=int, default=180)
    ap.add_argument("--ahr-days", type=int, default=0)
    ap.add_argument("--dpi", type=float, default=120); ap.add_argument("--font-scale", type=float, default=1.4)
    ap.add_argument("--out")
    a = ap.parse_args()
    step = STEP[a.tf]; now = time.time(); w = step / 86400
    end_b = bucket_start(int(now), step) + step; start_b = end_b - a.bars * step
    con = sqlite3.connect(PDB)
    S = lambda src, m, tf=a.tf: series(con, src, m, tf, start_b, end_b)
    kl, exch = get_klines(a.tf.lower(), int(start_b), int(end_b)); kl = kl[(kl.index >= start_b) & (kl.index < end_b)]
    fails, cur = [], {}
    global BN_LAST
    bn = binance_tf(con, step, start_b, end_b)
    if not bn.empty:
        BN_LAST = bn.attrs["last_ts"]
    else:
        fails.append("binance: no data in window")
    BS = lambda c: bn[c].dropna() if not bn.empty else pd.Series(dtype=float)

    plt.rcParams.update({"font.family": ["Noto Sans CJK TC", "DejaVu Sans"], "font.size": 10.5,
                         "text.color": TH["text"], "xtick.color": TH["muted"], "ytick.color": TH["muted"]})
    fs = a.font_scale
    fig = plt.figure(figsize=(32 / fs, 30 / fs), dpi=a.dpi * fs, facecolor=TH["bg"])
    gs = fig.add_gridspec(4, 2, left=0.04, right=0.955, top=0.875, bottom=0.06, hspace=0.62, wspace=0.11)
    axs = [fig.add_subplot(gs[i // 2, i % 2]) for i in range(8)]
    for ax in axs:
        style(ax)
    xlim = (num([start_b])[0], num([end_b])[0])
    last = lambda s: (float(s.iloc[-1]), datetime.fromtimestamp(int(s.index[-1]), TZ).strftime("%m/%d %H:%M")) if s is not None and not s.empty else (None, None)
    pctl = lambda s: float((s < s.iloc[-1]).mean() * 100)

    # 1 all-account long−short delta
    ax = axs[0]; o, g = lsdelta(S("okx", "ls_account")), lsdelta(S("gate", "ls_account"))
    bg = lsdelta(BS("global_account"))
    deltabars(ax, o, w, g, TH["b"]); stepline(ax, bg, w, BN_COL, lw=1.4); bn_mark(ax, bg, w)
    lim = max(abs(v) for v in ax.get_ylim()); 
    if not bg.empty: lim = max(lim, float(np.nanmax(np.abs(bg.values))) * 1.15); ax.set_ylim(-lim, lim)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:+.0f}%" if abs(v) > 1e-9 else "0%"))
    vo, vg = last(o)[0], last(g)[0]; cur["多空差值_全部_okx"], cur["多空差值_全部_gate"] = vo, vg
    head(ax, "多空差值（全部帳戶）", f"OKX {vo:+.1f}% · Gate {vg:+.1f}%" if vo is not None and vg is not None else "資料不足",
         "看法：例 56% 做多／44% 做空＝+12%。綠＝較多人做多；帳戶多的一方通常是散戶",
         "來源：OKX rubik long-short-account-ratio-contract（柱）、Gate.io lsr_account（青線）、data.binance.vision count_long_short_ratio（紫線）")
    legend(ax, [("OKX（柱）", TH["up"]), ("Gate", TH["b"]), (BN_LAB, BN_COL)]); add_price(ax, kl, w)

    # 2 OI
    ax = axs[1]; o, g = S("okx", "oi_usd"), S("gate", "oi_usd")
    stepline(ax, o / 1e9, w, TH["a"], fill=True); stepline(ax, g / 1e9, w, TH["b"])
    bo = BS("oi_usd"); stepline(ax, bo / 1e9, w, BN_COL, lw=1.4); bn_mark(ax, bo / 1e9, w)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"${v:.1f}B"))
    hl = series(con, "hyperliquid", "oi_usd", "snap"); vh = last(hl)[0]
    vo, vg = last(o)[0], last(g)[0]; cur.update({"未平倉_okx_usd": vo, "未平倉_gate_usd": vg, "未平倉_hyperliquid_usd": vh})
    chg = (o.iloc[-1] / o.iloc[-7] - 1) * 100 if len(o) > 7 else float("nan")
    head(ax, "未平倉量", (f"OKX ${vo/1e9:.2f}B ({chg:+.1f}%/6根) · Gate ${vg/1e9:.2f}B" if vo and vg else "資料不足") + (f" · Hyperliquid ${vh/1e9:.2f}B" if vh else ""),
         "看法：未平倉合約總值。價漲＋OI 增＝新資金追多；價跌＋OI 增＝加空；OI 急降＝平倉或爆倉", "來源：OKX open-interest-history、Gate.io contract_stats、data.binance.vision sum_open_interest_value、Hyperliquid（僅即時快照）")
    legend(ax, [("OKX", TH["a"]), ("Gate", TH["b"]), (BN_LAB, BN_COL)]); add_price(ax, kl, w)

    # 3 / 4 top-trader long−short delta (bars = position, line = account)
    for i, src, ttl, expl, srcl in ((2, "okx", "OKX 大戶多空差值", "看法：OKX 前 5% 保證金大戶。柱＝大戶多空單量差值；藍線＝多空大戶數差值，例 56%／44%＝+12%",
                                     "來源：OKX rubik long-short-{position,account}-ratio-contract-top-trader (BTC-USDT-SWAP)"),
                                    (3, "gate", "Binance / Gate 大戶多空差值", "看法：柱＝Gate 大戶持倉量差值（即時）、藍線＝Gate 大戶帳戶數差值（即時）；紫線＝Binance 大戶持倉差值（延遲 1–2 天）",
                                     "來源：Gate.io contract_stats top_lsr_size / top_lsr_account（即時）、data.binance.vision sum_toptrader_long_short_ratio（官方每日檔）")):
        ax = axs[i]; tp, ta = lsdelta(S(src, "top_ls_position")), lsdelta(S(src, "top_ls_account"))
        deltabars(ax, tp, w, ta, TH["a"])
        vp, va = last(tp)[0], last(ta)[0]; cur[f"多空差值_{src}_大戶持倉"], cur[f"多空差值_{src}_大戶帳戶"] = vp, va
        val = f"持倉 {vp:+.1f}% · 帳戶 {va:+.1f}%" if vp is not None and va is not None else "資料不足"
        items = [("持倉（柱）", TH["up"]), ("帳戶數", TH["a"])]
        if src == "gate":
            btp = lsdelta(BS("top_position")); stepline(ax, btp, w, BN_COL, lw=1.4); bn_mark(ax, btp, w)
            if not btp.empty:
                lim = max(max(abs(v) for v in ax.get_ylim()), float(np.nanmax(np.abs(btp.values))) * 1.15); ax.set_ylim(-lim, lim)
                val = f"Gate 持倉 {vp:+.1f}% · 帳戶 {va:+.1f}% · Binance {float(btp.iloc[-1]):+.1f}%"
            items = [("Gate 持倉（柱）", TH["up"]), ("Gate 帳戶數", TH["a"]), (BN_LAB, BN_COL)]
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:+.0f}%" if abs(v) > 1e-9 else "0%"))
        head(ax, ttl, val, expl, srcl)
        legend(ax, items); add_price(ax, kl, w)

    # 5 whale vs retail delta at grid tf (same style as other 多空差值 panels); daily value kept for JSON compat
    ax = axs[4]
    try:
        wd = (S("okx", "top_ls_position") - S("okx", "ls_account")).dropna()
        bwd = (BS("top_position") - BS("global_account")).dropna()
        deltabars(ax, wd, w); stepline(ax, bwd, w, BN_COL, lw=1.4); bn_mark(ax, bwd, w)
        both = pd.concat([wd, bwd])
        m = max(0.1, float(np.nanmax(np.abs(both.values))) * 1.15) if not both.empty else 1
        ax.set_ylim(-m, m); ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:+.2f}" if abs(v) > 1e-9 else "0"))
        vw, tw = last(wd); cur[f"巨鯨散戶差_okx_{a.tf}"] = vw
        try:
            import whale_delta_chart as WD
            d = WD.delta_daily(); cur["巨鯨散戶差_okx_日線"] = float(d.iloc[-1]); cur["巨鯨散戶差_日期"] = str(d.index[-1].date())
        except Exception as e:
            fails.append(f"whale daily: {e!r}")
        head(ax, "巨鯨 vs 散戶差值", f"OKX {vw:+.3f} · {tw}" if vw is not None else "資料不足",
             "看法：大戶持倉多空比 − 全部帳戶多空比。綠＝大戶比散戶偏多；紅＝散戶比大戶偏多。紫線＝Binance（Coinglass 同定義）",
             "來源：OKX rubik top-trader position ratio − account ratio（柱）、data.binance.vision sum_toptrader − count_long_short_ratio（紫線）")
        legend(ax, [("OKX 差值（柱）", TH["up"]), (BN_LAB, BN_COL)]); add_price(ax, kl, w)
    except Exception as e:
        fails.append(f"whale delta: {e!r}"); head(ax, "巨鯨 vs 散戶差值", "繪製失敗", "", "")

    # 6 Bitfinex margin long−short delta = (long − short)/(long + short) %
    ax = axs[5]
    bl = series(con, "bitfinex", "long", "1H", start_b, end_b); bs = series(con, "bitfinex", "short", "1H", start_b, end_b)
    if a.tf != "1H" and not bl.empty:
        bl = bl.groupby(bucket_start(bl.index.to_numpy(), step)).last(); bs = bs.groupby(bucket_start(bs.index.to_numpy(), step)).last()
    bd = ((bl - bs) / (bl + bs) * 100).dropna()
    deltabars(ax, bd, w)
    if not bd.empty:
        lo_, hi_ = float(bd.min()), float(bd.max()); pad = max(0.05, (hi_ - lo_) * 0.15)
        ax.set_ylim(lo_ - pad, hi_ + pad)   # near +99%: zoom so changes are visible
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:+.2f}%"))
    vl, vs = last(bl)[0], last(bs)[0]; vd = last(bd)[0]
    cur["多空差值_bitfinex_pct"], cur["bitfinex_多_btc"], cur["bitfinex_空_btc"] = vd, vl, vs
    if vd is None:
        fails.append("bitfinex: no data")
    head(ax, "Bitfinex 槓桿多空差值", f"{vd:+.2f}% · 多 {vl:,.0f} / 空 {vs:,.0f} BTC" if vd is not None else "資料不足",
         "看法：(多單 − 空單) ÷ (多單 + 空單)。Bitfinex 槓桿幾乎全是多單，所以看變化：下降＝空單增加或多單減少",
         "來源：Bitfinex stats1 pos.size tBTCUSD long/short（1 分鐘資料重取樣）· 縱軸已放大，未從 0 起算")
    add_price(ax, kl, w)

    # 7 Ahr999 daily — full history, log scale (Coinglass style)
    ax = axs[6]
    try:
        ah = ahr999_series()
        if a.ahr_days > 0:
            ah = ah.iloc[-a.ahr_days:]
        x = mdates.date2num(ah.index.to_pydatetime())
        ax.set_yscale("log")
        ax.axhspan(1e-3, 0.45, color="#3a1a26", alpha=0.55, lw=0, zorder=1)
        ax.axhspan(0.45, 1.2, color="#131825", alpha=0.9, lw=0, zorder=1)
        ax.plot(x, ah.ahr, color=TH["c"], lw=1.0, zorder=4)
        ax.fill_between(x, ah.ahr, 1e-3, color=TH["c"], alpha=0.10, lw=0, zorder=2)
        for lv, col in ((0.45, TH["red"]), (1.2, TH["green"])):
            ax.axhline(lv, color=col, lw=1.1, zorder=3)
        ax.set_ylim(0.1, 1000); ax.set_xlim(x[0], x[-1] + 20)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:g}"))
        axp = ax.twinx(); [sp.set_visible(False) for sp in axp.spines.values()]
        axp.set_yscale("log")
        axp.plot(x, ah.gm, color=TH["white"], lw=0.8, alpha=0.85, zorder=5)
        axp.plot(x, ah.price, color=TH["price"], lw=1.1, alpha=0.9, zorder=6)
        axp.set_ylim(0.01, 1e6)
        axp.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"${v/1000:g}K" if v >= 1000 else f"${v:g}"))
        axp.tick_params(axis="y", labelsize=9, colors=TH["muted"])
        ax.xaxis.set_major_locator(mdates.YearLocator(2)); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        n_hi, n_mid, n_lo = int((ah.ahr > 1.2).sum()), int(((ah.ahr >= 0.45) & (ah.ahr <= 1.2)).sum()), int((ah.ahr < 0.45).sum())
        cur["ahr999_days_gt_1_2"], cur["ahr999_days_045_12"], cur["ahr999_days_lt_045"] = n_hi, n_mid, n_lo
        for j, (lab, n) in enumerate(((f"Ahr999 > 1.2", n_hi), ("Ahr999 0.45 – 1.2", n_mid), ("Ahr999 < 0.45", n_lo))):
            ax.text(0.01 + j * 0.2, -0.10, lab, transform=ax.transAxes, fontsize=9, color=TH["muted"], va="top")
            ax.text(0.01 + j * 0.2, -0.155, f"{n:,} 天", transform=ax.transAxes, fontsize=12, fontweight="bold", color=TH["white"], va="top")
        va = float(ah.ahr.iloc[-1]); cur["ahr999"] = round(va, 3); cur["ahr999_date"] = str(ah.index[-1].date())
        zone = "抄底區（<0.45）" if va < 0.45 else ("定投區（0.45–1.2）" if va <= 1.2 else "高於定投線（>1.2）")
        head(ax, "Ahr999 指數（日線，全歷史）", f"{va:.2f} · {zone} · {ah.index[-1]:%m/%d}",
             "看法：<0.45＝歷史抄底區；0.45–1.2＝適合定投；>1.2＝偏貴。長線估值指標，不看短線",
             "")
        ax.text(0, -0.25, "計算：(價格÷200 日幾何平均)×(價格÷10^(5.84×log10(幣齡天數)−17.01)) · 價格：blockchain.info(2010–14)＋FRED CBBTCUSD＋Kraken · 對數座標", transform=ax.transAxes, fontsize=8.5, color=TH["muted"], va="top")
        _cglegend(ax, [("200日成本", TH["white"]), ("BTC 價格", TH["price"]), ("Ahr999", TH["c"]), ("抄底線 0.45", TH["red"]), ("定投線 1.2", TH["green"])], fs=9.5)
    except Exception as e:
        fails.append(f"ahr999: {e!r}"); head(ax, "Ahr999 指數（日線）", "計算失敗", "", "")

    # 8 funding
    ax = axs[7]
    fo = load_okx_pos(connect(), "funding_realized", "8h", start_b, end_b) * 100
    fg = S("gate", "funding") * 100
    if not fo.empty:
        ax.bar(num(fo.index), fo.values, width=8 / 24 * 0.8, align="edge", color=np.where(fo.values >= 0, TH["up"], TH["down"]), alpha=0.85, lw=0)
    stepline(ax, fg, w, TH["b"], lw=1.3)
    ax.axhline(0, color=TH["muted"], lw=0.7); ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:.3f}%"))
    hf = last(series(con, "hyperliquid", "funding_1h", "snap"))[0]
    vo, vg = last(fo)[0], last(fg)[0]; cur.update({"資金費率_okx_8h_pct": vo, "資金費率_gate_pct": vg, "資金費率_hyperliquid_1h_pct": hf * 100 if hf is not None else None})
    head(ax, "資金費率", (f"OKX {vo:+.4f}%/8h" if vo is not None else "OKX —") + (f" · Gate {vg:+.4f}%" if vg is not None else "") + (f" · HL {hf*100:+.4f}%/1h" if hf is not None else ""),
         "看法：正（綠）＝多方付錢給空方，多單擁擠；負（紅）＝空方付錢，空單擁擠", "來源：OKX 已結算資金費率（柱）、Gate.io last_funding_rate（青線）、Hyperliquid 即時")
    legend(ax, [("OKX", TH["up"]), ("Gate", TH["b"])]); add_price(ax, kl, w)

    for i, ax in enumerate(axs):
        if i != 6:
            ax.set_xlim(*xlim); ax.yaxis.set_major_locator(MaxNLocator(5))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=TZ, minticks=6, maxticks=12))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=TZ))
    nowt = datetime.now(TZ)
    fig.text(0.04, 0.972, "BTC 持倉數據", fontsize=30, fontweight="bold", color=TH["title"], va="center")
    fig.text(0.04 + 0.13, 0.972, f"{a.tf} · {a.bars} 根（{a.bars*step/86400:g} 天）· BTC {kl.c.iloc[-1]/1000:.1f}K · 金線＝BTC 價格（{exch}）",
             fontsize=16, color=TH["text"], va="center")
    fig.text(0.955, 0.972, f"{nowt:%Y/%m/%d %H:%M} UTC+8", fontsize=16, color=TH["muted"], ha="right", va="center")
    fig.text(0.04, 0.948, "全部為免費公開資料。持倉數據描述「誰站在哪邊」，尚未回測過預測力；Ahr999 為長線估值。", fontsize=12, color=TH["muted"], va="center")

    out = a.out or str(OUT_DIR / "positioning" / f"btc_positioning_overview_{nowt:%Y%m%d_%H%M%S}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=TH["bg"], dpi=a.dpi * fs); plt.close(fig)
    Image.open(io.BytesIO(buf.getvalue())).convert("RGB").save(out, "PNG", optimize=True)
    print(os.path.abspath(out))
    if not bn.empty:
        lb = bn.iloc[-1]
        cur.update({"binance_多空差值_全部": lsdelta(float(lb.global_account)), "binance_多空差值_大戶持倉": lsdelta(float(lb.top_position)),
                    "binance_多空差值_大戶帳戶": lsdelta(float(lb.top_account)), "binance_巨鯨散戶差": float(lb.top_position - lb.global_account),
                    "binance_未平倉_usd": float(lb.oi_usd), "binance_主動買賣比": float(lb.taker_ratio),
                    "binance_asof": datetime.fromtimestamp(BN_LAST, TZ).strftime("%Y-%m-%d %H:%M UTC+8")})
    cur["btc"] = round(float(kl.c.iloc[-1]), 1); cur["fails"] = fails
    print("POSGRID_JSON " + json.dumps({k: (round(v, 6) if isinstance(v, float) else v) for k, v in cur.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()

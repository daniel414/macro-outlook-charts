#!/usr/bin/env python3
"""BTC positioning overview (2×2 mobile-readable): leverage stack | whale vs retail | Bitfinex | Ahr999.
Leverage stack (CoinGlass order): OKX perp OHLC → aggregate OI candles → futures CVD (OKX+Bitget raw taker)
/ spot CVD (BN+OKX) → OI-weighted funding. No Binance fapi. Prints POSGRID_JSON + png path.
"""
import argparse, io, json, os, sqlite3, sys, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE) if os.path.basename(_HERE) == "scripts" else _HERE
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter, MaxNLocator, MaxNLocator
from PIL import Image
from btcsent.common import connect, OUT_DIR
from btcsent.price import bucket_start, get_klines
from btcsent.positioning import load as load_okx_pos, pos_con

ROOT = _ROOT
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


def gate_series(con, metric, tf, start=None, end=None):
    """Gate contract_stats rows are a snapshot taken when the bucket opens and are only finalised at close (= the last 1H row
    in the bucket), so the in-progress 4H/1D row stays frozen at its opening value for up to a whole bar (2026-10-19: 09:12–11:16
    publishes showed the 08:00 values). Use the last 1H row per bucket instead; closed buckets are identical."""
    s = series(con, "gate", metric, tf, start, end)
    if tf == "1H":
        return s
    h = series(con, "gate", metric, "1H", start, end)
    if h.empty:
        return s
    return h.groupby(bucket_start(h.index.to_numpy(), STEP[tf])).last().combine_first(s).sort_index()


STALE, ASOF = {}, {}
FUND_OKX_MAX = 9 * 3600     # OKX settles every 8h; the last realized rate may legitimately be up to 8h old
SNAP_MAX = 2 * 3600


def tf_max(tf):
    return STEP.get(tf, 3600) + 2 * 3600   # ts = bar start, so a current bar can be up to one bar old


def _maxts(con, src, metric, tf):
    return con.execute("SELECT MAX(ts) FROM series WHERE src=? AND metric=? AND tf=?", (src, metric, tf)).fetchone()[0] or 0


def freshen(tf, fails):
    """Refresh OKX funding live every run; refetch any OI/funding source whose newest row is stale (free public APIs)."""
    try:
        from btcsent.positioning import update as okx_update
        okx_update(pos_con(), max_pages=1)
    except Exception as e:
        fails.append(f"okx_funding_refresh: {e!r}")
    now = time.time(); con = sqlite3.connect(PDB, timeout=60)
    checks = {"okx": [("oi_usd", tf)], "gate": [("oi_usd", tf), ("funding", tf)]}
    need = [src for src, ms in checks.items()
            if any(now - _maxts(con, src, m, t) > (SNAP_MAX if t == "snap" else tf_max(t)) for m, t in ms)]
    con.close()
    if need:
        try:
            import posdata as PD
            pc = PD.db()
            for src in need:
                try:
                    {"okx": lambda: PD.okx(pc, False), "gate": lambda: PD.gate(pc, False)}[src]()
                except Exception as e:
                    fails.append(f"{src}_refetch: {e!r}")
            pc.close()
        except Exception as e:
            fails.append(f"posdata_refetch: {e!r}")


def fresh(key, s, max_age):
    """Last value of s, or None (recorded in STALE) when its timestamp is older than max_age seconds."""
    if s is None or s.empty:
        return None
    ts, v = int(s.index[-1]), float(s.iloc[-1])
    asof = datetime.fromtimestamp(ts, TZ).strftime("%m/%d %H:%M"); ASOF[key] = asof
    if time.time() - ts > max_age:
        STALE[key] = {"value": round(v, 8), "asof": asof + " UTC+8"}
        return None
    return v


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
    ax.tick_params(labelsize=11)


def add_price(ax, kl, w, label=True):
    axp = ax.twinx(); axp.set_facecolor("none")
    for sp in axp.spines.values():
        sp.set_visible(False)
    axp.plot(num(kl.index) + w / 2, kl.c, color=TH["price"], lw=2.2, alpha=0.95, zorder=9)
    lo, hi = kl.l.min(), kl.h.max(); pad = (hi - lo) * 0.08
    axp.set_ylim(lo - pad, hi + pad)
    axp.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v/1000:.0f}K" if hi > 2e5 else f"{v/1000:.1f}K"))
    axp.yaxis.set_major_locator(MaxNLocator(5)); axp.tick_params(axis="y", labelsize=9, colors=TH["muted"])
    return axp


def end_tag(ax, series, fmt, color, dy=0):
    """曲線／柱末端右側標最新值（手機對齊 CoinGlass）。"""
    if series is None or len(series) == 0:
        return
    s = series.dropna()
    if s.empty:
        return
    x = num([s.index[-1]])[0]
    y = float(s.iloc[-1]) + dy
    ax.annotate(
        fmt.format(y),
        xy=(x, y), xytext=(6, 0), textcoords="offset points",
        fontsize=11, fontweight="bold", color=color, va="center",
        bbox=dict(boxstyle="round,pad=0.2", fc="#1a1d24", ec=color, lw=0.7, alpha=0.9),
        clip_on=False, zorder=15,
    )


def head(ax, title, value, expl, src):
    # 手機可讀：標題伸出縮短，避免與 fig 總標題／鄰格重疊
    ax.text(0, 1.10, title, transform=ax.transAxes, fontsize=16, fontweight="bold", color=TH["title"], va="bottom")
    ax.text(1, 1.10, value, transform=ax.transAxes, fontsize=13, fontweight="bold", color=TH["text"], ha="right", va="bottom")
    ax.text(0, 1.03, expl, transform=ax.transAxes, fontsize=11, color=TH["muted"], va="bottom")
    ax.text(0, -0.10, src, transform=ax.transAxes, fontsize=9, color=TH["muted"], va="top")


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


def stepline(ax, s, w, col, lw=2.0, fill=False):
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
        ax.bar(num(s.index), s.values, width=w * 0.88, align="edge", color=np.where(s.values >= 0, TH["up"], TH["down"]), alpha=0.9, lw=0, zorder=3)
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


BN_COL = "#c77dff"; BN_LAB = "Binance"


def bn_mark(ax, s, w, col=BN_COL):
    """Dot + Taipei-time label at the end of the (lagged) Binance series."""
    if s is None or s.empty:
        return
    x = num([s.index[-1]])[0] + w; y = float(s.iloc[-1])
    ax.plot([x], [y], "o", color=col, ms=5, zorder=6)
    ax.annotate(f"Binance 截至 {datetime.fromtimestamp(BN_LAST, TZ):%m/%d %H:%M}", (x, y), xytext=(-6, 10), textcoords="offset points",
                ha="right", fontsize=9, color=col, zorder=7, bbox=dict(fc=TH["chip"], ec="none", alpha=0.85, pad=1.5))


BN_LAST = None




def _in_tag(ax, y, text, color, fontsize=10):
    """單元格內右側最新值標籤（避免超出 subplot 被裁切）。"""
    ax.text(
        0.99, y, text, transform=ax.get_yaxis_transform(),
        fontsize=fontsize, fontweight="bold", color=color,
        va="center", ha="right",
        bbox=dict(boxstyle="round,pad=0.22", fc="#1a1d24", ec=color, lw=0.8, alpha=0.92),
        clip_on=False, zorder=20,
    )


def draw_leverage_stack_cell(ax):
    """右上角：價→OI合計K→CVD合計→OI加權費率（CoinGlass 序／配色）。"""
    import leverage_cvd as L
    from matplotlib.gridspec import GridSpecFromSubplotSpec
    ss = ax.get_subplotspec()
    fig = ax.figure
    ax.remove()
    inner = GridSpecFromSubplotSpec(4, 1, subplot_spec=ss, hspace=0.16, height_ratios=[1.4, 1.15, 1.35, 1.0])
    axes = [fig.add_subplot(inner[i]) for i in range(4)]
    for a in axes:
        a.set_facecolor(TH["bg"])
        for sp in a.spines.values():
            sp.set_color(TH["grid"]); sp.set_linewidth(0.5)
        a.tick_params(colors=TH["muted"], labelsize=9)
        a.grid(True, color=TH["grid"], lw=0.5, alpha=0.8)

    days = 10.0
    hours = int(days * 24)
    # v4：完整 OHLC + 合約 CVD 僅原始 taker
    try:
        candles, _pm = L.fetch_okx_perp_candles(hours + 5)
    except Exception:
        candles, _pm = L.fetch_bn_spot_ohlc(hours + 5)
    if not candles:
        raise RuntimeError("no OHLC candles for leverage stack")
    price_by_ts = {int(c["ts"]): float(c["c"]) for c in candles}
    start_ts = candles[0]["ts"]; end_ts = candles[-1]["ts"] + 3600
    xlim = (
        mdates.date2num(datetime.fromtimestamp(start_ts, timezone.utc)),
        mdates.date2num(datetime.fromtimestamp(end_ts, timezone.utc)),
    )

    okx_f, okx_fm = L.fetch_okx_futures_cvd(hours + 10, price_by_ts=price_by_ts)
    bitget_f, bitget_fm = L.fetch_bitget_futures_cvd(hours + 10, price_by_ts=price_by_ts)
    fut, fut_meta = L.merge_delta_series([
        ("OKX", okx_f, okx_fm), ("Bitget", bitget_f, bitget_fm),
        ("Gate", [], {"kind": "excluded_estimate", "n": 0}),
        ("Bybit", [], {"kind": "unavailable", "n": 0, "error": "無公開歷史 taker"}),
    ])
    bn_s, bn_sm = L.fetch_bn_spot_delta(hours + 10)
    okx_s, okx_sm = L.fetch_okx_spot_delta(hours + 10, price_by_ts=price_by_ts)
    cb_s, cb_sm = L.fetch_coinbase_spot_delta(hours + 10)
    spot, spot_meta = L.merge_delta_series([
        ("Binance", bn_s, bn_sm), ("OKX", okx_s, okx_sm), ("Coinbase", cb_s, cb_sm),
    ])
    oi_venues, _ = L.fetch_oi_venues(hours + 10)
    for r in oi_venues.get("bybit") or []:
        if not r.get("usd"):
            px = price_by_ts.get(int(r["ts"])) or 0
            r["usd"] = float(r.get("btc") or 0) * px
    oi_candles, oi_meta = L.aggregate_oi_candles(oi_venues, price_by_ts, hours + 2)
    oi_maps = {n: {int(r["ts"]): float(r.get("usd") or 0) for r in rows} for n, rows in oi_venues.items()}
    fund, fund_meta = L.fetch_funding_oi_weighted(oi_maps, n=80)

    fx, fy = L.cum_delta(fut, start_ts)
    sx, sy = L.cum_delta(spot, start_ts)

    a0 = axes[0]
    L.draw_candles(a0, candles, width_days=1/24*0.65)
    a0.set_xlim(*xlim)
    lo = min(c["l"] for c in candles); hi = max(c["h"] for c in candles)
    pad = (hi - lo) * 0.10; a0.set_ylim(lo - pad, hi + pad)
    a0.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    a0.yaxis.set_major_locator(MaxNLocator(3))
    _in_tag(a0, candles[-1]["c"], f"{candles[-1]['c']:,.0f}", TH["title"], 10)
    a0.set_title("槓桿力道 · 價→OI→CVD→費率（10D·原始 taker）", fontsize=13, fontweight="bold", color=TH["title"], loc="left", pad=6)
    a0.tick_params(labelbottom=False)

    a1 = axes[1]
    L.draw_candles(a1, oi_candles, width_days=1/24*0.65, up="#0ecb81", dn="#f6465d")
    a1.set_xlim(*xlim)
    if oi_candles:
        lo = min(c["l"] for c in oi_candles); hi = max(c["h"] for c in oi_candles)
        pad = (hi - lo) * 0.10 or 1e8
        a1.set_ylim(lo - pad, hi + pad)
        _in_tag(a1, oi_candles[-1]["c"]/1e9, f"OI ${oi_candles[-1]['c']/1e9:.2f}B", "#4c82fb", 9)
    a1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v/1e9:.1f}B"))
    a1.tick_params(labelbottom=False)

    a2 = axes[2]
    if len(fx):
        a2.plot(L.to_num(fx), fy/1e6, color="#4c82fb", lw=2.0, label="合約 "+"+".join(fut_meta.get("included") or []))
        _in_tag(a2, fy[-1]/1e6, f"合約 {fy[-1]/1e6:+.1f}M", "#4c82fb", 9)
    if len(sx):
        a2.plot(L.to_num(sx), sy/1e6, color="#f0b90b", lw=2.0, label="現貨 "+"+".join(spot_meta.get("included") or []))
        _in_tag(a2, sy[-1]/1e6, f"現貨 {sy[-1]/1e6:+.1f}M", "#f0b90b", 9)
    a2.axhline(0, color=TH["muted"], lw=0.5); a2.set_xlim(*xlim)
    a2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}M"))
    a2.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=TH["text"], ncol=1)
    a2.tick_params(labelbottom=False)

    a3 = axes[3]
    fo = [r for r in fund if r["ts"] >= start_ts - 8*3600]
    if fo:
        xs = L.to_num([r["ts"] for r in fo]); ys = [r["pct"] for r in fo]
        cols = [TH["up"] if y >= 0 else TH["down"] for y in ys]
        a3.bar(xs, ys, width=7.5/24, align="center", color=cols, alpha=0.9)
        _in_tag(a3, ys[-1], f"加權 {ys[-1]:+.4f}%", TH["up"] if ys[-1] >= 0 else TH["down"], 9)
    a3.axhline(0, color=TH["muted"], lw=0.5); a3.set_xlim(*xlim)
    a3.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.3f}%"))
    a3.xaxis.set_major_locator(mdates.DayLocator(interval=2, tz=TZ))
    a3.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=TZ))
    def _ft(ts):
        return datetime.fromtimestamp(ts, TZ).strftime("%m/%d %H:%M") if ts else "—"
    a3.text(0, -0.32,
            f"CVD {_ft(fut_meta.get('start_ts'))}→{_ft(fut_meta.get('end_ts'))} · OI合計 {_ft(oi_meta.get('start_ts'))}→{_ft(oi_meta.get('end_ts'))} · 原始taker·非fapi",
            transform=a3.transAxes, fontsize=8, color=TH["muted"], va="top")
    return axes



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tf", default="4H", choices=list(STEP)); ap.add_argument("--bars", type=int, default=180)
    ap.add_argument("--ahr-days", type=int, default=0)
    ap.add_argument("--dpi", type=float, default=130); ap.add_argument("--font-scale", type=float, default=1.55)
    ap.add_argument("--out")
    a = ap.parse_args()
    step = STEP[a.tf]; now = time.time(); w = step / 86400
    end_b = bucket_start(int(now), step) + step; start_b = end_b - a.bars * step
    con = sqlite3.connect(PDB)
    S = lambda src, m, tf=a.tf: gate_series(con, m, tf, start_b, end_b) if src == "gate" else series(con, src, m, tf, start_b, end_b)
    kl, exch = get_klines(a.tf.lower(), int(start_b), int(end_b)); kl = kl[(kl.index >= start_b) & (kl.index < end_b)]
    fails, cur = [], {}
    freshen(a.tf, fails)
    global BN_LAST, BN_LAB
    bn = binance_tf(con, step, start_b, end_b)
    if not bn.empty:
        BN_LAST = bn.attrs["last_ts"]
        # live www.binance.com rows are <=2h old; older = daily-dump fallback, show as-of time
        BN_LAB = "Binance" if now - BN_LAST <= 2 * 3600 else f"Binance（截至 {datetime.fromtimestamp(BN_LAST, TZ):%m/%d %H:%M}）"
    else:
        fails.append("binance: no data in window")
    BS = lambda c: bn[c].dropna() if not bn.empty else pd.Series(dtype=float)

    plt.rcParams.update({"font.family": ["Noto Sans CJK TC", "DejaVu Sans"], "font.size": 10.5,
                         "text.color": TH["text"], "xtick.color": TH["muted"], "ytick.color": TH["muted"]})
    fs = a.font_scale
    # 2×2 — 槓桿堆疊 / 巨鯨vs散戶 / Bitfinex / Ahr999（移除全部帳戶、OKX大戶、BN/Gate大戶、獨立費率格）
    fig = plt.figure(figsize=(30 / fs, 42 / fs), dpi=a.dpi * fs, facecolor=TH["bg"])
    gs = fig.add_gridspec(2, 2, left=0.045, right=0.955, top=0.88, bottom=0.05, hspace=0.32, wspace=0.12,
                          height_ratios=[1.55, 1.0])
    axs = [fig.add_subplot(gs[i // 2, i % 2]) for i in range(4)]
    for ax in axs:
        style(ax)
    xlim = (num([start_b])[0], num([end_b])[0])
    last = lambda s: (float(s.iloc[-1]), datetime.fromtimestamp(int(s.index[-1]), TZ).strftime("%m/%d %H:%M")) if s is not None and not s.empty else (None, None)
    pctl = lambda s: float((s < s.iloc[-1]).mean() * 100)

    # 0 槓桿力道堆疊（多所合計）
    ax = axs[0]
    o, g = S("okx", "oi_usd"), S("gate", "oi_usd")
    vo, vg = fresh("未平倉_okx_usd", o, tf_max(a.tf)), fresh("未平倉_gate_usd", g, tf_max(a.tf))
    cur.update({"未平倉_okx_usd": vo, "未平倉_gate_usd": vg})
    try:
        draw_leverage_stack_cell(ax)
    except Exception as e:
        import traceback; traceback.print_exc()
        ax.text(0.5, 0.5, f"槓桿力道暫不可用\n{type(e).__name__}", transform=ax.transAxes,
                ha="center", va="center", color=TH["down"], fontsize=12)
        head(ax, "槓桿力道（暫不可用）", "見日誌", "降級", "—")

    # 1 巨鯨 vs 散戶
    ax = axs[1]
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
             "看法：大戶持倉比−散戶帳戶比。綠=大戶偏多；紫=Binance",
             "來源：OKX rubik top−account（柱）、Binance top−global（紫線）")
        legend(ax, [("OKX 差值（柱）", TH["up"]), (BN_LAB, BN_COL)]); add_price(ax, kl, w)
        end_tag(ax, wd, "{:+.2f}", TH["up"]); end_tag(ax, bwd, "{:+.2f}", BN_COL)
    except Exception as e:
        fails.append(f"whale delta: {e!r}"); head(ax, "巨鯨 vs 散戶差值", "繪製失敗", "", "")

    # 2 Bitfinex
    ax = axs[2]
    bl = series(con, "bitfinex", "long", "1H", start_b, end_b); bs = series(con, "bitfinex", "short", "1H", start_b, end_b)
    if a.tf != "1H" and not bl.empty:
        bl = bl.groupby(bucket_start(bl.index.to_numpy(), step)).last(); bs = bs.groupby(bucket_start(bs.index.to_numpy(), step)).last()
    bd = ((bl - bs) / (bl + bs) * 100).dropna()
    deltabars(ax, bd, w)
    if not bd.empty:
        lo_, hi_ = float(bd.min()), float(bd.max()); pad = max(0.05, (hi_ - lo_) * 0.15)
        ax.set_ylim(lo_ - pad, hi_ + pad)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _=None: f"{v:+.2f}%"))
    vl, vs = last(bl)[0], last(bs)[0]; vd = last(bd)[0]
    cur["多空差值_bitfinex_pct"], cur["bitfinex_多_btc"], cur["bitfinex_空_btc"] = vd, vl, vs
    if vd is None:
        fails.append("bitfinex: no data")
    head(ax, "Bitfinex 槓桿多空差值", f"{vd:+.2f}% · 多 {vl:,.0f} / 空 {vs:,.0f} BTC" if vd is not None else "資料不足",
         "看法：近全多。看變化：下降＝空增或多減",
         "來源：Bitfinex stats1 pos.size tBTCUSD long/short")
    add_price(ax, kl, w)
    end_tag(ax, bd, "{:+.2f}%", TH["up"])

    # 3 Ahr999
    ax = axs[3]
    try:
        ah = ahr999_series()
        if a.ahr_days > 0:
            ah = ah.iloc[-a.ahr_days:]
        x = mdates.date2num(ah.index.to_pydatetime())
        ax.set_yscale("log")
        ax.axhspan(1e-3, 0.45, color="#3a1a26", alpha=0.55, lw=0, zorder=1)
        ax.axhspan(0.45, 1.2, color="#131825", alpha=0.9, lw=0, zorder=1)
        ax.plot(x, ah.ahr, color=TH["c"], lw=1.0, zorder=4)
        ax.annotate(f"{float(ah.ahr.iloc[-1]):.2f}", xy=(x[-1], float(ah.ahr.iloc[-1])), xytext=(6,0), textcoords="offset points", fontsize=12, fontweight="bold", color=TH["c"], va="center", bbox=dict(boxstyle="round,pad=0.2", fc="#1a1d24", ec=TH["c"], lw=0.7, alpha=0.9), clip_on=False, zorder=15)
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
             "看法：<0.45抄底；0.45–1.2定投；>1.2偏貴（長線）", "")
        ax.text(0, -0.22, "計算：Ahr999 公式 · 對數座標", transform=ax.transAxes, fontsize=8.5, color=TH["muted"], va="top")
        _cglegend(ax, [("200日成本", TH["white"]), ("BTC 價格", TH["price"]), ("Ahr999", TH["c"]), ("抄底線 0.45", TH["red"]), ("定投線 1.2", TH["green"])], fs=9.5)
    except Exception as e:
        fails.append(f"ahr999: {e!r}"); head(ax, "Ahr999 指數（日線）", "計算失敗", "", "")

    # still fetch funding into JSON for compat (not drawn as own cell — in stack)
    try:
        fo = load_okx_pos(pos_con(), "funding_realized", "8h", start_b, end_b) * 100
        fg = S("gate", "funding") * 100
        vo, vg = fresh("資金費率_okx_8h_pct", fo, FUND_OKX_MAX), fresh("資金費率_gate_pct", fg, tf_max(a.tf))
        cur.update({"資金費率_okx_8h_pct": vo, "資金費率_gate_pct": vg})
    except Exception as e:
        fails.append(f"funding json: {e!r}")

    for i, ax in enumerate(axs):
        if i != 3:
            ax.set_xlim(*xlim); ax.yaxis.set_major_locator(MaxNLocator(5))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=TZ, minticks=6, maxticks=12))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=TZ))
    nowt = datetime.now(TZ)
    # 標題區完全在 gridspec top=0.88 之上，與單元 head() 分離
    fig.text(0.04, 0.978, "BTC 持倉數據", fontsize=24, fontweight="bold", color=TH["title"], va="top")
    fig.text(0.955, 0.978, f"{nowt:%Y/%m/%d %H:%M} UTC+8", fontsize=14, color=TH["muted"], ha="right", va="top")
    fig.text(0.04, 0.935, f"{a.tf} · {a.bars} 根（{a.bars*step/86400:g} 天）· BTC {kl.c.iloc[-1]/1000:.1f}K · 左上＝槓桿四層（價→OI→CVD→費率）· 多空僅留巨鯨/Bitfinex · 非 fapi",
             fontsize=12, color=TH["muted"], va="top")

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
    for k, v in STALE.items():
        fails.append(f"stale: {k} as of {v['asof']} (not shown as current)")
    cur["asof"], cur["stale"] = ASOF, STALE
    cur["btc"] = round(float(kl.c.iloc[-1]), 1); cur["fails"] = fails
    print("POSGRID_JSON " + json.dumps({k: (round(v, 6) if isinstance(v, float) else v) for k, v in cur.items()}, ensure_ascii=False))
    try:
        now_path = os.path.join(ROOT, "out", "posgrid_now.json")
        os.makedirs(os.path.dirname(now_path), exist_ok=True)
        with open(now_path, "w", encoding="utf-8") as f:
            json.dump({k: (round(v, 6) if isinstance(v, float) else v) for k, v in cur.items()}, f, ensure_ascii=False)
    except Exception as e:
        print("posgrid_now write fail", e, file=sys.stderr)


if __name__ == "__main__":
    main()

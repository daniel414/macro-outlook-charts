#!/usr/bin/env python3
"""Daily CheckOnChain cycle analysis for LINE /btccheckonchain.
Fetches chart data from charts-cdn.checkonchain.com (free Plotly HTML; the charts.checkonchain.com pages are iframe shells),
falls back to /home/box/shared/onchain/ cached HTML, rebuilds combined_daily.csv, onchain_summary.png, onchain_text.txt,
and publishes btc/onchain_latest.json + jpg to macro-outlook-charts (keeps last 60). Standalone: does NOT import publish_btc.py.
Usage: update_onchain.py [--dry-run] [--no-fetch]"""
import argparse, base64, glob, json, os, re, shutil, subprocess, sys, time
from datetime import datetime
import numpy as np, pandas as pd, requests
HERE = os.path.dirname(os.path.abspath(__file__)); RAW = os.path.join(HERE, "raw"); SHARED = "/home/box/shared/onchain"
CDN = "https://charts-cdn.checkonchain.com/btconchain/"
CHARTS = {"sthmvrv_bollingerbands": "unrealised/sthmvrv_bollingerbands/sthmvrv_bollingerbands", "lthsopr_indicator": "realised/lthsopr_indicator/lthsopr_indicator",
 "mvrv_lth_zscore": "unrealised/mvrv_lth_zscore/mvrv_lth_zscore", "aviv_lth_zscore": "unrealised/aviv_lth_zscore/aviv_lth_zscore",
 "mvrv_aviv_1": "unrealised/mvrv_aviv_1/mvrv_aviv_1", "nupl_bycohort": "unrealised/nupl_bycohort/nupl_bycohort",
 "nupl": "unrealised/nupl/nupl", "mvrv_sth": "unrealised/mvrv_sth/mvrv_sth"}
FALLBACK = {"sthmvrv_bollingerbands": "sth2.html"}
KEEP = 60
GHR = "daniel414/macro-outlook-charts"
try:
    m = re.search(r'GHR="([^"]+)"', open("/workspace/big-players/line_push.py").read()); GHR = m.group(1) if m else GHR
except Exception: pass
API = f"https://api.github.com/repos/{GHR}/contents/"
def H(): return {"Authorization": "Bearer " + os.environ["GH_CHARTS_TOKEN"], "Accept": "application/vnd.github+json"}

def arr(v):
    if isinstance(v, dict) and "bdata" in v: return np.frombuffer(base64.b64decode(v["bdata"]), dtype=np.dtype(v["dtype"])).tolist()
    return v
def parse(path):
    s = open(path, errors="ignore").read(); i = s.find("Plotly.newPlot")
    if i < 0: raise ValueError("no plotly")
    data, _ = json.JSONDecoder().raw_decode(s[s.find("[", i):]); cols = {}
    for t in data:
        x, y = arr(t.get("x")), arr(t.get("y"))
        if not (x and y and len(x) == len(y)): continue
        n = t.get("name") or "unnamed"; k = n; j = 2
        while k in cols: k = f"{n}_{j}"; j += 1
        se = pd.Series(y, index=pd.to_datetime(x)); cols[k] = se[~se.index.duplicated(keep="last")]
    return pd.DataFrame(cols).sort_index()

def fetch(no_fetch):
    os.makedirs(RAW, exist_ok=True); fresh = {}; dfs = {}
    for k, p in CHARTS.items():
        f = os.path.join(RAW, k + ".html"); ok = False
        if not no_fetch:
            try:
                r = requests.get(CDN + p + "_dark.html", timeout=90, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and "Plotly.newPlot" in r.text and len(r.content) > 100000:
                    open(f, "wb").write(r.content); ok = True
            except Exception as e: print("fetch", k, e, file=sys.stderr)
        if not ok:   # fallback: previous raw or shared cache
            for c in [f, os.path.join(SHARED, FALLBACK.get(k, "raw_" + k + ".html"))]:
                if os.path.exists(c) and os.path.getsize(c) > 100000: f = c; break
        dfs[k] = parse(f); fresh[k] = ok
        dfs[k].to_csv(os.path.join(HERE, k + ".csv"))
    return dfs, fresh

def combine(d):
    m, l, z, a, s, n, c, b = (d[k] for k in ["mvrv_aviv_1", "lthsopr_indicator", "mvrv_lth_zscore", "aviv_lth_zscore", "mvrv_sth", "nupl", "nupl_bycohort", "sthmvrv_bollingerbands"])
    nz = n[[x for x in ["Euphoria-Greed", "Belief-Denial", "Optimism", "Hope-Fear", "Capitulation"] if x in n]].bfill(axis=1).iloc[:, 0]
    D = pd.DataFrame({"P": l["Price"], "TMM": m["True Market Mean"], "AVIV": m["AVIV Ratio"], "LTHz": z["LTH-MVRV Z-Score"], "LTHAVIVz": a["LTH-AVIV Z-Score"],
        "LTHRP": l["LTH Realised Price"], "LTHSOPR": l["LTH-SOPR > 1"].fillna(l["LTH-SOPR < 1"]), "LTHMVRV": l["LTH-MVRV"],
        "STHMVRV": s["STH-MVRV"], "STHRP": s["STH Realised Price"], "NUPL": c["NUPL"], "NUPLn": nz, "STHNUPL": c["STH-NUPL"], "LTHNUPL": c["LTH-NUPL"],
        "BBosc": b["Oscillator"], "BBhot": b["Overheated"], "BBheat": b["Heated"], "BBcool": b["Cooled Off"], "BBover": b["Oversold"]})
    D = D.resample("D").last()  # daily traces are stamped 00:00 UTC; P = CheckOnChain daily Price trace
    D = D[D.index <= D["P"].last_valid_index()]
    D.attrs["live_price"] = float(m["Price"].dropna().iloc[-1]); D.attrs["live_ts"] = m["Price"].dropna().index[-1]
    D.to_csv(os.path.join(HERE, "combined_daily.csv")); return D

def k(v): return f"{v/1000:.1f}K"
def n0(v): return f"{v:,.0f}"
def md(t): return f"{t.month}/{t.day}"
def ymd(t): return f"{t.year}/{t.month}/{t.day}"
def last(D, c):
    s = D[c].dropna(); return float(s.iloc[-1])

def analyse(D):
    P = D["P"].dropna(); today = P.index[-1]; price = D.attrs.get("live_price", float(P.iloc[-1]))
    # Manual fixes (approved by Daniel 2026-10-15):
    # - CheckOnChain's daily Price on 2015-01-14 is 120 (exchange wick). Override with the Bitstamp BTC/USD daily close
    #   for 2015-01-14 (UTC) = 171.41, fetched 2026-10-15 from
    #   https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=3&start=1421107200  (bar timestamp 1421193600).
    #   Cross-check: CoinMetrics community API PriceUSD 2015-01-14 = 175.64.
    # - Cycle bottoms are pinned to the commonly used dates 2015-01-14, 2018-12-15, 2022-11-21 (same as image markers).
    P = P.copy()
    if pd.Timestamp("2015-01-14") in P.index and P[pd.Timestamp("2015-01-14")] < 150: P[pd.Timestamp("2015-01-14")] = 171.41
    hist = [("2013-11-01", "2013-12-31", "2015-01-14"), ("2017-11-01", "2018-01-31", "2018-12-15"), ("2021-10-01", "2021-12-31", "2022-11-21")]
    past = []
    for t0, t1, bd in hist:
        top = P.loc[t0:t1].idxmax(); bot = pd.Timestamp(bd); w = D.loc[bot - pd.Timedelta(days=45): bot + pd.Timedelta(days=45)]
        past.append(dict(top=top, bot=bot, days=(bot - top).days, dd=1 - P[bot] / P[top], nupl=w["NUPL"].min(), lthm=w["LTHMVRV"].min(), lthz=w["LTHz"].min()))
    top = P.loc["2024-01-01":].idxmax(); low = P.loc[top:].idxmin(); w = D.loc[low - pd.Timedelta(days=45): low + pd.Timedelta(days=45)]
    cur = dict(top=top, low=low, topP=float(P[top]), lowP=float(P[low]), days=(today - top).days, lowdays=(low - top).days, dd=1 - P[low] / P[top],
               nupl=float(D.loc[low, "NUPL"]) if not np.isnan(D.loc[low, "NUPL"]) else w["NUPL"].min(), lthm=w["LTHMVRV"].min(), lthz=w["LTHz"].min(), lthaz=w["LTHAVIVz"].min())
    # STH reclaim held 30 days after low
    above = (D["P"] > D["STHRP"]).loc[low:]; run = above.astype(int).groupby((~above).cumsum()).cumsum()
    hit = run[run >= 30]; sth_ok = hit.index[0] - pd.Timedelta(days=29) if len(hit) else None
    below_tmm = D.loc[low:].query("P < TMM"); tmm_since = (below_tmm.index[-1] + pd.Timedelta(days=1)) if len(below_tmm) else low
    tmm_ok = price > last(D, "TMM"); tmm_weeks = (today - tmm_since).days // 7 if tmm_ok else 0
    v = {c: last(D, c) for c in D.columns}
    sth_peak_win = D.loc[today - pd.Timedelta(days=60):, "STHMVRV"]; sopr_win = D.loc[today - pd.Timedelta(days=60):, "LTHSOPR"]
    av = D["AVIV"].dropna(); av_cross = av[(av > 0) & (av.shift() <= 0)].index; av_cross = av_cross[-1] if len(av_cross) else None
    return dict(today=today, price=price, live_ts=D.attrs.get("live_ts"), past=past, cur=cur, sth_ok=sth_ok, tmm_ok=tmm_ok, tmm_since=tmm_since, tmm_weeks=tmm_weeks, v=v,
                sth_peak=(sth_peak_win.idxmax(), sth_peak_win.max()), sopr_max=sopr_win.max(), av_cross=av_cross)

def months(m0, n): return f"{m0}–{(m0 + n - 1 - 1) % 12 + 1}月" if n > 1 else f"{m0}月"
SEP = "\n──────────"
def text(A):
    t, p, c, v, past = A["today"], A["price"], A["cur"], A["v"], A["past"]
    sup_lo, sup_hi = min(v["STHRP"], v["TMM"]), max(v["STHRP"], v["TMM"])
    # regime
    if p < v["BBcool"] and p < sup_lo: regime = "熊市延續、跌破短期成本後的弱勢期"
    elif c["lowdays"] < 120 and (t - c["low"]).days < 60: regime = "熊市後段的探底期"
    elif v["LTHz"] < 0 or v["AVIV"] < 0.1: regime = "熊市後段的修復期，最接近 2019 年第一季和 2023 年第一季"
    else: regime = "底部後的新上升段初期"
    L = [f"BTC 鏈上週期分析（CheckOnChain）", f"{datetime.now().strftime('%Y.%m.%d %H:%M')} (UTC+8)｜BTC {n0(p)}", "",
         f"從 CheckOnChain 數據看，目前週期位置：{regime}。", SEP, "【週期時間】",
         f"・前高是 {ymd(c['top'])} 的 {n0(c['topP'])}，到今天 {c['days']} 天。過去三輪的底都落在前高後 {min(x['days'] for x in past)}–{max(x['days'] for x in past)} 天：" +
         "、".join(f"{ymd(x['bot'])} 跌 {x['dd']*100:.0f}%" for x in past) + "。",
         f"・這輪低點是 {ymd(c['low'])} 的 {n0(c['lowP'])}，在前高後 {c['lowdays']} 天，跌幅 {c['dd']*100:.0f}%。", SEP, "【底部深度比較】",
         f"・低點附近 NUPL {c['nupl']:+.2f}，過去三輪底部為 " + "、".join(f"{x['nupl']:.2f}" for x in past) + "。",
         f"・LTH MVRV 最低 {c['lthm']:.2f}，過去三輪為 " + "、".join(f"{x['lthm']:.2f}" for x in past) + "。",
         f"・LTH MVRV Z 最低 {c['lthz']:.2f}，過去三輪為 " + "、".join(f"{x['lthz']:.2f}" for x in past) + "。"]
    shallow = c["lthm"] > max(x["lthm"] for x in past)
    L.append("・這輪的底比過去淺很多，低點還不能完全確認是週期底部。" if shallow else "・這輪底部深度已接近過去週期底部水準。")
    L += [SEP, "【確認底部條件】",
          "・價格站回短期持有者成本並守住 30 天：2019 年在 3/26、2023 年在 1/11 達成，" + (f"這輪 {md(A['sth_ok'])} 已經做到。" if A["sth_ok"] is not None else "這輪尚未達成。"),
          f"・站穩 True Market Mean（{n0(v['TMM'])}）：2019 年到 5 月、2023 年到 10 月才做到。" +
          (f"這輪從 {md(A['tmm_since'])} 起價格都在其上，約 {A['tmm_weeks']} 週，還要觀察。" if A["tmm_ok"] else "目前價格在其下方，尚未達成。")]
    m0 = t.month; q1 = m0 + 3 if m0 + 3 <= 12 else m0 + 3 - 12
    rng_lo, rng_hi = min(sup_lo, v["BBcool"] * 1.1), v["BBhot"]
    sp_d, sp_v = A["sth_peak"]
    L += [SEP, "【目前位置】",
          f"・STH MVRV {md(sp_d)} 到 {sp_v:.3f} 後{'回落' if v['STHMVRV'] < sp_v - 0.005 else '維持高檔'}，目前 {v['STHMVRV']:.3f}；LTH SOPR 近 60 天高點 {A['sopr_max']:.2f}、目前 {v['LTHSOPR']:.2f}。",
          f"・價格 {k(p)}，" + ("在" if p >= sup_hi else "低於" if p < sup_lo else "介於") + f" STH 成本 {k(v['STHRP'])} 與 True Market Mean {k(v['TMM'])} " + ("之上" if p >= sup_hi else "之下" if p < sup_lo else "之間") + f"；距 STH 布林帶偏熱線 {k(v['BBheat'])} {(p / v['BBheat'] - 1) * 100:+.1f}%、過熱線 {k(v['BBhot'])} {(p / v['BBhot'] - 1) * 100:+.1f}%。",
          f"・距 STH 布林帶冷卻線 {k(v['BBcool'])} {(p / v['BBcool'] - 1) * 100:+.1f}%、週期低點 {k(c['lowP'])} {(p / c['lowP'] - 1) * 100:+.1f}%、LTH 成本 {k(v['LTHRP'])} {(p / v['LTHRP'] - 1) * 100:+.1f}%。"]
    bb = "過熱" if v["BBosc"] > 1 else "超賣" if v["BBosc"] < -1 else ("中性偏上" if v["BBosc"] > 0 else "中性偏下")
    nz = "興奮／貪婪" if v["NUPL"] > 0.75 else "信念／否認" if v["NUPL"] > 0.5 else "樂觀／焦慮" if v["NUPL"] > 0.25 else "希望／恐懼" if v["NUPL"] > 0 else "投降"
    nz = "希望／恐懼" if 0 < v["NUPL"] <= 0.5 and v["NUPL"] < 0.5 else nz
    L += [SEP, f"【指標一覽（資料日 {md(t)}）】",
          f"・STH MVRV 布林帶｜{v['STHMVRV']:.3f}，振盪器 {v['BBosc']:+.2f}，{bb}。",
          f"・LTH SOPR｜{v['LTHSOPR']:.2f}，" + ("長期持有者仍有獲利但已接近 1。" if v["LTHSOPR"] < 1.1 and v["LTHSOPR"] >= 1 else "長期持有者獲利了結明顯。" if v["LTHSOPR"] >= 1.1 else "長期持有者整體虧損賣出。"),
          f"・MVRV LTH Z｜{v['LTHz']:.2f}，" + ("低於長期均值" if v["LTHz"] < 0 else "高於長期均值") + f"，週期低點 {c['lthz']:.2f}。",
          f"・AVIV LTH Z｜{v['LTHAVIVz']:.2f}，週期低點 {c['lthaz']:.2f}。",
          f"・AVIV｜{v['AVIV']:+.3f}，" + ((f"{md(A['av_cross'])} 由負轉正。") if A["av_cross"] is not None and (t - A["av_cross"]).days < 120 and v["AVIV"] > 0 else ("為正值。" if v["AVIV"] > 0 else "為負值，屬低估區。")),
          f"・NUPL｜{v['NUPL']:.2f}，屬於{nz}區。",
          f"・NUPL 分族群｜短期持有者 {v['STHNUPL']:+.2f}，長期持有者 {v['LTHNUPL']:+.2f}。",
          f"・STH MVRV｜{v['STHMVRV']:.2f}，短期持有者平均{'獲利' if v['STHMVRV'] >= 1 else '虧損'}約 {abs(v['STHMVRV']-1)*100:.0f}%。",
          SEP, "【關鍵價位】",
          f"・STH 布林帶過熱線 {n0(v['BBhot'])}｜偏熱線 {n0(v['BBheat'])}",
          f"・True Market Mean {n0(v['TMM'])}｜STH 成本 {n0(v['STHRP'])}",
          f"・STH 布林帶冷卻線 {n0(v['BBcool'])}｜超賣線 {n0(v['BBover'])}",
          f"・週期低點 {n0(c['lowP'])}｜LTH 成本 {n0(v['LTHRP'])}", SEP, "【結論】"]
    pos = "在 STH 成本與 True Market Mean 之上" if p > sup_hi else "介於 STH 成本與 True Market Mean 之間" if p >= sup_lo else ("低於 STH 成本、高於冷卻線" if p > v["BBcool"] else "低於 STH 成本與冷卻線")
    L.append(f"・週期階段：{regime}。價格{pos}，" + ("這輪尚未" if A["sth_ok"] is None else "這輪已") + "達成「站回短期持有者成本並守住 30 天」，" + ("目前在 True Market Mean 之上已約 " + str(A["tmm_weeks"]) + " 週。" if A["tmm_ok"] else "目前在 True Market Mean 下方。"))
    return "\n".join(L).rstrip("\n")

def coc_fig(path):
    """Read a CheckOnChain Plotly page: traces (with their own colors/dash/fill/yaxis) and layout."""
    s = open(path, errors="ignore").read(); i = s.find("Plotly.newPlot"); dec = json.JSONDecoder()
    j = s.find("[", i); data, e = dec.raw_decode(s[j:]); r = s[j + e:]; lay, _ = dec.raw_decode(r[r.find("{"):])
    return data, lay
def _rgba(c, default="#DAD7D2"):
    if not c: return default, 1.0
    m_ = re.match(r"rgba?\(([^)]*)\)", c.replace(" ", ""))
    if m_:
        p_ = [float(x) for x in m_.group(1).split(",")]
        return "#%02x%02x%02x" % tuple(int(x) for x in p_[:3]), (p_[3] if len(p_) > 3 else 1.0)
    return c, 1.0

def chart(D, A, out, dfs=None):
    """One panel per CheckOnChain chart, mirroring each *_dark.html: price + overlays on a log left axis (yaxis 'y'),
    indicator traces on the overlaid right axis (yaxis 'y2') with CheckOnChain's own axis ranges, trace colors, dashes,
    fills, and x-axis start date. Dark template colors: paper #06040b, plot rgb(45,46,47), grid #283442, text #DAD7D2."""
    import matplotlib, matplotlib.ticker as tk; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.dates as mdt
    from matplotlib import font_manager as fm
    cjk = next((f for f in ["Noto Sans CJK TC", "Noto Sans CJK JP", "WenQuanYi Zen Hei"] if any(f in x.name for x in fm.fontManager.ttflist)), "sans-serif")
    TX, PAPER, PLOT, GRID, LINE = "#DAD7D2", "#06040b", "#2d2e2f", "#283442", "#506784"
    plt.rcParams.update({"font.family": [cjk], "font.weight": "bold", "font.size": 17, "text.color": TX, "axes.labelcolor": TX, "xtick.color": TX, "ytick.color": TX,
        "axes.edgecolor": LINE, "axes.titlecolor": TX, "legend.labelcolor": TX, "figure.facecolor": PAPER, "axes.facecolor": PLOT, "savefig.facecolor": PAPER,
        "grid.color": GRID, "grid.linewidth": 1.2, "xtick.labelsize": 15, "ytick.labelsize": 15, "axes.unicode_minus": False, "axes.titlesize": 21, "axes.titleweight": "bold"})
    v = A["v"]
    order = [("sthmvrv_bollingerbands", f"STH MVRV 布林帶｜振盪器 {v['BBosc']:+.2f}"), ("lthsopr_indicator", f"LTH SOPR｜{v['LTHSOPR']:.2f}"),
             ("mvrv_lth_zscore", f"MVRV LTH Z-Score｜{v['LTHz']:+.2f}"), ("aviv_lth_zscore", f"AVIV LTH Z-Score｜{v['LTHAVIVz']:+.2f}"),
             ("mvrv_aviv_1", f"MVRV vs AVIV｜AVIV {v['AVIV']:+.3f}"), ("nupl", f"NUPL｜{v['NUPL']:.2f}"),
             ("nupl_bycohort", f"NUPL 分族群｜STH {v['STHNUPL']:+.2f}／LTH {v['LTHNUPL']:+.2f}"), ("mvrv_sth", f"STH MVRV｜{v['STHMVRV']:.3f}")]
    DASH = {"dash": (0, (6, 4)), "dot": (0, (1.5, 3)), "dashdot": (0, (6, 3, 1.5, 3)), "longdash": (0, (10, 4))}
    fig, axs = plt.subplots(4, 2, figsize=(20, 30), dpi=150); axs = axs.ravel(); starts = {}
    t_end = A["today"] + pd.Timedelta(days=60)
    for ax, (key, title) in zip(axs, order):
        data, lay = coc_fig(os.path.join(RAW, key + ".html"))
        x0 = pd.Timestamp(str((lay.get("xaxis") or {}).get("range", ["2015-01-01"])[0])[:10]); starts[key] = str(x0.date())
        ax2 = ax.twinx(); ax2.set_facecolor("none"); ax.set_zorder(ax2.get_zorder() - 1) if False else None
        for t in data:
            yk = t.get("yaxis", "y")
            if yk not in ("y", "y2"): continue
            x_, y_ = arr(t.get("x")), arr(t.get("y"))
            if not x_ or not y_ or len(x_) != len(y_): continue
            se = pd.Series(y_, index=pd.to_datetime(x_)).astype(float); se = se[~se.index.duplicated(keep="last")]; se = se[se.index >= x0 - pd.Timedelta(days=5)]  # keep NaN gaps (CheckOnChain segment colouring)
            if se.dropna().empty: continue
            ln = t.get("line") or {}; col, al = _rgba(ln.get("color")); w = float(ln.get("width", 2) or 2)
            nm = t.get("name") or ""; tgt = ax if yk == "y" else ax2
            show = nm and not re.fullmatch(r"[0-9.\-+]+(sd)?", nm)
            if yk == "y" and nm == "Price": col, w = "#ffffff", max(w, 1.4)
            if t.get("fill") in ("tozeroy", "tonexty"):
                fc, fa = _rgba(t.get("fillcolor") or ln.get("color"))
                tgt.fill_between(se.index, 0 if yk == "y2" else 1e-9, se.values, color=fc, alpha=min(fa, .6), lw=0, label=nm if show else None)
                if w > 0 and t.get("fill") == "tozeroy" and yk == "y2": tgt.plot(se.index, se.values, c=col, alpha=al, lw=max(w * .9, .8))
            else:
                tgt.plot(se.index, se.values, c=col, alpha=max(al, .45), lw=max(w * 1.1, .9), ls=DASH.get(ln.get("dash"), "-"), label=nm if show else None)
        for k_, axx in (("yaxis", ax), ("yaxis2", ax2)):
            y = lay.get(k_) or {}; rng = y.get("range")
            if y.get("type") == "log":
                axx.set_yscale("log")
                if rng: axx.set_ylim(10 ** rng[0], 10 ** rng[1])
            elif rng: axx.set_ylim(*rng)
        ax.yaxis.set_major_formatter(tk.FuncFormatter(lambda y, _: (f"{y/1000:.0f}K" if y >= 1000 else f"{y:g}")))
        ax.yaxis.set_minor_formatter(tk.NullFormatter())
        if (lay.get("yaxis2") or {}).get("type") == "log": ax2.yaxis.set_major_formatter(tk.FuncFormatter(lambda y, _: f"{y:g}")); ax2.yaxis.set_minor_formatter(tk.NullFormatter())
        ax.set_xlim(x0, t_end); ax.grid(True)
        span = (t_end - x0).days / 365
        ax.xaxis.set_major_locator(mdt.YearLocator(2 if span > 9 else 1)); ax.xaxis.set_major_formatter(mdt.DateFormatter("%Y"))
        h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=12, ncol=3, frameon=False)
        ax.set_title(f"{title}\n自 {x0:%Y.%m.%d}", fontsize=20); ax2.set_ylabel(((lay.get("yaxis2") or {}).get("title") or {}).get("text", ""), fontsize=14)
        for bd in ["2018-12-15", "2022-11-21"]: ax.axvline(pd.Timestamp(bd), c=TX, ls=":", lw=1.2, alpha=.4)
    fig.suptitle(f"BTC 鏈上週期（CheckOnChain）{A['today']:%Y.%m.%d}｜BTC {A['price']:,.0f}", fontsize=30, fontweight="bold", color=TX, y=.995)
    fig.text(.99, .003, "資料：checkonchain.com（各圖時間軸與原圖相同）", ha="right", fontsize=15, color=TX, alpha=.7)
    fig.subplots_adjust(left=.05, right=.95, top=.955, bottom=.02, hspace=.3, wspace=.28); plt.savefig(out, facecolor=PAPER); plt.close()
    print("panel starts:", starts, file=sys.stderr)

def gh_put(path, data, msg):
    g = requests.get(API + path, headers=H(), timeout=60)
    body = {"message": msg, "content": base64.b64encode(data).decode(), "branch": "main"}
    if g.status_code == 200: body["sha"] = g.json()["sha"]
    elif g.status_code != 404: raise Exception(f"gh get {g.status_code}")
    for _ in range(3):
        r = requests.put(API + path, headers=H(), json=body, timeout=120)
        if r.status_code in (200, 201): return r.json()["commit"]["sha"]
        time.sleep(3)
    raise Exception(f"gh put {r.status_code}")
def pinned(sha, path):
    # Private-repo safe: verify via Contents API with GH_CHARTS_TOKEN, still write the raw sha URL (Worker rewrites it to a signed /img/ URL).
    raw = f"https://raw.githubusercontent.com/{GHR}/{sha}/{path}"
    if not os.environ.get("GH_CHARTS_TOKEN"):
        print("WARN pinned: GH_CHARTS_TOKEN not set, cannot verify " + path + ", using raw URL anyway", file=sys.stderr); return raw
    for _ in range(3):
        try:
            r = requests.get(API + path, params={"ref": sha}, headers=H(), timeout=60)
            if r.status_code == 200 and r.json().get("type") == "file" and r.json().get("size", 0) > 0: return raw
            print("pinned check", path, r.status_code, file=sys.stderr)
        except Exception as e: print("pinned check err", path, e, file=sys.stderr)
        time.sleep(3)
    print("WARN pinned: could not verify " + path + ", using raw URL anyway", file=sys.stderr)
    return raw
def prune(pre="btc_onchain_"):
    r = requests.get(API + "btc", headers=H(), timeout=60)
    if r.status_code != 200: return
    imgs = [f for f in r.json() if f["name"].startswith(pre) and f["name"].endswith(".jpg")]
    stamps = sorted({re.sub(r"(_preview)?\.jpg$", "", f["name"]) for f in imgs}); old = set(stamps[:-KEEP])
    for f in imgs:
        if re.sub(r"(_preview)?\.jpg$", "", f["name"]) in old:
            requests.delete(API + f["path"], headers=H(), json={"message": "prune " + f["name"], "sha": f["sha"], "branch": "main"}, timeout=60)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--no-fetch", action="store_true"); a = ap.parse_args()
    dfs, fresh = fetch(a.no_fetch); D = combine(dfs); A = analyse(D); T = text(A); CCJ = None
    try:  # @0xcryptochan signals (free data rebuild); never blocks the main flow
        sys.path.insert(0, HERE); import cryptochan_signals as ccs; CCJ = ccs.run()
        T = T.replace(SEP + "\n【結論】", SEP + "\n" + "\n".join(CCJ["section"]) + SEP + "\n【結論】")
    except Exception as e: print("WARN cryptochan:", e, file=sys.stderr)
    open(os.path.join(HERE, "onchain_text.txt"), "w").write(T)
    png = os.path.join(HERE, "onchain_summary.png"); chart(D, A, png, dfs)
    print(T); print("fresh:", fresh, "data date:", A["today"].date(), file=sys.stderr)
    if a.dry_run: return
    from PIL import Image
    stamp = datetime.now().strftime("%Y%m%d_%H%M"); od = os.path.join(HERE, "publish"); os.makedirs(od, exist_ok=True)
    im = Image.open(png).convert("RGB"); jb = os.path.join(od, f"btc_onchain_{stamp}.jpg"); im.save(jb, "JPEG", quality=95, optimize=True, subsampling=0)
    q = im.copy(); q.thumbnail((1024, 1024)); jp = jb[:-4] + "_preview.jpg"; q.save(jp, "JPEG", quality=85, optimize=True)
    assert os.path.getsize(jb) <= 10_000_000 and os.path.getsize(jp) <= 1_000_000, "LINE image size limit"
    m1, m2 = "btc/" + os.path.basename(jb), "btc/" + os.path.basename(jp)
    j = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "data_date": str(A["today"].date()), "fresh": all(fresh.values()), "text": T,
         "image_url": pinned(gh_put(m1, open(jb, "rb").read(), "btc onchain " + stamp), m1),
         "preview_url": pinned(gh_put(m2, open(jp, "rb").read(), "btc onchain preview " + stamp), m2)}
    if CCJ: j["cryptochan"] = {"current": CCJ["current"], "live": CCJ["live"], "backtest": CCJ["backtest"]}
    gh_put("btc/onchain_latest.json", json.dumps(j, ensure_ascii=False, indent=1).encode(), "btc onchain latest " + stamp)
    prune(); print("published", j["image_url"], file=sys.stderr)
if __name__ == "__main__": main()

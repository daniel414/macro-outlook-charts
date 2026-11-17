#!/usr/bin/env python3
"""/btccheckonchain 兩排自有圖（2026-11-17 Daniel 同意上線）：正式由 update_onchain.py 呼叫 produce()；--draft 產樣稿。
資料只讀自家網站鏈上頁的公開資料檔（線上優先，失敗或較舊才讀本機副本）。共用 onchain_selfchart.py 的讀檔、分析與自檢。
用法：onchain_six.py --draft [--date 20261117]"""
import argparse, json, os, re, sys
from datetime import datetime
import numpy as np, pandas as pd, requests
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import onchain_selfchart as S0
from onchain_selfchart import TZ, ROOT, n0, md, ymd, SEP

S0.SERIES.update({"RISK": "bv-risk-metric", "PSIP": "bv-supply-in-profit-pct", "STHPSIP": "bv-sth-psip", "MVRVZ": "bv-mvrv-z",
                  "AVm2": "bv-aviv-sd-m2", "AVm15": "bv-aviv-sd-m15", "AVm1": "bv-aviv-sd-m1", "AVp1": "bv-aviv-sd-p1",
                  "AVp15": "bv-aviv-sd-p15", "AVp2": "bv-aviv-sd-p2",
                  "MCAP": "bv-market-cap", "RCAP": "bv-realized-cap"})
HODL_LTH = ["6m-9m", "9m-1y", "1y-18m", "18m-2y", "2y-3y", "3y-4y", "4y-5y", "5y-6y", "6y-7y", "7y-8y", "8y-10y", "10y-plus"]
HODL_COL = {"6m-9m": "#c0ca33", "9m-1y": "#d4e157", "1y-18m": "#9ccc65", "18m-2y": "#7cb342", "2y-3y": "#9ccc65", "3y-4y": "#66bb6a",
            "4y-5y": "#26a69a", "5y-6y": "#26c6da", "6y-7y": "#29b6f6", "7y-8y": "#42a5f5", "8y-10y": "#5c6bc0", "10y-plus": "#7c4dff"}
HODL_LAB = {"6m-9m": "6–9 個月", "9m-1y": "9–12 個月", "1y-18m": "1–1.5 年", "18m-2y": "1.5–2 年", "2y-3y": "2–3 年", "3y-4y": "3–4 年",
            "4y-5y": "4–5 年", "5y-6y": "5–6 年", "6y-7y": "6–7 年", "7y-8y": "7–8 年", "8y-10y": "8–10 年", "10y-plus": "10 年以上"}
RISK_Z = [(0.2, "#1E3A8A", "低風險"), (0.4, "#0891B2", "偏低"), (0.6, "#16A34A", "中性"), (0.8, "#EA580C", "偏高"), (1.01, "#DC2626", "高風險")]
AV_Z = [("AVm2", "#2dd4bf", "極度便宜"), ("AVm15", "#26a69a", "很便宜"), ("AVm1", "#3b82f6", "便宜"), ("AVp1", "#94a3b8", "中性"),
        ("AVp15", "#f5a623", "偏貴"), ("AVp2", "#f472b6", "很貴"), (None, "#e57373", "過熱")]

def load_hodl():
    d = None
    for src in ("site", "local"):
        try:
            d = requests.get(S0.SITE_URL + "bv-hodl-waves.json", timeout=40).json() if src == "site" else json.load(open(S0.SITE_LOCAL + "bv-hodl-waves.json"))
            break
        except Exception as e: print("hodl", src, e, file=sys.stderr)
    idx = pd.to_datetime(d["t"])
    return pd.DataFrame({k: d["bands"][k] for k in d["order"]}, index=idx).astype(float)

def av_zone(v, row):
    for k, c, lab in AV_Z:
        if k is None or v < row[k]: return c, lab
def risk_zone(v):
    for m, c, lab in RISK_Z:
        if v < m: return c, lab

def text(A, price, M, now, D):
    t, c, v, past = A["today"], A["cur"], A["v"], A["past"]; p = price
    sup_lo = min(v["STHRP"], v["TMM"])
    if p < v["BBcool"] and p < sup_lo: regime = "熊市延續的弱勢期"
    elif v["AVIV"] < 1: regime = "熊市後段的修復期"
    else: regime = "底部之後的新上升段初期"
    L = ["BTC 鏈上週期", f"{now:%Y.%m.%d} 台北時間 {now:%H:%M}｜BTC {n0(p)}", "", f"目前週期位置：{regime}。", SEP, "【週期時間】",
         f"・前高在 {ymd(c['top'])} 的 {n0(c['topP'])}，到今天 {c['days']} 天。過去三輪的底都落在前高後 {min(x['days'] for x in past)}～{max(x['days'] for x in past)} 天，"
         + "、".join(f"{x['bot'].year} 年那次跌了 {x['dd']*100:.0f}%" for x in past) + "。",
         f"・這輪目前的低點是 {ymd(c['low'])} 的 {n0(c['lowP'])}，離前高 {c['lowdays']} 天，跌了 {c['dd']*100:.0f}%。", SEP, "【底部深度比較】",
         f"・低點附近 NUPL 最低 {c['nupl']:.2f}，過去三輪底部是 " + "、".join(f"{x['nupl']:.2f}" for x in past) + "。",
         f"・LTH MVRV 最低 {c['lthm']:.2f}，過去三輪是 " + "、".join(f"{x['lthm']:.2f}" for x in past) + "。",
         ]
    shallow = c["lthm"] > max(x["lthm"] for x in past)
    L.append((f"・這輪的底比過去淺很多，長期持有者還沒被洗到虧損，LTH SOPR 30 日均值 {v['LTHSOPR']:.2f}，這個低點還不能當成週期大底。" if v["LTHSOPR"] >= 1 else f"・這輪的底比過去淺很多，但 LTH SOPR 30 日均值 {v['LTHSOPR']:.2f}，長期持有者已經開始賠錢賣。") if shallow else "・這輪的深度已經接近過去的週期大底。")
    def ym(x): return f"{x.year} 年 {x.month} 月" if x is not None else "未達成"
    L += [SEP, "【確認底部條件】",
          f"・站回短期持有者成本並守住 30 天：前兩輪在 {ym(past[1]['sth_ok'])}、{ym(past[2]['sth_ok'])}做到，"
          + (f"這輪 {md(A['sth_ok'])} 做到過，但沒守住。" if A["sth_ok"] is not None and p < v["STHRP"] else f"這輪 {md(A['sth_ok'])} 已經做到。" if A["sth_ok"] is not None else "這輪還沒做到。"),
          f"・站穩真實市場均價：前兩輪到 {ym(past[1]['tmm_ok'])}、{ym(past[2]['tmm_ok'])}才做到，" + ("這輪目前站在上面。" if p > v["TMM"] else "這輪現在還在它下面。")]
    # 成本帶（圖 1）
    lv = [("真實市場均價", v["TMM"]), ("短期持有者成本", v["STHRP"]), ("已實現價格", v["RP"]), ("長期持有者成本", v["LTHRP"])]
    above = [n for n, x in lv if x > p]; below = [n for n, x in lv if x < p]
    sup = sorted([("這輪低點", c["lowP"])] + [(n, x) for n, x in lv if x < p], key=lambda z: -z[1]) if c["lowP"] < p else [(n, x) for n, x in lv if x < p]
    pos = "・價格壓在" + "、".join(above[::-1]) + "下面" if above else "・價格站在四條成本線上面"
    pos += ("；下面依序是" + "、".join(n for n, _ in sup) + "。") if sup else "。"
    L += [SEP, "【成本帶與關鍵價位】", pos,
          "・上方壓力：" + "｜".join(f"{n} {n0(x)}" for n, x in lv if x > p),
          "・下方支撐：" + "｜".join(f"{n} {n0(x)}" for n, x in lv if x < p)]
    # 指標一覽
    rz = risk_zone(v["RISK"])[1]; avrow = {k: v[k] for k in ("AVm2", "AVm15", "AVm1", "AVp1", "AVp15", "AVp2")}; avz = av_zone(v["AVIV"], avrow)[1]
    psip_d = D["PSIP"].dropna().index[-1]
    sm = v["STHMVRV"]
    smpos = "低於長期平均下方兩個標準差" if sm < v["SMm2"] else "低於長期平均下方一個標準差" if sm < v["SMm1"] else "低於長期平均" if sm < v["SMmean"] else "高於長期平均"
    L += [SEP, "【指標一覽】", f"・資料日：{md(t)}",
          f"・短期持有者偏離度｜振盪值 {v['BBz']:.2f}，" + ("已經掉進超賣區，短線賣壓偏重，但超賣可以維持一段時間，不等於馬上見底" if v["BBz"] <= -1 else "還沒到超賣區，還沒出現典型的投降訊號" if v["BBz"] < 1 else "在過熱區")
          + f"；STH MVRV {sm:.2f}，{smpos}。",
          f"・AVIV 溫度｜{v['AVIV']:.2f}，落在{avz}區" + ("，還沒進便宜區。" if avz == "中性" and v["AVIV"] < 1 else "。"),
          f"・風險指標｜{v['RISK']:.2f}，屬於{rz}區" + ("，跨週期看估值不貴，但低檔可以待很久。" if v["RISK"] < 0.4 else "。"),
          f"・MVRV Z｜{v['MVRVZ']:.2f}，" + ("已經跌破 0，過去三輪熊市底部也都跌到 0 下面。" if v["MVRVZ"] < 0 else "還在 0 上面，過去三輪熊市底部都跌到 0 下面。"),
          f"・獲利供給占比｜{md(psip_d)} 是 {v['PSIP']:.1f}%，" + ("已經跌破五成，過去這是週期底部區常見的訊號。" if v["PSIP"] < 50 else "還在五成上面，全市場還沒痛到過去熊市底部的程度。")
          + f"短期持有者只剩 {v['STHPSIP']:.1f}% 還在賺。",
          f"・短期持有者未實現損益｜STH-NUPL {v['STHNUPL']:.2f}，" + ("已經跌破 -0.2，短線恐慌出清的深度到了。" if v["STHNUPL"] < -0.2 else "還在 -0.2 上面，短線還沒到恐慌出清的深度。" if v["STHNUPL"] < 0 else "在 0 上面，短期買盤整體有賺。"),
          ]
    Sg = ["長短期成本比還在 MA60 下面，短期買盤成本相對墊高，算偏多，站回 MA60 上面就失效。" if v["RATIO"] < v["RATIO60"] else
          "長短期成本比站回 MA60 上面，這個偏多訊號先失效。"]
    L += [SEP, "【訊號】"] + ["・" + s for s in Sg]
    J = []; k = []
    if M.get("bear_year"): k.append("今年大多數日線都收在年開盤價下面，這是熊市年")
    if M.get("ma200_w") and p < M["ma200_w"]: k.append(f"價格已經掉到週線 MA200 的 {n0(M['ma200_w'])} 下面")
    if M.get("ma120_d") and p < M["ma120_d"]: k.append(f"日線也還壓在 MA120 的 {n0(M['ma120_d'])} 下面")
    if k: J.append("・K 線位置：" + "，".join(k) + "。")
    mm = []
    if M.get("dxy"): mm.append(f"美元 {M['dxy_md']} 收在 {M['dxy']:.2f}" + ("，還在創高" if M.get("dxy_high") else ""))
    if M.get("y10") and M.get("y2"): mm.append(f"10 年期 {M['y10']:.2f}%、2 年期 {M['y2']:.2f}%" + ("，2 年期還高過政策利率，市場在押再升息，資金面沒有放鬆" if M.get("hike_priced") else ""))
    if mm: J.append("・總經：" + "；".join(mm) + "。殖利率還沒見頂回落，風險資產通常要等殖利率轉頭後才真正落底。")
    J.append("・鏈上：" + ("這輪還沒走完過去幾輪那種徹底的重置，底部要靠時間和籌碼換手慢慢磨出來。" if shallow else "鏈上已經接近過去幾輪的重置深度。"))
    w = M.get("window"); mt = M.get("meeting")
    J.append("・時間上或許已經接近尾聲，價格卻不一定到底" + (f"，弱勢窗口還開到 {w}" if w else "") + (f"，{mt}的利率會議是下一個關口" if mt else "") + "。")
    J.append("・怎麼做：不追多，長線只用現貨分批，不一次做滿；反彈先看成更低的高點，不急著當成底部。")
    J.append("・什麼情況代表看錯：週線收盤重新站回短期持有者成本並守住，偏空這套就要重估；週線或月線收盤跌破這輪低點，就改看已實現價格和長期持有者成本那一帶。")
    L += [SEP, "【週期判斷】"] + J
    return "\n".join(L) + "\n", dict(risk_zone=rz, aviv_zone=avz)

def charts(D, A, H, outdir, now, price):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt, matplotlib.dates as mdates, matplotlib.ticker as tk
    from matplotlib import font_manager as fm
    from matplotlib.collections import LineCollection
    cjk = next((f for f in ["Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Serif CJK TC"] if any(f == x.name for x in fm.fontManager.ttflist)), "sans-serif")
    BG, GRID, TX = "#000000", "#1a1a1a", "#e5e5e5"
    plt.rcParams.update({"font.family": [cjk], "font.size": 13, "text.color": TX, "axes.labelcolor": TX, "xtick.color": "#a3a3a3", "ytick.color": "#a3a3a3",
                         "axes.edgecolor": "#262626", "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG, "axes.grid": True,
                         "grid.color": GRID, "grid.linewidth": .8, "legend.frameon": False, "axes.unicode_minus": False})
    dd = A["today"]
    def S(c, st): return D[c].dropna().loc[st:]
    def xfmt(a, st):
        a.set_xlim(pd.Timestamp(st), dd + pd.Timedelta(days=60)); a.xaxis.set_major_locator(mdates.YearLocator()); a.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    def colored(a, s, cols, lw=1.6):
        x = mdates.date2num(s.index.to_pydatetime()); y = s.values
        seg = np.stack([np.column_stack([x[:-1], y[:-1]]), np.column_stack([x[1:], y[1:]])], axis=1)
        a.add_collection(LineCollection(seg, colors=cols[:-1], linewidths=lw)); a.autoscale_view()
    usd = tk.FuncFormatter(lambda x, _: f"${x:,.0f}")
    def ttl(a, t, small): a.set_title(t, loc="left", fontsize=17 if small else 20, fontweight="bold", pad=8)

    def d_cost(f, sp, sm):
        st = "2018-01-01"; a = f.add_subplot(sp); ttl(a, "成本帶總覽", sm)
        a.plot(S("P", st), color="#ffffff", lw=1.4, label="價格")
        for col, cc, lab in [("STHRP", "#f472b6", "短期持有者成本"), ("TMM", "#8264ff", "真實市場均價"), ("RP", "#f5a623", "已實現價格"), ("LTHRP", "#2dd4bf", "長期持有者成本")]:
            a.plot(S(col, st), color=cc, lw=1.7, label=lab)
        a.set_yscale("log"); a.yaxis.set_major_formatter(usd); a.yaxis.set_minor_formatter(tk.NullFormatter())
        lo = pd.Timestamp(A["cur"]["low"]); a.axvline(lo, color="#64748b", ls=":", lw=1)
        a.annotate("這輪低點", (lo, A["cur"]["lowP"]), xytext=(-70, -24), textcoords="offset points", color="#a3a3a3", fontsize=12)
        a.legend(loc="upper left", ncol=5, fontsize=12); xfmt(a, st)
    def d_price_risk(f, sp, sm):
        st = "2015-01-01"; a = f.add_subplot(sp); ttl(a, "BTC 價格｜依風險指標上色", sm)
        p = S("P", st); r = D["RISK"].reindex(p.index).ffill().bfill()
        colored(a, p, [risk_zone(x)[0] for x in r.values], lw=1.8)
        a.set_yscale("log"); a.set_ylim(p.min() * .8, p.max() * 1.3); a.yaxis.set_major_formatter(usd); a.yaxis.set_minor_formatter(tk.NullFormatter())
        [a.plot([], [], color=cc, lw=3, label=lab) for _, cc, lab in RISK_Z]; a.legend(loc="upper left", ncol=5, fontsize=12); xfmt(a, st)
    def d_sth(f, sp, sm):
        st = "2018-01-01"; g = sp.subgridspec(2, 1, hspace=.06); a1 = f.add_subplot(g[0]); a2 = f.add_subplot(g[1], sharex=a1); ttl(a1, "短期持有者偏離度", sm)
        a1.plot(S("STHMVRV", st), color="#f44870", lw=1.3, label="STH MVRV")
        for col, cc, lab in [("SMp2", "#e57373", "+2σ"), ("SMp1", "#f5a623", "+1σ"), ("SMmean", "#94a3b8", "均值"), ("SMm1", "#3b82f6", "−1σ"), ("SMm2", "#26a69a", "−2σ")]:
            a1.plot(S(col, st), color=cc, lw=1, ls="--", alpha=.8, label=lab)
        a1.axhline(1, color="#e2e8f0", ls="--", lw=1, alpha=.85); a1.legend(loc="upper left", ncol=6, fontsize=11); plt.setp(a1.get_xticklabels(), visible=False)
        z = S("BBz", st); a2.plot(z, color="#c47a3a", lw=1.1, label="布林帶振盪值")
        a2.fill_between(z.index, 0, z.values, color=(180/255, 100/255, 40/255, .35), lw=0)
        a2.fill_between(z.index, 1, z.values, where=z.values > 1, color="#f44870", alpha=.55, lw=0)
        a2.fill_between(z.index, -1, z.values, where=z.values < -1, color="#30c070", alpha=.55, lw=0)
        for y, cc, ls in [(1, "#f44870", "-"), (-1, "#30c070", "-"), (0, "#e2e8f0", "--")]: a2.axhline(y, color=cc, ls=ls, lw=1, alpha=.8)
        a2.legend(loc="upper left", fontsize=11); xfmt(a2, st)
    def d_risk(f, sp, sm):
        st = "2015-01-01"; a = f.add_subplot(sp); ttl(a, "風險指標", sm)
        r = S("RISK", st); colored(a, r, [risk_zone(x)[0] for x in r.rolling(2, min_periods=1).mean().values], lw=2)
        for y, cc in [(.2, "#1E3A8A"), (.5, "#16A34A"), (.8, "#EA580C")]: a.axhline(y, color=cc, ls="--", lw=1, alpha=.7)
        a.set_ylim(0, 1); [a.plot([], [], color=cc, lw=3, label=lab) for _, cc, lab in RISK_Z]; a.legend(loc="upper left", ncol=5, fontsize=11); xfmt(a, st)
    def d_psip(f, sp, sm):
        st = "2015-01-01"; a = f.add_subplot(sp); ttl(a, "獲利供給占比", sm)
        ps = S("PSIP", st); a.fill_between(ps.index, 0, 100, where=ps.values < 50, color=(38/255, 198/255, 218/255, .30), lw=0, label="低於五成")
        a.plot(ps, color="#26c6da", lw=1.5, label="全市場"); a.axhline(50, color="#e2e8f0", ls="--", lw=1, alpha=.85); a.set_ylim(0, 100)
        a.yaxis.set_major_formatter(tk.FuncFormatter(lambda x, _: f"{x:.0f}%")); a.legend(loc="lower left", ncol=2, fontsize=11); xfmt(a, st)
    def d_aviv(f, sp, sm):
        st = "2015-01-01"; a = f.add_subplot(sp); ttl(a, "AVIV 溫度", sm)
        lv = ["AVm2", "AVm15", "AVm1", "AVp1", "AVp15", "AVp2"]; B = D[lv].dropna().loc[st:]
        edges = [B[lv[0]] * 0 + 1e-3] + [B[k] for k in lv] + [B[lv[-1]] * 0 + 50]
        for i, (_, cc, lab) in enumerate(AV_Z): a.fill_between(B.index, edges[i], edges[i + 1], color=cc, alpha=.13, lw=0)
        av = D["AVIV"].dropna().loc[st:]; R = D[lv].reindex(av.index).ffill()
        colored(a, av, [av_zone(av.iloc[i], R.iloc[i])[0] for i in range(len(av))], lw=1.7)
        a.set_yscale("log"); a.set_ylim(max(.3, av.min() * .8), av.max() * 1.25); a.axhline(1, color="#e2e8f0", ls="--", lw=1, alpha=.7)
        a.yaxis.set_major_locator(tk.FixedLocator([.4, .5, .6, .8, 1, 1.5, 2, 3])); a.yaxis.set_minor_locator(tk.NullLocator())
        a.yaxis.set_major_formatter(tk.FuncFormatter(lambda x, _: f"{x:g}"))
        for _, cc, lab in AV_Z: a.plot([], [], color=cc, lw=3, label=lab)
        a.legend(loc="upper right", ncol=7, fontsize=11); xfmt(a, st)
    def d_sthnupl(f, sp, sm):
        st = "2015-01-01"; a = f.add_subplot(sp); ttl(a, "短期持有者未實現損益 STH-NUPL", sm)
        n = S("STHNUPL", st); a.plot(n, color="#f5a623", lw=1.2, label="STH-NUPL")
        a.fill_between(n.index, 0, n.values, where=n.values > 0, color="#26a69a", alpha=.35, lw=0)
        a.fill_between(n.index, 0, n.values, where=n.values < 0, color="#e57373", alpha=.35, lw=0)
        a.axhline(0, color="#e2e8f0", ls="--", lw=1, alpha=.85); a.axhline(-0.2, color="#e57373", ls="--", lw=1, alpha=.85)
        a.annotate("-0.2", (pd.Timestamp(st), -0.2), xytext=(4, -14), textcoords="offset points", color="#e57373", fontsize=11)
        a.legend(loc="upper left", fontsize=11); xfmt(a, st)
    def d_mvrvz(f, sp, sm):
        st = "2011-01-01"; a = f.add_subplot(sp); ttl(a, "MVRV Z 分數", sm)
        a.plot(S("MCAP", st), color="#4a90e2", lw=1.4, label="BTC 市值"); a.plot(S("RCAP", st), color="#f5a623", lw=1.4, label="BTC 實現市值")
        a.set_yscale("log"); a.yaxis.set_major_locator(tk.FixedLocator([1e6, 1e8, 1e10, 1e12])); a.yaxis.set_minor_locator(tk.NullLocator())
        a.yaxis.set_major_formatter(tk.FuncFormatter(lambda x, _: {1e6: "$1M", 1e8: "$100M", 1e10: "$10B", 1e12: "$1T"}.get(x, "")))
        b = a.twinx(); z = S("MVRVZ", st); b.plot(z, color="#e57373", lw=1.4, label="MVRV Z 分數"); b.set_ylim(-2, 10); b.set_yticks([-2, 0, 2, 4, 6, 8, 10])
        b.axhline(0, color="#e2e8f0", ls="--", lw=1, alpha=.45); b.grid(False)
        for f0, t0, k in [("2011-01-01", "2011-12-31", "max"), ("2013-01-01", "2014-03-31", "max"), ("2017-01-01", "2018-03-31", "max"), ("2020-10-01", "2021-12-31", "max"),
                          ("2024-01-01", "2025-12-31", "max"), ("2011-09-01", "2012-06-30", "min"), ("2014-09-01", "2015-12-31", "min"), ("2018-06-01", "2019-06-30", "min"), ("2022-06-01", "2023-03-31", "min")]:
            seg = z.loc[f0:t0]
            if seg.empty: continue
            ti = seg.idxmax() if k == "max" else seg.idxmin(); vv = seg.loc[ti]
            b.annotate(f"{vv:.2f}", (ti, vv), xytext=(0, 8 if k == "max" else -16), textcoords="offset points", ha="center", fontsize=10, color="#f87171" if k == "max" else "#4ade80")
        h1, l1 = a.get_legend_handles_labels(); h2, l2 = b.get_legend_handles_labels(); a.legend(h1 + h2, l1 + l2, loc="upper left", ncol=3, fontsize=11); xfmt(a, st)

    DR = {"01_cost_bands.png": d_cost, "02_sth_deviation.png": d_sth, "03_aviv_heat.png": d_aviv, "04_risk.png": d_risk, "05_psip.png": d_psip,
          "07_sth_nupl.png": d_sthnupl, "08_price_risk.png": d_price_risk, "09_mvrv_z.png": d_mvrvz}
    paths = []
    for name, fn in DR.items():
        f = plt.figure(figsize=(16, 10), dpi=100); sp = f.add_gridspec(1, 1, left=.07, right=.93, top=.9, bottom=.07)[0]; fn(f, sp, False)
        f.text(.93, .955, f"資料日 {dd:%Y.%m.%d}", ha="right", fontsize=13, color="#a3a3a3")
        p = os.path.join(outdir, name); f.savefig(p); plt.close(f); paths.append(p)
    f = plt.figure(figsize=(32, 30), dpi=100)
    f.text(.03, .985, f"BTC 鏈上週期　{now:%Y.%m.%d} 台北時間 {now:%H:%M}｜BTC {price:,.0f}", fontsize=34, fontweight="bold", va="top")
    f.text(.97, .985, f"資料日 {dd:%Y.%m.%d}", fontsize=18, color="#a3a3a3", va="top", ha="right")
    G = f.add_gridspec(4, 2, height_ratios=[1.5, 1, 1, 1], left=.045, right=.965, top=.95, bottom=.025, hspace=.16, wspace=.12)
    for r, (L, R) in enumerate([(d_cost, d_price_risk), (d_sth, d_risk), (d_psip, d_aviv), (d_sthnupl, d_mvrvz)]):
        L(f, G[r, 0], True); R(f, G[r, 1], True)
    op = os.path.join(outdir, "00_overview.png"); f.savefig(op); plt.close(f)
    from PIL import Image
    im = Image.open(op).convert("RGB"); im.thumbnail((2000, 2000)); sp_ = os.path.join(outdir, "00_overview_small.jpg"); im.save(sp_, "JPEG", quality=88)
    return paths, op, sp_


def check_all(T):
    errs, warns, prices = S0.check(T)
    body = re.sub(r"\d{4}\.\d{2}\.\d{2} 台北時間 \d{1,2}:\d{2}", "", T)
    body = re.sub(r"\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}|\d{4} 年|\d+ 月|\d+ 日|\d+ 年期|MA\d+|30 日均值|30 天|60 天|\d+～\d+ 天|\d+ 天", "", body)
    nums = re.findall(r"(?<![\d.])[+\-]?\d+(?:[.,]\d+)*%?", body)
    norm = [x.lstrip("+") for x in nums]
    dup = sorted({x for x in norm if norm.count(x) > 1 and x not in ("0", "1", "2", "3")})
    if dup: errs.append("全文數字重複：" + ",".join(dup))
    if re.search(r"[（(][^）)]*[）)]", T): errs.append("有括號備註：" + ",".join(re.findall(r"[（(][^）)]*[）)]", T)))
    for name, pat in [("獲利供給沒跌破五成", r"五成"), ("長期持有者還沒虧損／投降", r"長期持有者[^。；]*(虧損|投降|賠錢|獲利|利潤)|投降"),
                      ("價格在短期持有者成本下面", r"(短期持有者成本|掉回)[^。；｜]*下面")]:
        n = len(re.findall(pat, T))
        if n != 1: errs.append(f"意思重複或缺：{name} 出現 {n} 次")
    return errs, warns, prices

def macro():
    """總經數字：以子程序跑 midlong_model.py（不 import）；box 與 charts repo 兩種目錄結構都找得到。"""
    for c in (os.path.join(ROOT, "scripts", "midlong_model.py"), os.path.join(ROOT, "btc", "midlong_model.py")):
        if os.path.isfile(c): return S0.macro_from_midlong(c)
    print("WARN midlong: midlong_model.py not found", file=sys.stderr); return {}

def cross_state(D, now):
    """內部通知用：以日收盤（網站價格序列）判斷現價在四條成本線的上下位置，與前一日比較是否穿越。"""
    cols = [("sth_cost", "STHRP", "短期持有者成本"), ("true_market_mean", "TMM", "真實市場均價"), ("realized_price", "RP", "已實現價格"), ("lth_cost", "LTHRP", "長期持有者成本")]
    X = D[["P"] + [c for _, c, _ in cols]].dropna()
    d1, d0 = X.index[-1], X.index[-2]; r1, r0 = X.loc[d1], X.loc[d0]
    lines = {}
    for key, c, lab in cols:
        s1 = "above" if r1["P"] > r1[c] else "below"; s0 = "above" if r0["P"] > r0[c] else "below"
        lines[key] = {"label": lab, "value": round(float(r1[c]), 2), "prev_value": round(float(r0[c]), 2), "side": s1, "prev_side": s0,
                      "crossed": s1 != s0, "direction": ("up" if s1 == "above" else "down") if s1 != s0 else None}
    return {"generated": now.isoformat(timespec="seconds"), "basis": "daily_close", "close_date": str(d1.date()), "close": round(float(r1["P"]), 2),
            "prev_close_date": str(d0.date()), "prev_close": round(float(r0["P"]), 2), "lines": lines,
            "any_cross": any(v["crossed"] for v in lines.values()), "crossed": [k for k, v in lines.items() if v["crossed"]],
            "notify": "internal_manager_only", "line_push": False}

def produce(imgdir, now=None):
    """正式與樣稿共用：一次抓價，同一個時間點產生文字、圖、自檢、midlong 用的狀態與內部通知狀態。"""
    now = now or datetime.now(TZ); os.makedirs(imgdir, exist_ok=True)
    D, meta = S0.load(); BC = S0.binance_cycle(); A = S0.analyse(D, meta, BC)
    price = S0.spot() or float(D["P"].dropna().iloc[-1]); M = macro()
    T, extra = text(A, price, M, now, D)
    paths, op, sp = charts(D, A, None, imgdir, now, price)
    errs, warns, prices = check_all(T)
    v, c = A["v"], A["cur"]
    st = {"generated": now.isoformat(timespec="seconds"), "data_date": str(A["today"].date()), "data_md": md(A["today"]), "price": price,
          "cycle": text_cycle(T), "ath": c["topP"], "ath_date": ymd(c["top"]), "cycle_low": c["lowP"], "cycle_low_date": ymd(c["low"]),
          "sth": v["STHRP"], "tmm": v["TMM"], "realized": v["RP"], "lth": v["LTHRP"], "bb_over": v["BBover"], "bb_cool": v["BBcool"], "bb_heated": v["BBheat"], "bb_overheated": v["BBhot"],
          "lth_sopr": v["LTHSOPR"], "sth_bbz": v["BBz"], "stood_sth": A["sth_ok"] is not None, "stood_sth_date": (str(A["sth_ok"].date()) if A["sth_ok"] is not None else None),
          "stood_sth_month": (int(A["sth_ok"].month) if A["sth_ok"] is not None else None), "below_sth_now": price < v["STHRP"]}
    fresh = all(t == "site" for t, _ in meta.values()) and (pd.Timestamp(now.date()) - A["today"]).days <= 4
    return dict(T=T, now=now, price=price, A=A, meta=meta, BC=BC, M=M, extra=extra, paths=paths, overview=op, small=sp,
                errs=errs, warns=warns, prices=prices, state=st, alert=cross_state(D, now), fresh=fresh)

def text_cycle(T):
    m = re.search(r"目前週期位置：([^\n]+)", T); return m.group(1).rstrip("。") if m else None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--draft", action="store_true", required=True)
    ap.add_argument("--date", default=datetime.now(TZ).strftime("%Y%m%d")); a = ap.parse_args()
    out = os.path.join(ROOT, "out", "drafts"); imgdir = os.path.join(out, f"onchain_six_{a.date}")
    R_ = produce(imgdir); T = R_["T"]
    open(os.path.join(out, f"onchain_six_draft_{a.date}.txt"), "w").write(T)
    json.dump({"generated": R_["now"].isoformat(), "data_date": R_["state"]["data_date"], "header_price": R_["price"], "binance_cycle": R_["BC"], "extra": R_["extra"],
               "series": {k: list(v) for k, v in R_["meta"].items()}, "check_errors": R_["errs"], "check_warnings": R_["warns"], "prices_in_text": R_["prices"],
               "images": R_["paths"], "overview": R_["overview"], "state": R_["state"], "alert": R_["alert"]},
              open(os.path.join(imgdir, "draft_meta.json"), "w"), ensure_ascii=False, indent=1, default=str)
    print(T); print("CHECK", "OK" if not R_["errs"] else "FAIL", R_["errs"], R_["warns"], file=sys.stderr)
    return 1 if R_["errs"] else 0

if __name__ == "__main__": sys.exit(main())

#!/usr/bin/env python3
"""/btccheckonchain 自有圖樣稿（只產樣稿，不推播、不上傳、不改正式檔）。
資料：只讀自家網站鏈上頁的公開資料檔（優先讀線上 /data/*.json，失敗或較舊時讀本機 4dan-site 副本）。
不 import publish_btc.py / short_model.py / midlong_model.py；總經數字以子程序執行 midlong_model.py 到暫存目錄取得。
用法：onchain_selfchart.py --draft [--date 20261117]
"""
import argparse, json, os, re, subprocess, sys, tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, requests

TZ = ZoneInfo("Asia/Taipei")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
SITE_URL = "https://4dan.app/data/"
SITE_LOCAL = "/workspace/4dan-site/web/public/data/"
CACHE = os.path.join(HERE, "selfchart_raw")
SERIES = {
    "P": "btc-daily", "STHRP": "bv-sth-realized", "LTHRP": "bv-lth-realized", "RP": "bv-realized", "TMM": "bv-tmm",
    "BBhot": "bv-sth-mvrv-bb-px-overheated", "BBheat": "bv-sth-mvrv-bb-px-heated", "BBcool": "bv-sth-mvrv-bb-px-cooled",
    "BBover": "bv-sth-mvrv-bb-px-oversold", "BBz": "bv-sth-mvrv-bb-z", "BBzhot": "bv-sth-mvrv-bb-z-hot", "BBzcold": "bv-sth-mvrv-bb-z-cold",
    "LTHSOPR": "bv-lth-sopr", "LTHMVRV": "bv-lth-mvrv", "LTHAVIVz": "bv-lth-aviv-z",
    "AVIV": "bv-aviv-coc", "MVRV": "bv-mvrv", "NUPL": "bv-nupl", "STHNUPL": "bv-sth-nupl", "LTHNUPL": "bv-lth-nupl",
    "STHMVRV": "bv-sth-mvrv", "SMmean": "bv-sth-mvrv-mean4y", "SMp1": "bv-sth-mvrv-p1", "SMp2": "bv-sth-mvrv-p2",
    "SMm1": "bv-sth-mvrv-m1", "SMm2": "bv-sth-mvrv-m2", "CBp2": "bv-sth-cb-p2", "CBp1": "bv-sth-cb-p1", "CBmean": "bv-sth-cb-mean",
    "CBm1": "bv-sth-cb-m1", "CBm2": "bv-sth-cb-m2", "RATIO": "bv-lth-sth-ratio", "RATIO60": "bv-lth-sth-ratio-sma60",
}
# 網站資料沒有、這版不補算的指標（回報用）
# 備註：AVIV LTH Z 用網站替代算法照畫，不與舊圖數字比較。
# MVRV LTH Z：對數版 expanding Z（2026-11-17 Manager 核定），見 lth_mvrv_logz()。
NOTES = ["AVIV LTH Z 為網站替代算法", "MVRV LTH Z 為對數版 expanding Z（自 2012-01-01 起算、最少 365 天、母體標準差）"]
LTHZ_START, LTHZ_MIN = "2012-01-01", 365

def lth_mvrv_logz(s):
    """MVRV LTH Z（對數版）：x=ln(LTH MVRV)，z_t=(x_t−mean(x_0..t))/pstdev(x_0..t)；
    樣本自 2012-01-01 起，缺值與非正值先剔除，累計滿 365 個樣本才輸出。"""
    s = s.dropna(); s = s[(s.index >= LTHZ_START) & (s > 0)]
    x = np.log(s); e = x.expanding(min_periods=LTHZ_MIN)
    return ((x - e.mean()) / e.std(ddof=0)).dropna()

def load_one(stem):
    pts_l = pts_r = None
    try:
        pts_l = json.load(open(SITE_LOCAL + stem + ".json"))["points"]
    except Exception as e: print("local miss", stem, e, file=sys.stderr)
    try:
        r = requests.get(SITE_URL + stem + ".json", timeout=40, headers={"User-Agent": "btc-analyst-onchain-draft"})
        if r.status_code == 200: pts_r = r.json()["points"]
    except Exception as e: print("remote miss", stem, e, file=sys.stderr)
    cands = [(p[-1]["time"], tag, p) for tag, p in (("site", pts_r), ("local", pts_l)) if p]
    if not cands: raise RuntimeError("no data " + stem)
    last, tag, pts = max(cands, key=lambda x: x[0])
    os.makedirs(CACHE, exist_ok=True); json.dump({"points": pts}, open(os.path.join(CACHE, stem + ".json"), "w"))
    s = pd.Series({pd.Timestamp(p["time"]): p["value"] for p in pts if p.get("value") is not None}, dtype=float).sort_index()
    return s, tag, last

def load():
    cols, meta = {}, {}
    for k, stem in SERIES.items():
        s, tag, last = load_one(stem); cols[k] = s; meta[k] = (tag, last)
    D = pd.DataFrame(cols).sort_index()
    raw = D["LTHSOPR"].dropna(); D["LTHSOPRraw"] = raw; D["LTHSOPR"] = raw.rolling(30).mean()  # 30 日均值
    D["LTHz"] = lth_mvrv_logz(D["LTHMVRV"])
    return D, meta

def binance_cycle():
    """幣安現貨 BTCUSDT 日 K（公開 spot klines，不碰 fapi）：前高＝盤中最高、這輪低點＝前高後盤中最低。"""
    import datetime as _dt
    start = int(_dt.datetime(2025, 1, 1, tzinfo=_dt.timezone.utc).timestamp() * 1000)
    for base in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            r = requests.get(base + "/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "1d", "startTime": start, "limit": 1000}, timeout=20)
            if r.status_code != 200: continue
            k = r.json(); hi = max(k, key=lambda x: float(x[2])); lo = min([x for x in k if x[0] >= hi[0]], key=lambda x: float(x[3]))
            ts = lambda ms: pd.Timestamp(ms, unit="ms").normalize()
            return dict(top=ts(hi[0]), topP=float(hi[2]), low=ts(lo[0]), lowP=float(lo[3]), src=base)
        except Exception as e: print("binance kline", base, e, file=sys.stderr)
    return None

def spot():
    for url, f in [("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", lambda j: float(j["price"])),
                   ("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT", lambda j: float(j["data"][0]["last"])),
                   ("https://www.bitstamp.net/api/v2/ticker/btcusd/", lambda j: float(j["last"]))]:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200: return f(r.json())
        except Exception: pass
    return None

def macro_from_midlong(path=None):
    """以腳本方式跑 midlong_model.py（--out 暫存目錄），只讀它的輸出，不 import。"""
    tmp = tempfile.mkdtemp(prefix="onchain_ml_")
    try:
        subprocess.run([sys.executable, path or os.path.join(ROOT, "scripts", "midlong_model.py"), "--out", tmp, "--style", "macro"],
                       check=True, capture_output=True, timeout=300)
        j = json.load(open(os.path.join(tmp, "midlong_model.json")))
    except Exception as e:
        print("WARN midlong:", e, file=sys.stderr); return {}
    st = j.get("states", {}); txt = j.get("mid", "")
    m = {"ma200_w": st.get("ma200_w"), "ma120_d": st.get("ma120_d"), "ma50_w": st.get("ma50_w"), "year_open": st.get("year_open"),
         "bear_year": st.get("bear_year"), "week_below_ma200": st.get("week_below_ma200"), "meeting": st.get("meeting_label"),
         "window": st.get("window_label") if st.get("window_open") else None, "w_macd": (st.get("w_macd_side"), st.get("w_macd_cross"))}
    r = re.search(r"美元 (\d+/\d+) 收到 ([\d.]+)(，再做新高)?", txt)
    if r: m.update(dxy_md=r.group(1), dxy=float(r.group(2)), dxy_high=bool(r.group(3)))
    r = re.search(r"10 年期 ([\d.]+)%、2 年期 ([\d.]+)%", txt)
    if r: m.update(y10=float(r.group(1)), y2=float(r.group(2)))
    m["hike_priced"] = "再升息" in txt
    return m

def n0(v): return f"{v:,.0f}"
def md(t): return f"{t.month}/{t.day}"
def ymd(t): return f"{t.year}/{t.month}/{t.day}"
def lastv(D, c): s = D[c].dropna(); return float(s.iloc[-1])
def held(cond, start, n=30):
    c = cond.loc[start:]; run = c.astype(int).groupby((~c).cumsum()).cumsum(); hit = run[run >= n]
    return hit.index[0] - pd.Timedelta(days=n - 1) if len(hit) else None

def analyse(D, meta, BC=None):
    P = D["P"].dropna(); today = D["STHMVRV"].dropna().index[-1]
    hist = [("2013-11-01", "2013-12-31", "2015-01-14"), ("2017-11-01", "2018-01-31", "2018-12-15"), ("2021-10-01", "2021-12-31", "2022-11-21")]
    past = []
    for t0, t1, bd in hist:
        top = P.loc[t0:t1].idxmax(); bot = pd.Timestamp(bd); w = D.loc[bot - pd.Timedelta(days=45): bot + pd.Timedelta(days=45)]
        sth_ok = held((D["P"] > D["STHRP"]).dropna(), bot); tmm_ok = held((D["P"] > D["TMM"]).dropna(), bot)
        past.append(dict(top=top, bot=bot, days=(bot - top).days, dd=1 - P[bot] / P[top], nupl=w["NUPL"].min(), lthm=w["LTHMVRV"].min(),
                         lthz=w["LTHz"].min(), sth_ok=sth_ok, tmm_ok=tmm_ok))
    if BC: top, low, topP, lowP = BC["top"], BC["low"], BC["topP"], BC["lowP"]
    else:
        top = P.loc["2024-01-01":].idxmax(); low = P.loc[top:].idxmin(); topP, lowP = float(P[top]), float(P[low])
    w = D.loc[low - pd.Timedelta(days=45): low + pd.Timedelta(days=45)]
    today_tp = pd.Timestamp(datetime.now(TZ).date())
    cur = dict(top=top, low=low, topP=topP, lowP=lowP, days=(today_tp - top).days, lowdays=(low - top).days,
               dd=1 - lowP / topP, nupl=float(w["NUPL"].min()), lthm=float(w["LTHMVRV"].min()), lthz=float(w["LTHz"].min()),
               lthaz=float(w["LTHAVIVz"].min()))
    sth_ok = held((D["P"] > D["STHRP"]).dropna(), low)
    v = {c: lastv(D, c) for c in D.columns}
    win60 = D.loc[today - pd.Timedelta(days=60):]
    # LTH/STH 成本比最近一次下穿 MA60
    r = (D["RATIO"] - D["RATIO60"]).dropna(); xdn = r[(r < 0) & (r.shift() >= 0)].index
    return dict(today=today, pdate=P.index[-1], past=past, cur=cur, sth_ok=sth_ok, v=v, meta=meta,
                sth_peak=(win60["STHMVRV"].idxmax(), float(win60["STHMVRV"].max())), sopr60=float(win60["LTHSOPR"].max()),
                lthz_cycmin=float(D.loc[top:, "LTHz"].min()), lthaz_cycmin=float(D.loc[top:, "LTHAVIVz"].min()),
                ratio_xdn=(xdn[-1] if len(xdn) else None))

def nupl_zone(x):
    return "投降區" if x < 0 else "希望／恐懼區" if x < .25 else "樂觀區" if x < .5 else "信念區" if x < .75 else "狂熱區"

SEP = "──────────"
def pos_line(p, lv, lowP):
    above = [n for n, x in lv if x > p]; below = [n for n, x in lv if x < p]
    s = "・價格"
    if "超賣線" in above: s += "已經跌破超賣線，上面的" + "、".join(n for n in above[::-1][1:4]) + "都變成壓力"
    elif above: s += "壓在" + "、".join(above[::-1][:3]) + "下面"
    else: s += "站在所有關鍵線上面"
    sup = (["這輪低點"] if lowP < p else []) + below
    sup.sort(key=lambda n: -(lowP if n == "這輪低點" else dict(lv)[n]))
    if sup: s += "；下面先看" + sup[0] + ("，再下面是" + "、".join(sup[1:]) if len(sup) > 1 else "")
    return s + "。"
def text(A, price, M, now):
    t, c, v, past = A["today"], A["cur"], A["v"], A["past"]
    p = price if price else v["P"]
    sup_lo = min(v["STHRP"], v["TMM"])
    if p < v["BBcool"] and p < sup_lo: regime = "熊市延續，價格掉回短期持有者成本下面的弱勢期"
    elif c["lowdays"] < 120 and (t - c["low"]).days < 60: regime = "熊市後段的探底期"
    elif v["AVIV"] < 1: regime = "熊市後段的修復期"
    else: regime = "底部之後的新上升段初期"
    L = ["BTC 鏈上週期", f"{now:%Y.%m.%d} 台北時間 {now:%H:%M}｜BTC {n0(p)}", "", f"目前週期位置：{regime}。", SEP, "【週期時間】",
         f"・前高在 {ymd(c['top'])} 的 {n0(c['topP'])}，到今天 {c['days']} 天。"
         + f"過去三輪的底都落在前高後 {min(x['days'] for x in past)}～{max(x['days'] for x in past)} 天，"
         + "、".join(f"{x['bot'].year} 年那次跌了 {x['dd']*100:.0f}%" for x in past) + "。",
         f"・這輪目前的低點是 {ymd(c['low'])} 的 {n0(c['lowP'])}，離前高 {c['lowdays']} 天，跌了 {c['dd']*100:.0f}%。", SEP, "【底部深度比較】",
         f"・低點附近 NUPL 最低 {c['nupl']:.2f}，過去三輪底部是 " + "、".join(f"{x['nupl']:.2f}" for x in past) + "。",
         f"・LTH MVRV 最低 {c['lthm']:.2f}，過去三輪是 " + "、".join(f"{x['lthm']:.2f}" for x in past) + "。",
         f"・MVRV LTH Z 最低 {c['lthz']:.2f}，過去三輪是 " + "、".join(f"{x['lthz']:.2f}" for x in past) + "。"]
    shallow = c["lthm"] > max(x["lthm"] for x in past)
    L.append("・這輪的底比過去淺很多，長期持有者還沒被洗到虧損，這個低點還不能當成週期大底。" if shallow else "・這輪的深度已經接近過去的週期大底。")
    def ym(x): return f"{x.year} 年 {x.month} 月" if x is not None else "未達成"
    tmm_now = p > v["TMM"]
    L += [SEP, "【確認底部條件】",
          f"・站回短期持有者成本並守住 30 天：前兩輪在 {ym(past[1]['sth_ok'])}、{ym(past[2]['sth_ok'])}做到，"
          + (f"這輪 {md(A['sth_ok'])} 做到過，但現在又掉回下面。" if A["sth_ok"] is not None and p < v["STHRP"] else f"這輪 {md(A['sth_ok'])} 已經做到。" if A["sth_ok"] is not None else "這輪還沒做到。"),
          f"・站穩真實市場均價：前兩輪到 {ym(past[1]['tmm_ok'])}、{ym(past[2]['tmm_ok'])}才做到，" + ("這輪目前站在上面。" if tmm_now else "這輪現在還在它下面。")]
    # 目前位置與關鍵價位（每個價位只寫一次）
    lv = [("過熱線", v["BBhot"]), ("偏熱線", v["BBheat"]), ("真實市場均價", v["TMM"]), ("短期持有者成本", v["STHRP"]),
          ("冷卻線", v["BBcool"]), ("超賣線", v["BBover"]), ("長期持有者成本", v["LTHRP"])]
    above = [n for n, x in lv if x > p]; below = [n for n, x in lv if x < p]
    L += [SEP, "【目前位置與關鍵價位】",
          f"・STH MVRV {md(A['sth_peak'][0])} 衝到 {A['sth_peak'][1]:.2f} 後一路回落；LTH SOPR 30 日均值近 60 天最高 {A['sopr60']:.2f}，現在已經跌破 1。" if v["LTHSOPR"] < 1 else
          f"・STH MVRV {md(A['sth_peak'][0])} 衝到 {A['sth_peak'][1]:.2f} 後一路回落；LTH SOPR 30 日均值近 60 天最高 {A['sopr60']:.2f}，現在還在 1 上面。",
          pos_line(p, lv, c["lowP"]),
          "・上方壓力：" + "｜".join(f"{n} {n0(x)}" for n, x in lv if x > p),
          "・下方支撐：" + "｜".join(f"{n} {n0(x)}" for n, x in lv if x < p)]
    dd = A["today"]
    L += [SEP, f"【指標一覽（資料日 {md(dd)}）】",
          f"・STH MVRV 布林帶｜振盪值 {v['BBz']:.2f}，" + ("已經掉進超賣區。" if v["BBz"] <= -1 else "還沒到超賣區。" if v["BBz"] < 1 else "在過熱區。"),
          f"・LTH SOPR（30 日均值）｜{v['LTHSOPR']:.2f}，" + ("長期持有者這一個月賣出平均是賠錢的。" if v["LTHSOPR"] < 1 else "長期持有者賣出還有賺，但利潤變薄。"),
          f"・MVRV LTH Z｜{v['LTHz']:.2f}，" + ("低於長期平均" if v["LTHz"] < 0 else "高於長期平均") + ("，也是這輪最低。" if round(v['LTHz'], 2) == round(A['lthz_cycmin'], 2) else f"，這輪最低到 {A['lthz_cycmin']:.2f}。"),
          f"・AVIV LTH Z｜{v['LTHAVIVz']:.2f}" + ("，也是這輪最低。" if round(v['LTHAVIVz'], 2) == round(A['lthaz_cycmin'], 2) else f"，這輪最低到 {A['lthaz_cycmin']:.2f}。"),
          f"・MVRV vs AVIV｜MVRV {v['MVRV']:.2f}、AVIV {v['AVIV']:.2f}，" + ("AVIV 跌破 1，價格落在真實市場均價下面，屬低估區。" if v["AVIV"] < 1 else "AVIV 在 1 上面。"),
          f"・NUPL｜{v['NUPL']:.2f}，屬於{nupl_zone(v['NUPL'])}。",
          f"・NUPL 分族群｜短期持有者 {v['STHNUPL']:+.2f}，長期持有者 {v['LTHNUPL']:+.2f}。",
          f"・STH MVRV｜{v['STHMVRV']:.2f}，" + ("短期持有者整體是虧損的。" if v["STHMVRV"] < 1 else "短期持有者整體還有獲利。")]
    # 訊號（白話）
    S = []
    if v["RATIO"] < v["RATIO60"]:
        S.append("長短期成本比還在 MA60 下面，短期買盤成本相對墊高，算偏多，站回 MA60 上面就失效。")
    else:
        S.append("長短期成本比站回 MA60 上面，短期買盤成本相對往下掉，這個偏多訊號先失效。")
    sm = v["STHMVRV"]
    if sm < v["SMm2"]: S.append("STH MVRV 跌破長期平均下方兩個標準差，短期持有者虧損已經到歷史少見的深度。")
    elif sm < v["SMm1"]: S.append("STH MVRV 跌破長期平均下方一個標準差，短期持有者虧損偏深，常見於恐慌段。")
    elif sm < v["SMmean"]: S.append("STH MVRV 在長期平均下面，但還沒到偏深的虧損區。")
    else: S.append("STH MVRV 在長期平均上面，短期持有者整體還不痛。")
    S.append("布林帶振盪值已經進超賣區，短線賣壓偏重，但超賣可以維持一段時間，不等於馬上見底。" if v["BBz"] <= -1 else
             "布林帶振盪值還沒進超賣區，還沒出現典型的投降訊號。")
    S.append("長期持有者的 SOPR 均值跌破 1，代表他們開始賠錢賣，這是熊市後段常見的現象，要等均值重新回到 1 上面才算賣壓退潮。" if v["LTHSOPR"] < 1 else
             "長期持有者賣出還有利潤，還沒出現投降。")
    L += [SEP, "【訊號】"] + ["・" + s for s in S]
    # 週期判斷（總經框架，口徑同 /btc 中長期）
    J = []
    k = []
    if M.get("bear_year"): k.append("今年大多數日線都收在年開盤價下面，這是熊市年")
    if M.get("ma200_w") and p < M["ma200_w"]: k.append(f"價格已經掉到週線 MA200 的 {n0(M['ma200_w'])} 下面")
    if M.get("ma120_d") and p < M["ma120_d"]: k.append(f"日線也還壓在 MA120 的 {n0(M['ma120_d'])} 下面")
    if k: J.append("・K 線位置：" + "，".join(k) + "。")
    mm = []
    if M.get("dxy"): mm.append(f"美元 {M['dxy_md']} 收在 {M['dxy']:.2f}" + ("，還在創高" if M.get("dxy_high") else ""))
    if M.get("y10") and M.get("y2"): mm.append(f"10 年期 {M['y10']:.2f}%、2 年期 {M['y2']:.2f}%" + ("，2 年期還高過政策利率，市場在押再升息，資金面沒有放鬆" if M.get("hike_priced") else ""))
    if mm: J.append("・總經：" + "；".join(mm) + "。殖利率還沒見頂回落，風險資產通常要等殖利率轉頭後才真正落底。")
    J.append("・鏈上：" + ("長期持有者整體還有獲利，鏈上沒有走到過去幾輪那種重置，" if shallow else "鏈上已接近過去幾輪的重置深度，")
             + ("長期持有者成本也還沒被測試。" if p > v["LTHRP"] else "價格已經跌到長期持有者成本下面。"))
    w = M.get("window"); mt = M.get("meeting")
    J.append("・時間上或許已經接近尾聲，價格卻不一定到底" + (f"，弱勢窗口還開到 {w}" if w else "") + (f"，{mt}的利率會議是下一個關口" if mt else "") + "。")
    J.append("・怎麼做：不追多，長線只用現貨分批，不一次做滿；反彈先看成更低的高點，不急著當成底部。")
    J.append("・什麼情況代表看錯：週線收盤重新站回短期持有者成本並守住，偏空這套就要重估；週線或月線收盤跌破這輪低點，就改看長期持有者成本那一帶。")
    L += [SEP, "【週期判斷】"] + J
    return "\n".join(L) + "\n"

# ---------------- charts ----------------
def charts(D, A, outdir, now, price):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    from matplotlib.collections import LineCollection
    import matplotlib.dates as mdates
    cjk = next((f for f in ["Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Serif CJK TC"] if any(f == x.name for x in fm.fontManager.ttflist)), "sans-serif")
    BG, GRID, TX = "#000000", "#1a1a1a", "#e5e5e5"
    C = dict(price="#4a90e2", green="#26a69a", red="#e57373", orange="#f5a623", yellow="#ffd54f", blue="#4a90e2", purple="#ab47bc",
             pink="#f472b6", teal="#2dd4bf", slate="#94a3b8", sky="#3b82f6")
    plt.rcParams.update({"font.family": [cjk], "font.size": 13, "text.color": TX, "axes.labelcolor": TX, "xtick.color": "#a3a3a3",
                         "ytick.color": "#a3a3a3", "axes.edgecolor": "#262626", "figure.facecolor": BG, "axes.facecolor": BG,
                         "savefig.facecolor": BG, "axes.grid": True, "grid.color": GRID, "grid.linewidth": .8, "legend.frameon": False,
                         "axes.unicode_minus": False})
    dd = A["today"]; paths = []
    def fig2(title, start):
        f = plt.figure(figsize=(16, 10), dpi=100)
        g = f.add_gridspec(2, 1, height_ratios=[1.15, 1], hspace=.06, left=.07, right=.93, top=.9, bottom=.06)
        a1 = f.add_subplot(g[0]); a2 = f.add_subplot(g[1], sharex=a1)
        f.suptitle(title, fontsize=22, fontweight="bold", x=.07, ha="left", y=.965)
        f.text(.93, .935, f"資料日 {dd:%Y.%m.%d}", ha="right", fontsize=13, color="#a3a3a3")
        plt.setp(a1.get_xticklabels(), visible=False)
        a1.set_yscale("log"); a1.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        a2.set_xlim(pd.Timestamp(start), dd + pd.Timedelta(days=60)); a2.xaxis.set_major_locator(mdates.YearLocator()); a2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        return f, a1, a2
    def S(c, start): return D[c].dropna().loc[start:]
    def px(a, start, color=C["price"], lw=1.4): a.plot(S("P", start), color=color, lw=lw, label="價格")
    def save(f, name, a1, a2, l2=True):
        a1.legend(loc="upper left", ncol=4, fontsize=12)
        if l2: a2.legend(loc="upper left", ncol=5, fontsize=12)
        p = os.path.join(outdir, name); f.savefig(p); plt.close(f); paths.append(p)
    def ylim_px(a, start, *cols):
        vals = pd.concat([S(c, start) for c in ("P",) + cols]); a.set_ylim(max(vals.min() * .8, 1), vals.max() * 1.25)
    def colored(a, s, lvl_colors, lw=1.3):
        x = mdates.date2num(s.index.to_pydatetime()); y = s.values
        seg = np.stack([np.column_stack([x[:-1], y[:-1]]), np.column_stack([x[1:], y[1:]])], axis=1)
        cols = [lvl_colors(val) for val in (y[:-1] + y[1:]) / 2]
        a.add_collection(LineCollection(seg, colors=cols, linewidths=lw))

    # 1 STH MVRV 布林帶
    st = "2018-01-01"; f, a1, a2 = fig2("STH MVRV 布林帶", st); px(a1, st)
    a1.plot(S("STHRP", st), color=C["pink"], lw=1.6, label="短期持有者成本")
    a1.plot(S("BBhot", st), color=C["red"], lw=1, ls="--", alpha=.85, label="過熱線")
    a1.plot(S("BBheat", st), color=C["orange"], lw=1, ls="--", alpha=.8, label="偏熱線")
    a1.plot(S("BBcool", st), color=C["blue"], lw=1, ls="--", alpha=.75, label="冷卻線")
    a1.plot(S("BBover", st), color=C["green"], lw=1.2, alpha=.85, label="超賣線"); ylim_px(a1, st)
    z = S("BBz", st); a2.plot(z, color="#c47a3a", lw=1.2, label="振盪值")
    a2.fill_between(z.index, 0, z.values, color=(180/255, 100/255, 40/255, .35), lw=0)
    a2.fill_between(z.index, 1, z.values, where=z.values > 1, color="#f44870", alpha=.55, lw=0)
    a2.fill_between(z.index, -1, z.values, where=z.values < -1, color="#30c070", alpha=.55, lw=0)
    for y, c_, ls in [(1, "#f44870", "-"), (-1, "#30c070", "-"), (0, "#e2e8f0", "--")]: a2.axhline(y, color=c_, ls=ls, lw=1, alpha=.8)
    save(f, "01_sth_mvrv_bollinger.png", a1, a2)

    # 2 LTH SOPR
    st = "2015-01-01"; f, a1, a2 = fig2("LTH SOPR（30 日均值）", st); px(a1, st); a1.plot(S("LTHRP", st), color=C["teal"], lw=1.5, label="長期持有者成本"); ylim_px(a1, st)
    s = S("LTHSOPR", st); colored(a2, s, lambda y: C["green"] if y >= 1 else C["red"], lw=1.5)
    a2.set_yscale("log"); a2.set_ylim(max(.4, s.min() * .9), s.max() * 1.15); a2.axhline(1, color="#e2e8f0", lw=1, alpha=.85)
    a2.plot([], [], color=C["green"], label="> 1"); a2.plot([], [], color=C["red"], label="< 1")
    a2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:g}"))
    save(f, "02_lth_sopr.png", a1, a2)

    # 3 MVRV LTH Z（對數版）
    f, a1, a2 = fig2("MVRV LTH Z", st); px(a1, st); a1.plot(S("LTHRP", st), color=C["teal"], lw=1.5, label="長期持有者成本"); ylim_px(a1, st)
    a2.plot(S("LTHz", st), color=C["orange"], lw=1.4, label="MVRV LTH Z")
    for y, c_ in [(1.5, C["red"]), (1.25, C["orange"]), (0, "#e2e8f0"), (-.8, "#3b82f6"), (-1, C["green"])]: a2.axhline(y, color=c_, ls="--", lw=1, alpha=.7)
    zz = S("LTHz", st); a2.set_ylim(min(-1.5, zz.min() - .2), max(2, zz.max() + .2))
    save(f, "03_mvrv_lth_z.png", a1, a2)


    # 4 AVIV LTH Z
    f, a1, a2 = fig2("AVIV LTH Z", st); px(a1, st, color="#ffffff", lw=1)
    a1.plot(S("LTHRP", st), color="#2681ff", lw=1.8, label="長期持有者成本"); a1.plot(S("TMM", st), color="#8264ff", lw=1.8, label="真實市場均價"); ylim_px(a1, st)
    a2.plot(S("LTHAVIVz", st), color="#2681ff", lw=1.4, label="AVIV LTH Z")
    for y, c_ in [(1.5, "#f74870"), (1.25, "#f7931a"), (.8, "#ffffff"), (-.8, "#2681ff"), (-1, "#38d996")]: a2.axhline(y, color=c_, ls="--", lw=1, alpha=.8)
    save(f, "04_aviv_lth_z.png", a1, a2)

    # 5 MVRV vs AVIV
    f, a1, a2 = fig2("MVRV vs AVIV", st); px(a1, st)
    a1.plot(S("TMM", st), color=C["blue"], lw=1.6, alpha=.6, ls="-", label="真實市場均價"); a1.plot(S("RP", st), color=C["purple"], lw=1.5, label="已實現價格"); ylim_px(a1, st)
    a2.plot(S("MVRV", st), color=C["orange"], lw=1.4, label="MVRV"); a2.plot(S("AVIV", st), color=C["teal"], lw=1.4, label="AVIV")
    a2.axhline(1, color="#e2e8f0", ls="--", lw=1, alpha=.8)
    save(f, "05_mvrv_vs_aviv.png", a1, a2)

    # 6 NUPL
    zones = [(0, C["red"], "投降"), (.25, C["orange"], "希望／恐懼"), (.5, C["yellow"], "樂觀"), (.75, C["green"], "信念"), (9, C["blue"], "狂熱")]
    def zc(y):
        for m, c_, _ in zones:
            if y < m: return c_
        return zones[-1][1]
    f, a1, a2 = fig2("NUPL", st); px(a1, st); ylim_px(a1, st)
    s = S("NUPL", st); colored(a2, s, zc, lw=1.4); a2.set_ylim(-.5, 1); a2.axhline(0, color="#e2e8f0", ls="--", lw=1, alpha=.7)
    for _, c_, lab in zones: a2.plot([], [], color=c_, label=lab)
    save(f, "06_nupl.png", a1, a2)

    # 7 NUPL 分族群
    f, a1, a2 = fig2("NUPL 分族群", st); px(a1, st); ylim_px(a1, st)
    for lo, hi, c_ in [(-1, 0, (180, 40, 70, .20)), (0, .25, (244, 144, 24, .14)), (.25, .5, (252, 192, 72, .12)), (.5, .75, (48, 132, 96, .14)), (.75, 4, (36, 128, 252, .16))]:
        a2.axhspan(lo, hi, color=(c_[0] / 255, c_[1] / 255, c_[2] / 255, c_[3]), lw=0)
    a2.plot(S("STHNUPL", st), color=C["orange"], lw=1.3, label="短期持有者 NUPL"); a2.plot(S("LTHNUPL", st), color=C["green"], lw=1.5, label="長期持有者 NUPL")
    a2.axhline(0, color="#e2e8f0", ls="--", lw=1, alpha=.7); a2.set_ylim(-1, 1.05)
    save(f, "07_nupl_cohort.png", a1, a2)

    # 8 STH MVRV
    f, a1, a2 = fig2("STH MVRV", st); px(a1, st); a1.plot(S("STHRP", st), color=C["pink"], lw=1.5, label="短期持有者成本")
    for c, col, lab in [("CBp2", C["red"], "+2σ"), ("CBp1", C["orange"], "+1σ"), ("CBmean", C["slate"], "均值"), ("CBm1", C["sky"], "−1σ"), ("CBm2", C["green"], "−2σ")]:
        a1.plot(S(c, st), color=col, lw=.9, ls="--", alpha=.6)
    ylim_px(a1, st)
    a2.plot(S("STHMVRV", st), color="#f44870", lw=1.4, label="STH MVRV")
    for c, col, lab in [("SMp2", C["red"], "+2σ"), ("SMp1", C["orange"], "+1σ"), ("SMmean", C["slate"], "均值"), ("SMm1", C["sky"], "−1σ"), ("SMm2", C["green"], "−2σ")]:
        a2.plot(S(c, st), color=col, lw=1, ls="--", alpha=.75, label=lab)
    a2.axhline(1, color="#e2e8f0", ls="--", lw=1, alpha=.85); a2.set_ylim(.4, 3)
    save(f, "08_sth_mvrv.png", a1, a2)

    # 總覽
    from PIL import Image, ImageDraw, ImageFont
    ims = [Image.open(p).convert("RGB") for p in paths]; w, h = ims[0].size; cols = 2; rows = (len(ims) + 1) // 2; head = 110
    ov = Image.new("RGB", (w * cols, h * rows + head), (0, 0, 0))
    for i, im in enumerate(ims): ov.paste(im, ((i % cols) * w, head + (i // cols) * h))
    fp = fm.findfont(fm.FontProperties(family=cjk, weight="bold"))
    ImageDraw.Draw(ov).text((40, 22), f"BTC 鏈上週期　{now:%Y.%m.%d} 台北時間 {now:%H:%M}｜BTC {price:,.0f}", fill=(229, 229, 229), font=ImageFont.truetype(fp, 60))
    op = os.path.join(outdir, "00_overview.png"); ov.save(op, optimize=True)
    sm = ov.copy(); sm.thumbnail((2000, 4000)); sp = os.path.join(outdir, "00_overview_small.jpg"); sm.save(sp, "JPEG", quality=88)
    return paths, op, sp

# ---------------- self check ----------------
BANNED = r"CheckOnChain|checkonchain|Check On Chain|Bitview|bitview|Coin ?Metrics|coinmetrics|cryptochan|0xcryptochan|Cowen|\bBen\b|Glassnode|CoinGlass|Bitcoin Research Kit|BRK|4dan|https?://|街哥|鋒哥|資料：|資料來源|【結論】"
def check(T):
    errs = []
    for m in re.finditer(BANNED, T): errs.append("來源名或禁用段：" + m.group(0))
    for pat, why in [(r"距", "距離描述"), (r"[+\-−]\d+(\.\d+)?%", "帶正負號的百分比"), (r"SMA\s?120|120\s?(日)?均線", "MA120 寫法"),
                     (r"UTC|\(UTC", "時區寫法"), (r"數天|數週|數月", "週期標籤"), (r"現價來源|價格來源", "現價來源"), (r"免責|不構成投資建議", "免責聲明"),
                     (r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "emoji"), (r"約\s?\d+%", "百分比距離（約 x%）")]:
        for m in re.finditer(pat, T): errs.append(f"{why}：{m.group(0)}")
    prices = []
    for m in re.finditer(r"(?<![\d/.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?K)(?![\d/])", T):
        s = m.group(1); val = float(s[:-1]) * 1000 if s.endswith("K") else float(s.replace(",", "")); prices.append((s, val))
    seen = {}
    for s, val in prices:
        for s2, v2 in seen.items():
            if abs(val - v2) <= max(60, v2 * 0.0006): errs.append(f"價位重複：{s2} / {s}")
        seen[s] = val
    # 各段數字重複（警告）
    warns = []
    for sec in re.split(SEP, T):
        nums = [x for x in re.findall(r"(?<![\d/])[+\-]?\d+(?:[.,]\d+)*(?![\d/])", re.sub(r"\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}|\d{4} 年|\d+ 月|\d{1,2}:\d{2}|\d+ 日|\d+ 年期|\d{4}\.\d{2}\.\d{2}", "", sec))]
        d = {x for x in nums if nums.count(x) > 1 and x not in ("1", "0", "30", "60", "2", "3")}
        if d: warns.append("同段數字重複：" + ",".join(sorted(d)))
    return errs, warns, [s for s, _ in prices]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--draft", action="store_true", required=True, help="只產樣稿（必填）")
    ap.add_argument("--date", default=datetime.now(TZ).strftime("%Y%m%d")); a = ap.parse_args()
    now = datetime.now(TZ); out = os.path.join(ROOT, "out", "drafts"); imgdir = os.path.join(out, f"onchain_selfchart_{a.date}"); os.makedirs(imgdir, exist_ok=True)
    D, meta = load(); BC = binance_cycle(); A = analyse(D, meta, BC); price = spot() or float(D["P"].dropna().iloc[-1]); M = macro_from_midlong()
    T = text(A, price, M, now)
    paths, op, sp = charts(D, A, imgdir, now, price)
    errs, warns, prices = check(T)
    tp = os.path.join(out, f"onchain_selfchart_draft_{a.date}.txt"); open(tp, "w").write(T)
    info = {"generated": now.isoformat(), "data_date": str(A["today"].date()), "price_date": str(A["pdate"].date()), "header_price": price,
            "series": {k: list(v) for k, v in meta.items()}, "macro": M, "notes": NOTES, "binance_cycle": BC, "check_errors": errs, "check_warnings": warns,
            "prices_in_text": prices, "images": paths, "overview": op, "overview_small": sp}
    json.dump(info, open(os.path.join(imgdir, "draft_meta.json"), "w"), ensure_ascii=False, indent=1, default=str)
    print(T); print("CHECK", "OK" if not errs else "FAIL", errs, warns, file=sys.stderr)
    return 1 if errs else 0

if __name__ == "__main__": sys.exit(main())

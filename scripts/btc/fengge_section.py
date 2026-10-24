"""【短線交易模型】 for /btc: mechanical short-term price-action model
(/workspace/big-players/fengge/full_model.md, approved by Daniel 2026-10-24). Reference block only; does not replace the main model.
Rules: 1) trend filter = price vs 50-week MA + daily swing structure (HH/HL or LH/LL); only the trend direction is labelled.
2) entry A = 0.5-0.618 retracement zone + 1H/4H reversal (false break reclaimed, double-bottom/top neckline retest);
   entry B = breakout then retest holds. 3) stop beyond the prior swing, T1 prior high/low, T2 fib extension 1.382-1.618.
4) half off at T1, rest stop to entry. 5) counter-trend shorts down-weighted.
Free public data: OKX BTC-USDT candles (1Wutc/1Dutc/4H/1H). Stdlib only.
Usage: fengge_section.py [--price 80780] [--json]  -> prints the section, or nothing on failure (exit 0)."""
import json, os, re, sys, urllib.request
_CACHE = {}
TITLE = "【交易模型】"
UA = {"User-Agent": "btc-fengge/1.0"}
B = "https://www.okx.com/api/v5/market/"
def _get(u, t=10): return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t))
def bars(bar, n):
    """Closed bars oldest->newest as (ts,o,h,l,c), plus the live bar (or None)."""
    out, after = [], ""
    while len(out) < n + 1:
        d = _get(f"{B}{'history-candles' if after else 'candles'}?instId=BTC-USDT&bar={bar}&limit=100{after}")["data"]
        if not d: break
        out += d; after = f"&after={d[-1][0]}"
    rows = [(int(r[0]), *map(float, r[1:5]), r[8]) for r in out]
    rows.sort(key=lambda r: r[0])
    live = rows[-1][:5] if rows and rows[-1][5] == "0" else None
    closed = [r[:5] for r in rows if r[5] == "1"]
    return closed[-n:], live
def pivots(b, k):
    """Fractal swing highs/lows (k bars each side): lists of (idx, price)."""
    H, L = [], []
    for i in range(k, len(b) - k):
        w = b[i - k:i + k + 1]
        if b[i][2] == max(x[2] for x in w): H.append((i, b[i][2]))
        if b[i][3] == min(x[3] for x in w): L.append((i, b[i][3]))
    return H, L
def n0(x): return f"{x:,.0f}"
def analyse(price=None):
    w, _ = bars("1Wutc", 50)
    d, dl = bars("1Dutc", 300)
    h4, h4l = bars("4H", 120)
    h1, h1l = bars("1H", 72)
    _CACHE.update(w=w, d=d, h4=h4)
    if len(w) < 50 or len(d) < 60 or len(h4) < 60: raise ValueError("not enough bars")
    px = price or (h1l or h1[-1])[4]
    ma50w = sum(x[4] for x in w) / 50
    H, L = pivots(d, 3)
    if len(H) < 2 or len(L) < 2: raise ValueError("no swings")
    (h1i, hp1), (h2i, hp2) = H[-2], H[-1]
    (l1i, lp1), (l2i, lp2) = L[-2], L[-1]
    if hp2 > hp1 and lp2 > lp1: ds = "HH/HL 多頭"
    elif hp2 < hp1 and lp2 < lp1: ds = "LH/LL 空頭"
    else: ds = "高低點混合"
    # a daily close below the last swing low breaks HL (above last swing high breaks LH)
    lc = d[-1][4]
    if ds.endswith("多頭") and lc < lp2: ds = "HL 已跌破，多頭結構失效"
    if ds.endswith("空頭") and lc > hp2: ds = "LH 已突破，空頭結構失效"
    above = px > ma50w
    if above and ds.endswith("多頭"): side = "long"
    elif not above and ds.endswith("空頭"): side = "short"
    else: side = None
    r = dict(price=px, ma50w=ma50w, daily=ds, side=side, swing_hi=hp2, swing_lo=lp2)
    # impulse leg for retracement: long = last swing low before last swing high -> that high; short mirrored
    if side == "long" or (side is None and not ds.endswith("空頭")):
        lo = ([p for i, p in L if i < h2i] or [lp2])[-1]; hi = hp2; dirn = "long"
        z = (hi - 0.618 * (hi - lo), hi - 0.5 * (hi - lo))
    else:
        hi = ([p for i, p in H if i < l2i] or [hp2])[-1]; lo = lp2; dirn = "short"
        z = (lo + 0.5 * (hi - lo), lo + 0.618 * (hi - lo))
    r.update(dirn=dirn, leg=(lo, hi), zone=z)
    inzone = z[0] * 0.995 <= px <= z[1] * 1.005
    # 4H/1H reversal triggers (last 12 4H bars / 24 1H bars)
    trig = []
    for name, b, look, k in (("4H", h4, 12, 2), ("1H", h1, 24, 2)):
        sh, sl = pivots(b[:-look] if len(b) > look + 10 else b, k)
        rec = b[-look:]
        if dirn == "long" and sl:
            ref = sl[-1][1]
            if min(x[3] for x in rec) < ref and rec[-1][4] > ref: trig.append(f"{name} 假跌破 {n0(ref)} 後收回")
        if dirn == "short" and sh:
            ref = sh[-1][1]
            if max(x[2] for x in rec) > ref and rec[-1][4] < ref: trig.append(f"{name} 假突破 {n0(ref)} 後跌回")
    sh4, sl4 = pivots(h4, 2)
    if dirn == "long" and len(sl4) >= 2:
        (a, pa), (b_, pb) = sl4[-2], sl4[-1]
        if abs(pa - pb) / pa < 0.008 and b_ - a >= 3:
            neck = max(x[2] for x in h4[a:b_ + 1])
            if px > neck * 0.995 and px < neck * 1.01 and max(x[4] for x in h4[b_:]) > neck: trig.append(f"4H 雙底頸線 {n0(neck)} 回踩")
    if dirn == "short" and len(sh4) >= 2:
        (a, pa), (b_, pb) = sh4[-2], sh4[-1]
        if abs(pa - pb) / pa < 0.008 and b_ - a >= 3:
            neck = min(x[3] for x in h4[a:b_ + 1])
            if px < neck * 1.005 and px > neck * 0.99 and min(x[4] for x in h4[b_:]) < neck: trig.append(f"4H 雙頂頸線 {n0(neck)} 回踩")
    # entry B: breakout of the prior daily swing within 10 days, retest holding (daily closes stay beyond it)
    brk = None
    if dirn == "long":
        lvl = hp2 if h2i < len(d) - 3 else None
        if lvl and any(x[4] > lvl for x in d[-10:]) and all(x[4] > lvl * 0.995 for x in d[-3:]) and px < lvl * 1.015 and px > lvl: brk = f"突破 {n0(lvl)} 後回踩不破"
    else:
        lvl = lp2 if l2i < len(d) - 3 else None
        if lvl and any(x[4] < lvl for x in d[-10:]) and all(x[4] < lvl * 1.005 for x in d[-3:]) and px > lvl * 0.985 and px < lvl: brk = f"跌破 {n0(lvl)} 後回抽不過"
    if brk: trig.append(brk)
    r.update(inzone=inzone, triggers=trig)
    # plan: stop beyond the nearest 4H swing (fallback daily swing), T1 prior daily swing, T2 1.382-1.618 extension
    if dirn == "long":
        stop = (sl4[-1][1] if sl4 else lp2) * 0.997
        t1 = hp2 if hp2 > px else max(x[2] for x in d[-60:])
        t2 = (lo + 1.382 * (hi - lo), lo + 1.618 * (hi - lo))
    else:
        stop = (sh4[-1][1] if sh4 else hp2) * 1.003
        t1 = lp2 if lp2 < px else min(x[3] for x in d[-60:])
        t2 = (hi - 1.382 * (hi - lo), hi - 1.618 * (hi - lo))
    r.update(stop=stop, t1=t1, t2=t2)
    return r
def short_lines(r):
    px = r["price"]
    pos = "上方" if px > r["ma50w"] else "下方"
    if r["side"] == "long": tr = "順勢方向：多（只看低多）"
    elif r["side"] == "short": tr = "順勢方向：空（只看高空）"
    else: tr = "方向不一致：不做（只做順勢，放掉勝率低的交易）"
    L = [f"・順勢濾網：BTC {n0(px)} 在 50 週均線 {n0(r['ma50w'])} {pos}｜日線 {r['daily']}｜{tr}"]
    lo, hi = r["leg"]; z = r["zone"]
    leg = f"{n0(lo)}→{n0(hi)}" if r["dirn"] == "long" else f"{n0(hi)}→{n0(lo)}"
    zs = "價格在區內" if r["inzone"] else ("價格在區上方" if px > z[1] else "價格在區下方")
    L.append(f"・{"回檔區" if r["dirn"] == "long" else "反彈區"} 0.5–0.618：{n0(z[0])}–{n0(z[1])}（波段 {leg}）｜{zs}")
    t = r["triggers"]
    L.append("・反轉訊號：" + ("、".join(t) if t else "1H/4H 尚無假跌破收回、頸線回踩或突破回踩"))
    word = "多" if r["dirn"] == "long" else "空"
    if r["side"] is None:
        state = "趨勢不一致，不列進場"
        if r["dirn"] == "short": state = "趨勢不一致，逆勢空單降權，不列進場"
    elif (r["inzone"] or any("回踩" in x or "回抽" in x for x in t)) and t:
        state = f"條件成立，可參考{word}單"
    else: state = "條件未齊，等待"
    L.append(f"・狀態：{state}")
    if r["side"] is not None:
        L.append(f"・{word}單計畫：止損 {n0(r['stop'])}（前波段外）｜T1 {n0(r['t1'])}｜T2 {n0(min(r['t2']))}–{n0(max(r['t2']))}（斐波 1.382–1.618）｜到 T1 出一半，餘倉止損移到開倉價")
    return L
import os
HERE = os.path.dirname(os.path.abspath(__file__))
def _rd(p):
    try: return json.load(open(os.path.join(HERE, p)))
    except Exception: return None
def big_trend(r, w, d):
    """Score the big trend from objective items (+1 bull / -1 bear / 0). Untested weighting."""
    px = r["price"]; it = []
    def add(name, v, txt): it.append((name, v, txt))
    add("50w", 1 if px > r["ma50w"] else -1, f"50 週均線 {n0(r['ma50w'])} {'上方' if px > r['ma50w'] else '下方'}")
    WH, WL = pivots(w, 2)
    if len(WH) >= 2 and len(WL) >= 2:
        up = WH[-1][1] > WH[-2][1]; hl = WL[-1][1] > WL[-2][1]
        v = 1 if up and hl else -1 if not up and not hl else 0
        add("wk", v, "週線 " + ("HH/HL 多頭" if v > 0 else "LH/LL 空頭" if v < 0 else "高低點混合"))
    ds = r["daily"]; add("d", 1 if ds.endswith("多頭") and "失效" not in ds else -1 if ds.endswith("空頭") and "失效" not in ds else 0, "日線 " + ds)
    ma120 = sum(x[4] for x in d[-120:]) / 120
    add("ma120", 1 if px > ma120 else -1, f"日線 MA120 {n0(ma120)} {'上方' if px > ma120 else '下方'}")
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    def open_at(day):
        ms = int(day.timestamp() * 1000)
        for x in d:
            if x[0] == ms: return x[1]
    for lab, day in (("今年開盤", dt.datetime(now.year, 1, 1, tzinfo=dt.timezone.utc)), ("本月開盤", dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc))):
        o = open_at(day)
        if o: add(lab, 1 if px > o else -1, f"{lab} {n0(o)} {'上方' if px > o else '下方'}")
    oc = _rd("out/repo_state/onchain_latest.json") or {}
    cur = ((oc.get("cryptochan") or {}).get("current")) or {}
    tmm = cur.get("tmm")
    if tmm:
        add("tmm", 1 if px > tmm else -1, f"鏈上市場均價 {n0(tmm)} {'上方' if px > tmm else '下方'}")
    m = re.search(r"週期位置：([^，。\n]+)", oc.get("text", ""))
    if m: it.append(("cycle", 0, "鏈上週期：" + m.group(1)))
    if cur:
        sig = [cur.get("ratio", 9) < cur.get("ratio60", 0), cur.get("sthrp", 0) > cur.get("ma200", 9e9), cur.get("rev30", 0) > cur.get("rev365", 9e9)]
        k = sum(sig); add("oc", 1 if k >= 2 else -1, f"鏈上訊號 {k}/3 偏多")
    cq = ((_rd("out/cq_consensus.json") or {}).get("consensus")) or {}
    z = cq.get("z_score")
    if z is not None:
        v = -1 if z >= 0.8 else 1 if z <= -1.5 else 0
        add("cq", v, f"分析師共識 σ {z:+.2f} " + ("偏熱（反向偏空）" if v < 0 else "極度悲觀（反向偏多）" if v > 0 else "中性"))
    sc = sum(v for _, v, _ in it); n = sum(1 for k_, _, _ in it if k_ != "cycle")
    dirn = "偏多" if sc >= 3 else "偏空" if sc <= -3 else ("震盪偏多" if sc > 0 else "震盪偏空" if sc < 0 else "震盪")
    lv = sorted([x for x in [r["ma50w"], tmm] if x])
    return dict(score=sc, n=n, dirn=dirn, items=it, floor=lv, ma120=ma120)
def nearest(levels, px, up):
    c = sorted({round(x) for x in levels if (x > px * 1.008 if up else x < px * 0.992)}, reverse=not up)
    out = []
    for x in c:
        if not out or abs(x - out[-1]) / out[-1] >= 0.008: out.append(x)
    return out
def short_bias(r, d, h4):
    px = r["price"]; sc = 0
    ds = r["daily"]; sc += 1 if ds.endswith("多頭") and "失效" not in ds else -1 if ds.endswith("空頭") and "失效" not in ds else 0
    H4, L4 = pivots(h4, 2)
    if len(H4) >= 2 and len(L4) >= 2:
        sc += 1 if H4[-1][1] > H4[-2][1] and L4[-1][1] > L4[-2][1] else -1 if H4[-1][1] < H4[-2][1] and L4[-1][1] < L4[-2][1] else 0
    ma20 = sum(x[4] for x in h4[-20:]) / 20; sc += 1 if px > ma20 else -1
    rs = (_rd("out/range_state.json") or {}).get("active") or {}
    rlo, rhi = rs.get("lo"), rs.get("hi")
    if rlo and px < rlo: sc -= 1
    if rhi and px > rhi: sc += 1
    bias = "偏多" if sc > 0 else "偏空" if sc < 0 else ("偏多" if px > ma20 else "偏空")
    DH, DL = pivots(d, 3)
    sup = [p for _, p in DL] + [p for _, p in L4[-6:]] + [r["ma50w"]] + [rlo] * bool(rlo)
    res = [p for _, p in DH] + [p for _, p in H4[-6:]] + [rhi] * bool(rhi) + [rlo] * bool(rlo)
    down = nearest(sup, px, False); upl = nearest(res, px, True)
    rn = lambda a: round(a / 1000) * 1000
    if bias == "偏空":
        tg = down[:2] or [rn(px * 0.97)]
        inv = (rlo if rlo and rlo > px else (upl[0] if upl else rn(px * 1.02)))
        nxt = [x for x in upl if x > inv * 1.008][:1] or [rn(inv * 1.02)]
        fail = f"4H 收盤站回 {n0(inv)} 視為假跌破，轉看 {n0(nxt[0])}"
    else:
        tg = upl[:2] or [rn(px * 1.03)]
        inv = down[0] if down else rn(px * 0.98)
        nxt = [x for x in down if x < inv * 0.992][:1] or [rn(inv * 0.98)]
        fail = f"4H 收盤跌破 {n0(inv)} 視為假突破，轉看 {n0(nxt[0])}"
    return dict(bias=bias, score=sc, targets=tg, fail=fail, ma20=ma20)
def rvw_line():
    import subprocess
    try:
        o = subprocess.run([sys.executable, os.path.join(HERE, "ls_section.py"), "--rvw"], cwd=HERE, capture_output=True, text=True, timeout=40).stdout
        m = re.search(r"巨鯨散戶差：([^\n]+)", o)
        if not m: return None
        v = [float(x) for x in re.findall(r"([+-]?\d+\.\d+)", m.group(1))]
        if not v: return None
        a = sum(v) / len(v)
        t = "大戶明顯比散戶偏空，支持偏空傾向" if a <= -0.5 else "大戶比散戶偏多，支持偏多傾向" if a >= 0.3 else "大戶散戶分歧不大"
        return f"・大戶散戶：巨鯨散戶差 {m.group(1).strip()}｜{t}"
    except Exception:
        return None
def section(price=None):
    r = analyse(price); px = r["price"]
    w = _CACHE["w"]; d = _CACHE["d"]; h4 = _CACHE["h4"]
    L = [TITLE, "大趨勢"]
    try:
        bt = big_trend(r, w, d)
        L.append(f"・方向：{bt['dirn']}（評分 {bt['score']:+d}／{bt['n']} 項）")
        L.append("・依據：" + "、".join(t for _, _, t in bt["items"]))
        fl = bt["floor"]
        if fl:
            L.append(f"・多空分界：{n0(fl[0])}" + (f"–{n0(fl[-1])}" if len(fl) > 1 else "") + "（50 週均線／鏈上市場均價），守住維持多方大結構，跌破轉為偏空；上方要站回今年開盤價才算回到強勢")
    except Exception as e:
        print("big trend skipped:", e, file=sys.stderr); bt = None
    L.append("短線")
    try:
        sb = short_bias(r, d, h4)
        L.append(f"・傾向：{sb['bias']}｜下一目標 {'、'.join(n0(x) for x in sb['targets'])}｜失效：{sb['fail']}")
    except Exception as e:
        print("short bias skipped:", e, file=sys.stderr)
    rv = rvw_line()
    if rv: L.append(rv)
    L += short_lines(r)
    L.append("・規則樣本小：實單 15 筆，各型態 1–4 筆；逆勢空單止損命中率高，已降權")
    return "\n".join(L), r
# ---------------- 走勢預測 (forecast_text, Daniel 2026-10-24 16:58) ----------------
def _num(pat, txt):
    m = re.search(pat, txt or "")
    return float(m.group(1).replace(",", "")) if m else None
def onchain_levels():
    oc = _rd("out/repo_state/onchain_latest.json") or {}
    t = oc.get("text", ""); cur = ((oc.get("cryptochan") or {}).get("current")) or {}
    lv = dict(tmm=cur.get("tmm") or _num(r"True Market Mean (\d{2,3},\d{3})(?![\d.])", t), sth=cur.get("sthrp") or _num(r"STH 成本 (\d{2,3},\d{3})(?![\d.])", t),
              hot=_num(r"偏熱線 (\d{2,3},\d{3})(?![\d.])", t), over=_num(r"過熱線 (\d{2,3},\d{3})(?![\d.])", t), cool=_num(r"冷卻線 (\d{2,3},\d{3})(?![\d.])", t),
              oversold=_num(r"超賣線 (\d{2,3},\d{3})(?![\d.])", t), cyc_low=_num(r"週期低點 (\d{2,3},\d{3})(?![\d.])", t), lth=_num(r"LTH 成本 (\d{2,3},\d{3})(?![\d.])", t),
              sth_mvrv=_num(r"STH MVRV｜([\d.]+)", t), lth_z=_num(r"MVRV LTH Z｜([+-]?[\d.]+)", t), nupl=_num(r"NUPL｜([\d.]+)", t))
    m = re.search(r"這輪低點是 (\d{4}/\d+/\d+) 的 ([\d,]+)", t); lv["low_date"] = m.group(1) if m else None
    m = re.search(r"週期位置：([^，。\n]+)", t); lv["cycle"] = m.group(1) if m else None
    lv["sth30"] = "這輪 " in t and "已經做到" in t
    m = re.search(r"從 (\d+/\d+) 起價格都在其上，約 (\d+) 週", t); lv["tmm_weeks"] = m.group(2) if m else None
    sig = None
    if cur: sig = sum([cur.get("ratio", 9) < cur.get("ratio60", 0), cur.get("sthrp", 0) > cur.get("ma200", 9e9), cur.get("rev30", 0) > cur.get("rev365", 9e9)])
    lv["sig"] = sig
    return lv
def zones(cands, px, below):
    """Cluster labelled levels (within 1.2%) on one side of price, nearest first."""
    c = sorted([(p, n) for p, n in cands if p and ((p < px * 0.995) if below else (p > px * 1.005))], key=lambda x: -x[0] if below else x[0])
    out = []
    for p, n in c:
        if out and abs(p - out[-1]["ref"]) / out[-1]["ref"] <= 0.012:
            out[-1]["lv"].append(p); out[-1]["names"].append(n)
        else: out.append(dict(ref=p, lv=[p], names=[n]))
    for z in out: z["lo"], z["hi"] = min(z["lv"]), max(z["lv"])
    return out
def zs(z): return n0(z["lo"]) if z["hi"] - z["lo"] < 50 else f"{n0(z['lo'])}–{n0(z['hi'])}"
# 長期推測參數（人工維護，來源：交易員公開觀點與 Daniel 提供的長期路徑圖）
LONG_VIEW = dict(low=57700, dd_min=0.10, dd_max=0.15, ath=124672, ath_window="2027 年 4 月底至 8 月初",
                 top="160–180K（長期上升趨勢線）", next_bottom="100K 以上",
                 path="90–100K 區間震盪 → 113K 附近突破後回踩 → 沿長期上升趨勢線上看 160–180K")

# Daniel 偏好分析師觀點（人工維護；鋒哥 10/16、10/24 影片 BTC 走勢段落，@KillaXBT 10/4 推文）
FAV_VIEWS = {
    "short": [("鋒哥", "80K 是短線關鍵，失守先看 77.7–76.3K；美國期中選舉（約 11/3）前偏橫盤震盪")],
    "mid":   [("鋒哥", "從 80K 看下跌空間不到 10%，極限 75–77K（50 週均線、週線 Vegas 通道）；跌回 70K 以下轉弱")],
    "long":  [("鋒哥", "57.7K 為本輪低點，目標回前高約 130K；失效：跌回 70K，再破 60K 則反轉失敗"),
              ("@KillaXBT", "2027 年 4 月底至 8 月初回前高，本輪週期可能只有約兩年")],
}
def _tf(seg):
    """Timeframe of an analyst level: short (hours–days) / mid (weeks) / long (cycle). Timeframe cues first, then price distance (>=100K)."""
    if re.search(r"數日|日內|4H|1H|日收|日線|5日", seg): return "short"
    if re.search(r"本輪|下輪|週期|[0-9]+ ?年|202[7-9]|兆|中位數", seg) or re.search(r"(?<![\d.,])1[0-9]{2}(?:\.\d)?K|(?<![\d,])1[0-9]{2},\d{3}", seg): return "long"
    if re.search(r"週|月|區間|HTF|[Ss]top [Hh]unt", seg): return "mid"
    return "short"
def _quiet(a, days=3):
    """No BTC view in ~3 days -> 近期未談 BTC."""
    if a.get("stance") == "未談BTC" or re.search(r"未談\s*BTC", a.get("reason", "")): return True
    try:
        import datetime as _d
        return (_d.date.today() - _d.date.fromisoformat(a.get("latest_post", "")[:10])).days > days
    except Exception: return False
def kol_layers():
    """Tracked analysts' levels from kol/latest_view.json split into short/mid/long (keyword rule)."""
    out = {"short": [], "mid": [], "long": []}
    try:
        d = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kol/latest_view.json")))
    except Exception:
        return out
    skip = {"traderfengge"}   # 鋒哥 已在 FAV_VIEWS
    for a in d.get("accounts", []):
        lv = (a.get("levels") or "").strip()
        if _quiet(a) or a["handle"] in skip or lv in ("", "-"): continue
        for seg in [x.strip() for x in lv.split("｜") if x.strip()]:
            k = _tf(seg)
            out[k].append((a["handle"], a.get("stance", ""), seg))
    return out
def views_block(layer, kl, n=3):
    L = [f"・{who}：{t}" for who, t in FAV_VIEWS.get(layer, [])]
    seen = {}
    for h, st, seg in kl.get(layer, []):
        seen.setdefault(h, (st, []))[1].append(seg)
    for h, (st, segs) in list(seen.items())[:n]:
        L.append(f"・@{h}（{st}）：{'；'.join(segs[:2])}")
    return ["・分析師觀點："] + ["  " + x for x in L] if L else []
def forecast(price=None):
    r = analyse(price); px = r["price"]; w, d, h4 = _CACHE["w"], _CACHE["d"], _CACHE["h4"]
    lv = onchain_levels(); kl = kol_layers()
    import datetime as dt
    tw = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    L = ["BTC 走勢預測", f"{tw.strftime('%Y.%m.%d %H:%M')} (UTC+8)｜BTC {n0(px)}"]
    bt = None
    try: bt = big_trend(r, w, d)
    except Exception as e: print("big trend skipped:", e, file=sys.stderr)
    # ---- 短線
    L += ["", "──────────", "【短線】數小時到數天"]
    try:
        sb = short_bias(r, d, h4)
        L.append(f"・傾向：{sb['bias']}｜下一目標 {'、'.join(n0(x) for x in sb['targets'])}")
        L.append(f"・失效：{sb['fail']}")
    except Exception as e: print("short bias skipped:", e, file=sys.stderr)
    rv = rvw_line()
    if rv: L.append(rv)
    L += short_lines(r)
    L += views_block("short", kl)
    # ---- 中期
    L += ["", "──────────", "【中期】數週：掃單與流動性價位"]
    DH, DL = pivots(d, 3)
    rs = (_rd("out/range_state.json") or {}).get("active") or {}
    dn = [(p, "前低") for _, p in DL[-8:]] + [(r["ma50w"], "50 週均線"), (lv["tmm"], "鏈上市場均價"), (lv["sth"], "短期持有者成本"), (lv["cool"], "鏈上冷卻線")]
    dn += [(k * 1000.0, "整數關口") for k in range(int(px // 5000) * 5, int(px // 5000) * 5 - 16, -5)]
    up = [(p, "前高") for _, p in DH[-8:]] + [(rs.get("lo"), "盤整區下緣"), (rs.get("hi"), "盤整區上緣"), (lv["hot"], "鏈上偏熱線")]
    try:
        o = [x for x in d if dt.datetime.fromtimestamp(x[0] / 1000, dt.timezone.utc).strftime("%m-%d") == "01-01" and dt.datetime.fromtimestamp(x[0] / 1000, dt.timezone.utc).year == tw.year]
        if o: up.append((o[0][1], "今年開盤價"))
    except Exception: pass
    up += [(k * 1000.0, "整數關口") for k in range(int(px // 5000) * 5 + 5, int(px // 5000) * 5 + 16, 5)]
    zda = [z for z in zones(dn, px, True) if z["ref"] > px * 0.8]; zd = zda[:3]
    zu = [z for z in zones(up, px, False) if z["ref"] < px * 1.2][:3]
    for i, z in enumerate(zd):
        back = zd[i - 1] if i > 0 else (zu[0] if zu else None)
        nxt = zda[i + 1] if i + 1 < len(zda) else None
        L.append(f"・下方掃單 {zs(z)}（{'、'.join(dict.fromkeys(z['names']))}）：快速跌破後 4H 收回，視為掃完停損，回看 {zs(back) if back else '上方壓力'}；日線收在下方則續看 {zs(nxt) if nxt else '更低支撐'}")
    for i, z in enumerate(zu[:2]):
        nxt = zu[i + 1] if i + 1 < len(zu) else None
        L.append(f"・上方掃單 {zs(z)}（{'、'.join(dict.fromkeys(z['names']))}）：插針站上後收回，視為掃空單後回落；日線站穩則看 {zs(nxt) if nxt else '更高壓力'}")
    if zd and zu:
        dirn = "先向下掃流動性再回升" if r["price"] < (rs.get("lo") or 0) else "區間內來回掃單"
        L.insert(L.index("【中期】數週：掃單與流動性價位") + 1, f"・方向：{dirn}｜目標 {'、'.join(zs(z) for z in zd[:2])}｜失效：日線站回 {zs(zu[0])}，改看 {zs(zu[1]) if len(zu) > 1 else '上方前高'}")
    L.append("・清算密集區：無可靠免費資料，未列入")
    L += views_block("mid", kl)
    # ---- 長期
    L += ["", "──────────", "【長期】週期"]
    if bt:
        L.append(f"・大趨勢：{bt['dirn']}（評分 {bt['score']:+d}／{bt['n']} 項）")
        L.append("・依據：" + "、".join(t for _, _, t in bt["items"]))
    WH, WL = pivots(w, 2)
    wk_up = len(WH) >= 2 and len(WL) >= 2 and WL[-1][1] > WL[-2][1]
    ev = []
    if lv["cyc_low"]: ev.append(f"週期低點 {n0(lv['cyc_low'])}（{lv['low_date'] or '?'}）後沒有再破")
    if lv["sth"] and px > lv["sth"]: ev.append("價格在短期持有者成本之上" + ("並守住 30 天" if lv["sth30"] else ""))
    if lv["tmm"] and px > lv["tmm"]: ev.append("在鏈上市場均價之上" + (f"約 {lv['tmm_weeks']} 週" if lv["tmm_weeks"] else ""))
    if wk_up: ev.append("週線低點墊高")
    if lv["sig"] is not None: ev.append(f"鏈上訊號 {lv['sig']}/3 偏多")
    con = []
    if lv["lth_z"] is not None: con.append(f"長期持有者 MVRV Z {lv['lth_z']:+.2f} 仍低於長期均值")
    if lv["nupl"] is not None: con.append(f"NUPL {lv['nupl']:.2f}，這輪底部比前三輪淺很多")
    k = len(ev) - (0 if (lv["sth"] and px > lv["sth"]) else 2)
    verdict = "熊市低點大概率已經出現" if k >= 4 else "熊市低點可能已出現，但還不能確認" if k >= 2 else "熊市低點尚未確認"
    L.append(f"・判斷：{verdict}" + (f"，低點 {n0(lv['cyc_low'])}（{lv['low_date']}）" if lv["cyc_low"] else "") + (f"；鏈上週期為{lv['cycle']}" if lv["cycle"] else ""))
    if ev: L.append("・支持：" + "、".join(ev))
    if con: L.append("・疑點：" + "、".join(con))
    flo = [x for x in (lv["sth"], lv["tmm"], r["ma50w"]) if x]
    if flo:
        a, b = min(flo), max(flo)
        tg = "、".join(n0(x) for x in sorted(x for x in (lv["hot"], next((p for p, n in up if n == "今年開盤價"), None), lv["over"]) if x and x > px))
        L.append(f"・路徑一：回測 {n0(a)}–{n0(b)}（短期持有者成本／鏈上市場均價／50 週均線）守住，築底後再上" + (f"，目標 {tg}" if tg else ""))
        dd = "、".join(n0(x) for x in (lv["cool"], lv["oversold"], lv["cyc_low"]) if x)
        L.append(f"・路徑二：週線收盤跌破 {n0(a)}，大結構轉弱，二次探底" + (f"，依序看 {dd}" if dd else ""))
    LT = LONG_VIEW
    dlo, dhi = px * (1 - LT["dd_max"]), px * (1 - LT["dd_min"])
    L += ["", "──────────", "【長期大方向推測】"]
    L.append(f"・方向：偏多，本輪熊市低點大概率已出現｜目標 前高 {n0(LT['ath'])}，再上 {LT['top']}｜失效：跌回 70K 以下（再破 60K 則反轉失敗）")
    L.append(f"・本輪低點約 {LT['low'] / 1000:.1f}K；自 6 萬起算最大再下探 -10~15%（約 53–54K），此區已大概率不會到")
    L.append("・從 8 萬看下跌空間不到 10%，極限 75–77K")
    L.append(f"・@KillaXBT 猜測路徑：若週期縮短，{LT["ath_window"]}回測前高 {n0(LT['ath'])}")
    L.append(f"・路徑：{LT['path']}")
    L.append(f"・下個週期底預估在 {LT['next_bottom']}")
    L += views_block("long", kl)
    return "\n".join(L)
def _kv(t):
    """80.5K -> 80,500 style kept short: leave K notation (compact message)."""
    return (t or "").strip()
def _kol_digest():
    try:
        d = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kol/latest_view.json")))
    except Exception: d = {}
    acc = [a for a in d.get("accounts", []) if not _quiet(a)]
    cnt = {k: sum(a.get("stance") == k for a in acc) for k in ("偏多", "中性", "偏空")}
    lv = {}
    for part in (d.get("common_levels") or "").split("｜"):
        m = re.match(r"\s*(支撐|壓力|上方目標|下方目標)\s*(.+)", part)
        if m: lv[m.group(1)] = [x.strip() for x in m.group(2).split("、") if x.strip()]
    div = [x.strip().rstrip("。") for x in re.split(r"[；;]", d.get("divergence") or "") if x.strip()]
    return cnt, lv, div
def _fav(layer, who="鋒哥"):
    return next((t for w, t in FAV_VIEWS.get(layer, []) if w == who), "")
def forecast_compact(price=None):
    """Compact synthesis of our model + tracked analysts (Daniel approved 2026-10-24 19:42). Synthesis lines auto-derived from
    kol/latest_view.json (stance counts, common levels, divergence) + FAV_VIEWS (鋒哥) + LONG_VIEW (@KillaXBT path)."""
    import datetime as dt
    r = analyse(price); px = r["price"]; w, d, h4 = _CACHE["w"], _CACHE["d"], _CACHE["h4"]
    lv = onchain_levels()
    tw = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    cnt, kl, div = _kol_digest()
    L = ["BTC 走勢預測", f"{tw.strftime('%Y.%m.%d %H:%M')} (UTC+8)｜BTC {n0(px)}"]
    # 短線
    sb = short_bias(r, d, h4)
    inv = re.search(r"([\d,]{5,})", sb["fail"]); inv = inv.group(1) if inv else ""
    L += ["", "【短線】數天"]
    L.append(f"方向：{sb['bias']}｜目標 {'、'.join(n0(x) for x in sb['targets'])}｜失效：4H 收回 {inv}")
    s1 = [f"模型{sb['bias']}"]
    if _fav("short"): s1.append("鋒哥：" + _fav("short"))
    s1.append(f"分析師 {cnt['偏多']} 偏多、{cnt['中性']} 中性、{cnt['偏空']} 偏空" + (f"，共同支撐 {'、'.join(kl['支撐'][:3])}" if kl.get("支撐") else ""))
    if div: s1.append("分歧：" + div[-1])
    L.append("；".join(s1) + "。")
    # 中期
    DH, DL = pivots(d, 3)
    dn = [(p, "") for _, p in DL[-8:]] + [(r["ma50w"], ""), (lv["tmm"], ""), (lv["sth"], "")]
    dn += [(k * 1000.0, "") for k in range(int(px // 5000) * 5, int(px // 5000) * 5 - 16, -5)]
    up = [(p, "") for _, p in DH[-8:]] + [(lv["hot"], "")] + [(k * 1000.0, "") for k in range(int(px // 5000) * 5 + 5, int(px // 5000) * 5 + 16, 5)]
    zd = [z for z in zones(dn, px, True) if z["ref"] > px * 0.8][:3]
    zu = [z for z in zones(up, px, False) if z["ref"] < px * 1.2][:2]
    L += ["", "【中期】數週"]
    L.append(f"方向：先掃下方流動性再回升｜目標 {'、'.join(zs(z) for z in zd)}｜失效：日線收在 {zs(zd[-1]) if zd else '75K'} 下方")
    flo = [x for x in (lv["sth"], lv["tmm"], r["ma50w"]) if x]
    s2 = []
    if flo: s2.append(f"{n0(min(flo))}–{n0(max(flo))} 是 50 週均線、短期持有者成本與鏈上市場均價的交會區")
    if _fav("mid"): s2.append("鋒哥：" + _fav("mid"))
    s2.append(f"上方 {zs(zu[0]) if zu else '前高'} 站回才轉強" + (f"，分析師共同壓力 {'、'.join(kl['壓力'][-2:])}" if kl.get("壓力") else "") + (f"、上方目標 {'、'.join(kl['上方目標'][:2])}" if kl.get("上方目標") else ""))
    L.append("；".join(s2) + "。")
    # 長期
    LT = LONG_VIEW
    L += ["", "【長期】週期"]
    L.append(f"方向：偏多，熊市低點大概率已出現（{n0(lv['cyc_low']) if lv.get('cyc_low') else n0(LT['low'])}）｜目標 前高 {n0(LT['ath'])}，再上 {LT['top'].split('（')[0]}｜失效：跌回 70K 以下，再破 60K 反轉失敗")
    s3 = []
    if lv.get("cycle"): s3.append(f"鏈上週期為{lv['cycle']}")
    if _fav("long"): s3.append("鋒哥：" + _fav("long"))
    s3.append(f"@KillaXBT 猜測路徑：{LT['ath_window']}回前高，下個週期底在 {LT['next_bottom']}")
    L.append("；".join(s3) + "。")
    return "\n".join(L)
if __name__ == "__main__":
    try:
        p = None
        if "--price" in sys.argv: p = float(sys.argv[sys.argv.index("--price") + 1])
        if "--forecast-compact" in sys.argv:
            t = forecast_compact(p); print(t)
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out/publish/forecast_compact_draft.txt"), "w") as fh: fh.write(t)
            sys.exit(0)
        if "--forecast" in sys.argv: print(forecast(p)); sys.exit(0)
        s, r = section(p)
        print(json.dumps(r, ensure_ascii=False) if "--json" in sys.argv else s)
    except Exception as e:
        print("fengge skipped:", e, file=sys.stderr)

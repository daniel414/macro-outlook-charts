"""【短線交易模型】 for /btc: mechanical short-term price-action model
(/workspace/big-players/fengge/full_model.md, approved by Daniel 2026-10-24). Reference block only; does not replace the main model.
Rules: 1) trend filter = price vs 50-week MA + daily swing structure (HH/HL or LH/LL); only the trend direction is labelled.
2) entry A = 0.5-0.618 retracement zone + 1H/4H reversal (false break reclaimed, double-bottom/top neckline retest);
   entry B = breakout then retest holds. 3) stop beyond the prior swing, T1 prior high/low, T2 fib extension 1.382-1.618.
4) half off at T1, rest stop to entry. 5) counter-trend shorts down-weighted.
Free public data: OKX BTC-USDT candles (1Wutc/1Dutc/4H/1H). Stdlib only.
Usage: fengge_section.py [--price 80780] [--json]  -> prints the section, or nothing on failure (exit 0)."""
import json, sys, urllib.request
TITLE = "【短線交易模型】"
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
    d, dl = bars("1Dutc", 180)
    h4, h4l = bars("4H", 120)
    h1, h1l = bars("1H", 72)
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
def section(price=None):
    r = analyse(price); px = r["price"]
    pos = "上方" if px > r["ma50w"] else "下方"
    if r["side"] == "long": tr = "順勢方向：多（只看低多）"
    elif r["side"] == "short": tr = "順勢方向：空（只看高空）"
    else: tr = "方向不一致：不做（只做順勢，放掉勝率低的交易）"
    L = [TITLE, f"・趨勢：BTC {n0(px)} 在 50 週均線 {n0(r['ma50w'])} {pos}｜日線 {r['daily']}｜{tr}"]
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
    L.append("・規則樣本小：實單 15 筆，各型態 1–4 筆；逆勢空單止損命中率高，已降權")
    return "\n".join(L), r
if __name__ == "__main__":
    try:
        p = None
        if "--price" in sys.argv: p = float(sys.argv[sys.argv.index("--price") + 1])
        s, r = section(p)
        print(json.dumps(r, ensure_ascii=False) if "--json" in sys.argv else s)
    except Exception as e:
        print("fengge skipped:", e, file=sys.stderr)

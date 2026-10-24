#!/usr/bin/env python3
"""@0xcryptochan 鏈上訊號（免費資料重建版）。資料：combined_daily.csv（CheckOnChain）、blockchain.info 礦工收入/價格。
Usage: cryptochan_signals.py [--backtest] [--json]   不 import publish_btc.py。"""
import json, os, sys, argparse
import numpy as np, pandas as pd, requests
HERE = os.path.dirname(os.path.abspath(__file__)); CC = os.path.join(HERE, "cryptochan")
def bc(name):
    os.makedirs(CC, exist_ok=True)
    f = os.path.join(CC, f"bc_{name}.json")
    try:
        r = requests.get(f"https://api.blockchain.info/charts/{name}?timespan=all&format=json&sampled=false", timeout=60)
        if r.status_code == 200 and len(r.content) > 10000: open(f, "wb").write(r.content)
    except Exception as e: print("bc fetch", name, e, file=sys.stderr)
    v = json.load(open(f))["values"]
    s = pd.Series({pd.Timestamp(x["x"], unit="s").normalize(): x["y"] for x in v}).sort_index()
    return s[~s.index.duplicated(keep="last")]
def load():
    D = pd.read_csv(os.path.join(HERE, "combined_daily.csv"), index_col=0, parse_dates=True)
    mp = bc("market-price"); mp = mp[mp > 0]; P = D["P"].combine_first(mp).dropna()
    idx = pd.date_range(P.index[0], D.index[-1]); P = P.reindex(idx).ffill()
    rev = bc("miners-revenue").reindex(idx).interpolate(limit=3)
    X = pd.DataFrame({"P": P, "STHRP": D["STHRP"].reindex(idx), "LTHRP": D["LTHRP"].reindex(idx), "TMM": D["TMM"].reindex(idx),
                      "STHMVRV": D["STHMVRV"].reindex(idx), "LTHSOPR": D["LTHSOPR"].reindex(idx), "REV": rev})
    X["MA200"] = X.P.rolling(200).mean(); X["MA365"] = X.P.rolling(365).mean(); X["PI2"] = 2 * X.P.rolling(350).mean()
    X["REV30"] = X.REV.rolling(30).mean(); X["REV365"] = X.REV.rolling(365).mean()
    X["RATIO"] = X.LTHRP / X.STHRP; X["RATIO60"] = X.RATIO.rolling(60).mean()
    m = X.STHMVRV; w = 4 * 365; mu = m.rolling(w, min_periods=365).mean(); sd = m.rolling(w, min_periods=365).std()
    X["SZ"] = (m - mu) / sd; X["S_P1"] = mu + sd; X["S_M08"] = mu - 0.8 * sd
    X["SOPR7"] = X.LTHSOPR.rolling(7).mean()
    return X
# 訊號定義：(代碼, 名稱, 方向, 條件 Series(bool), 他的原始說法)
def signals(X):
    up = lambda a, b: (a > b) & (a.shift() <= b.shift())
    return [
        ("rev_cross", "礦工收入 30DMA 上穿 365DMA", "bull", up(X.REV30, X.REV365), "金叉後 22–44 天到牛市復甦頂（2026-09-28）"),
        ("rev_dead", "礦工收入 30DMA 下穿 365DMA", "bear", up(X.REV365, X.REV30), "小牛頂後死叉 1–2 天見底（2023-12-06）"),
        ("ma365_up", "價格上穿 365DMA（熊轉牛起步完結）", "bull", up(X.P, X.MA365), "起步階段結束後 122 天到復甦頂（2026-10-21）"),
        ("tmm_up", "價格上穿 True Market Mean", "bull", up(X.P, X.TMM), "突破後在 TMM 附近盤 6.5–6.8 個月再主升（2026-09-17）"),
        ("tmm_dn", "價格跌破 True Market Mean", "bear", up(X.TMM, X.P), "跌至 TMM = 泡沫清除，之後整理（2025-12-02）"),
        ("sth200", "STH 成本上穿 200DMA（紅黑交叉）", "bull", up(X.STHRP, X.MA200), "交叉後 47–50 天到復甦頂（2026-09-11）"),
        ("ratio_dn", "LTH/STH 成本比下穿 60DMA（藍橙交叉）", "bull", up(X.RATIO60, X.RATIO) & (X.RATIO > 0.6), "交叉後 69–87 天到復甦頂（2026-09-11）"),
        ("sth_p1", "STH MVRV 突破 4 年 +1σ", "bull", up(X.SZ, pd.Series(1.0, X.index)), "突破後先調整 20–27 天再衝復甦頂（2026-09-24）"),
        ("sth_m08", "STH MVRV 跌破 4 年 -0.8σ", "bull", up(pd.Series(-0.8, X.index), X.SZ), "-0.8σ/-1.6σ 為熊底區（2026-09-06 圖）"),
        ("pi2_up", "價格上穿 2×350DMA", "bear", up(X.P, X.PI2), "歷次小牛頂都突破此線（2023-12-14）"),
        ("sopr1", "LTH SOPR 7DMA 回踩 1（由上往下）", "bull", up(pd.Series(1.0, X.index), X.SOPR7), "主升前最後一跌 LTH SOPR 回踩 1（2026-09-26）"),
    ]
def events(cond, gap=60):
    out, last = [], None
    for t in cond[cond.fillna(False)].index:
        if last is None or (t - last).days > gap: out.append(t)
        last = t
    return out
def backtest(X):
    R = []; P = X.P; end = P.index[-1]
    for code, name, d, cond, claim in signals(X):
        ev = events(cond); rows = []
        for t in ev:
            r = {"date": str(t.date()), "price": round(float(P[t]), 2)}
            for h in (30, 90, 180):
                t2 = t + pd.Timedelta(days=h); r[f"r{h}"] = round(float(P[t2] / P[t] - 1) * 100, 1) if t2 <= end else None
            if t + pd.Timedelta(days=180) <= end:
                w = P[t:t + pd.Timedelta(days=180)]; r["max180"] = round(float(w.max() / P[t] - 1) * 100, 1); r["dmax"] = int((w.idxmax() - t).days)
                r["min180"] = round(float(w.min() / P[t] - 1) * 100, 1)
            rows.append(r)
        done = [r for r in rows if r["r90"] is not None]
        hit = [r for r in done if (r["r90"] > 0) == (d == "bull")]
        R.append({"code": code, "name": name, "dir": d, "claim": claim, "n": len(done), "hits": len(hit),
                  "acc90": round(len(hit) / len(done) * 100) if done else None,
                  "med90": float(np.median([r["r90"] for r in done])) if done else None,
                  "med_dmax": float(np.median([r["dmax"] for r in rows if "dmax" in r])) if any("dmax" in r for r in rows) else None,
                  "events": rows})
    return R
def current(X):
    v = X.dropna(subset=["P"]).iloc[-1]; t = X.index[-1]
    def since(cond):
        e = events(cond, 0); return (str(e[-1].date()), (t - e[-1]).days) if e else (None, None)
    S = {s[0]: since(s[3]) for s in signals(X)}
    L = lambda c: float(X[c].dropna().iloc[-1])
    return {"date": str(t.date()), "price": float(v.P), "rev30": L("REV30"), "rev365": L("REV365"), "ma365": L("MA365"), "ma200": L("MA200"),
            "tmm": L("TMM"), "sthrp": L("STHRP"), "ratio": L("RATIO"), "ratio60": L("RATIO60"), "sz": L("SZ"), "sth_p1_price": L("S_P1") * L("STHRP"),
            "pi2": L("PI2"), "sopr7": L("SOPR7"), "last": S}
def section(cur, bt, live):
    B = {r["code"]: r for r in bt}; c = cur; L = ["【cryptochan 鏈上訊號（免費資料重建）】"]
    def tag(code):
        r = B[code]; return f"（回測 {r['n']} 次，90 天方向對 {r['hits']} 次）"
    for code in live:
        d, n = c["last"][code]
        if code == "rev_cross":
            st = "金叉中" if c["rev30"] > c["rev365"] else "死叉中"
            L.append(f"・礦工收入 30DMA/365DMA：{c['rev30']/1e6:.1f}M／{c['rev365']/1e6:.1f}M 美元，{st}；最近金叉 {d}（{n} 天前）{tag(code)}")
        elif code == "ma365_up":
            L.append(f"・價格 vs 365DMA {c['ma365']:,.0f}：{'上方' if c['price'] > c['ma365'] else '下方'}（{(c['price']/c['ma365']-1)*100:+.1f}%）；最近上穿 {d}{tag(code)}")
        elif code == "tmm_up":
            L.append(f"・價格 vs True Market Mean {c['tmm']:,.0f}：{(c['price']/c['tmm']-1)*100:+.1f}%；最近上穿 {d}（{n} 天前）{tag(code)}")
        elif code == "sth200":
            L.append(f"・STH 成本 {c['sthrp']:,.0f} vs 200DMA {c['ma200']:,.0f}：{'上方' if c['sthrp'] > c['ma200'] else '下方'}；最近紅黑交叉 {d}{tag(code)}")
        elif code == "sth_p1":
            L.append(f"・STH MVRV 4 年 Z 值 {c['sz']:+.2f}（+1σ 對應價格約 {c['sth_p1_price']:,.0f}）；最近突破 +1σ {d}{tag(code)}")
        elif code == "pi2_up":
            L.append(f"・2×350DMA {c['pi2']:,.0f}：價格距此線 {(c['price']/c['pi2']-1)*100:+.1f}%（突破常見於階段頂）{tag(code)}")
        elif code == "ratio_dn":
            L.append(f"・LTH/STH 成本比 {c['ratio']:.3f} vs 60DMA {c['ratio60']:.3f}：{'在均線下（偏多）' if c['ratio'] < c['ratio60'] else '在均線上'}；最近藍橙交叉 {d}（{n} 天前）{tag(code)}")
        elif code == "sth_m08":
            L.append(f"・STH MVRV 4 年 Z 值 {c['sz']:+.2f}，-0.8σ 以下為他定義的熊底區；最近跌破 {d}{tag(code)}")
    L.append("・說明：BTC 任意一天往後 90 天上漲的基準機率約 58%；以上樣本每項僅 6–12 次，只作參考。")
    return L
LIVE = []   # 由回測結果決定，見 cryptochan/LIVE.json
def run(write=True):
    X = load(); bt = backtest(X); cur = current(X)
    lf = os.path.join(CC, "LIVE.json"); live = json.load(open(lf)) if os.path.exists(lf) else LIVE
    sec = section(cur, bt, live)
    out = {"current": cur, "live": live, "section": sec, "backtest": [{k: v for k, v in r.items() if k != "events"} for r in bt]}
    if write:
        json.dump({"current": cur, "backtest": bt}, open(os.path.join(CC, "backtest.json"), "w"), ensure_ascii=False, indent=1)
    return out
if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--backtest", action="store_true"); a = ap.parse_args()
    o = run()
    if a.backtest:
        for r in o["backtest"]: print(f"{r['code']:9s} {r['dir']} n={r['n']:2d} hit={r['hits']} acc90={r['acc90']} med90={r['med90']} medDmax={r['med_dmax']} | {r['name']}")
    print("\n".join(o["section"]))

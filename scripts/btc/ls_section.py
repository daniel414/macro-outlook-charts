#!/usr/bin/env python3
"""Print the 【多空比】 section (version B) from data/positioning2.db latest values. Run as a script.
Rows older than MAXAGE are shown as 無資料. Ratios: raw (多 x%), x = r/(1+r)."""
import os, sqlite3, time, unicodedata as u
from datetime import datetime
HERE = os.path.dirname(os.path.abspath(__file__))
MAXAGE = 3 * 3600
BN_FRESH = 2 * 3600
c = sqlite3.connect(f"file:{os.path.join(HERE, 'data/positioning2.db')}?mode=ro", uri=True, timeout=20)
now = time.time()
def g(src, m, tf):
    r = c.execute("select ts,value from series where src=? and metric=? and tf=? order by ts desc limit 1", (src, m, tf)).fetchone()
    return r if r and now - r[0] <= MAXAGE else None
def w(s): return sum(2 if u.east_asian_width(ch) in "WF" else 1 for ch in s)
def p(s, n): return s + " " * max(1, n - w(s))
def f(v): return "無資料" if v is None else f"{v:.2f}（多 {v/(1+v)*100:.0f}%）"
T = lambda ts: datetime.fromtimestamp(ts).strftime("%m/%d %H:%M")
rows = []; t1h = None
for s, n in [("okx", "OKX"), ("gate", "Gate"), ("bitget", "Bitget")]:
    v = [g(s, m, "1H") for m in ("ls_account", "top_ls_account", "top_ls_position")]
    for x in v:
        if x: t1h = max(t1h or 0, x[0])
    rows.append((n, *[x[1] if x else None for x in v]))
def bcol(col):   # newest non-null value per column (the newest 5m row is often only partly filled)
    return c.execute(f"select ts,{col} from binance where {col} is not null order by ts desc limit 1").fetchone()
bc = [bcol(k) for k in ("global_account", "top_account", "top_position")]
bn = bc[0] and (bc[0][0], *[x[1] if x and bc[0][0] - x[0] <= BN_FRESH else None for x in bc])
bfresh = bool(bn) and now - bn[0] <= BN_FRESH   # live www.binance.com rows; older = fell back to daily dumps
rows.append(("Binance" if bfresh or not bn else "Binance*", *(bn[1:] if bn else (None,) * 3)))
# order by latest BTC perp OI (USD), all must be <= MAXAGE, else fixed fallback order
FIXED = ["Binance", "OKX", "Bitget", "Gate"]
def oi(src):
    r = c.execute("select ts,value from series where src=? and metric='oi_usd' order by ts desc limit 1", (src,)).fetchone()
    return r if r and now - r[0] <= MAXAGE else None
bo = c.execute("select ts,coalesce(oi_usd, oi_btc*?) from binance where oi_usd is not null or oi_btc is not null order by ts desc limit 1",
               (float(os.environ.get("BTC_PX", "0")) or None,)).fetchone()
OI = {"OKX": oi("okx"), "Gate": oi("gate"), "Bitget": oi("bitget"),
      "Binance": bo if bo and bo[1] and now - bo[0] <= MAXAGE else None}
if os.environ.get("LS_TEST_DROP_OI"): OI[os.environ["LS_TEST_DROP_OI"]] = None
key = lambda r: r[0].rstrip("*")
if all(OI.values()): rows.sort(key=lambda r: -OI[key(r)][1]); ORDER = "oi"
else: rows.sort(key=lambda r: FIXED.index(key(r))); ORDER = "fixed"
if os.environ.get("LS_DEBUG"):
    import sys
    print("ORDER", ORDER, {k: (None if v is None else (T(v[0]), round(v[1] / 1e9, 3))) for k, v in OI.items()}, file=sys.stderr)
import sys
if "--rvw" in sys.argv:
    # lines for 【散戶 vs 大戶】: long%-short% = (r-1)/(r+1)*100, same OI order as 【多空比】
    d = lambda v: "無資料" if v is None else f"{(v - 1) / (v + 1) * 100:+.1f}%"
    def ln(lab, i):
        xs = [r for r in rows if r[i] is not None]
        if not xs: return None
        return lab + "｜".join(f"{r[0]} {d(r[i])}" for r in rows if r[i] is not None or key(r) == "Binance")
    out = [ln("・全體帳戶：", 1), ln("・大戶持倉：", 3), ln("・大戶帳戶：", 2)]
    gap = [f"{r[0]} {r[3] - r[1]:+.2f}" for r in rows if key(r) in ("Binance", "OKX") and r[1] is not None and r[3] is not None]
    if gap: out.append("・巨鯨散戶差：" + "｜".join(gap))
    print("\n".join(x for x in out if x)); sys.exit(0)
L = ["【多空比】" + (T(t1h) if t1h else ""), "OKX/Gate/Bitget 1H、Binance 5m"]
NM = {"OKX": "OKX　　", "Gate": "Gate　 ", "Bitget": "Bitget　", "Binance": "Binance ", "Binance*": "Binance*"}
def blk(title, i, note=None):
    out = ["", title] + ([note] if note else [])
    for r in rows: out.append(NM.get(r[0], r[0] + " ") + f(r[i]))
    return out
L += blk("散戶（全體帳戶）", 1)
L += blk("大戶帳戶（看人數）", 2, "・大戶中做多與做空的帳戶數比")
L += blk("大戶持倉（看金額）", 3, "・大戶多單與空單的部位量比")
L.append("")
if not bn: L.append("*Binance 無資料")
elif not bfresh: L.append(f"*Binance 截至 {T(bn[0])}（改用官方每日檔）")
L.append("大戶：Binance 保證金前 20%")
L.append("OKX 持倉前 5%；Gate/Bitget 未公開")
L.append(">1 偏多／<1 偏空")
print("\n".join(L))

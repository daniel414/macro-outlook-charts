#!/usr/bin/env python3
"""Print the 【多空比】 section (version B) from data/positioning2.db latest values. Run as a script.
Rows older than MAXAGE are shown as 無資料. Ratios: raw (多 x%), x = r/(1+r)."""
import os, sqlite3, time, unicodedata as u
from datetime import datetime
HERE = os.path.dirname(os.path.abspath(__file__))
MAXAGE = 3 * 3600
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
bn = c.execute("select ts,global_account,top_account,top_position from binance order by ts desc limit 1").fetchone()
rows.append(("Binance*", *(bn[1:] if bn else (None,) * 3)))
ha, hp = g("hyperliquid", "whale_ls_account", "snap"), g("hyperliquid", "whale_ls_position", "snap")
rows.append(("HL**", None, ha[1] if ha else None, hp[1] if hp else None))
L = ["【多空比】OKX/Gate/Bitget 1H" + (f"，{T(t1h)}" if t1h else ""), "散戶（全體帳戶）"]
for r in rows: L.append((p(r[0], 10) + f(r[1])).rstrip())
L += ["", "大戶", (p("", 10) + p("帳戶", 18) + "持倉")]
for r in rows: L.append((p(r[0], 10) + p(f(r[2]), 18) + f(r[3])).rstrip())
L.append(f"*Binance 延遲，截至 {T(bn[0])}" if bn else "*Binance 無資料")
hn, hl, hs = g("hyperliquid", "whale_short_n", "snap"), g("hyperliquid", "whale_long_n", "snap"), g("hyperliquid", "whale_short_btc", "snap")
hlb = g("hyperliquid", "whale_long_btc", "snap")
if hn and hl and hs and hlb:
    L.append(f"**HL 無官方多空比，以淨值前 100 大地址倉位替代（{int(hl[1]+hn[1])} 個有 BTC 倉位：多 {hlb[1]:,.0f}／空 {hs[1]:,.0f} BTC）")
else:
    L.append("**HL 無官方多空比，以淨值前 100 大地址倉位替代")
L.append(">1 偏多／<1 偏空")
print("\n".join(L))

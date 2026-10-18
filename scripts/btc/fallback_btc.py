#!/usr/bin/env python3
"""GitHub Actions FALLBACK for LINE /btc (btc/latest.json + btc_positioning chart).

Runs only when Bitcoin Analysis (Grok Bot, box) has not published btc/latest.json recently, so the bot stays the
primary publisher and nothing conflicts. It rebuilds the data the box collector keeps (data/positioning2.db,
data/sentiment.db positioning, data/macro.db) from the same free, keyless public APIs with the box's own scripts
(posdata.py, btcsent/positioning.py, positioning_grid.py: vendored unchanged), then writes the same files the bot writes.

AI-written parts (lead_text, the 散戶：/大戶：/解讀： sentences) are never invented: they are carried over from the last
bot publish for 3 hours (same validity window publish_btc.py uses for out/lead_text.txt), then dropped.

  fallback_btc.py --mode check      # print STALE/FRESH and exit 0 (writes stale=true|false to $GITHUB_OUTPUT)
  fallback_btc.py --mode dry-run    # build everything into out/fallback/, compare with the repo's current files, no writes
  fallback_btc.py --mode publish    # build and publish (re-checks staleness right before writing)
  --force                           # ignore staleness (testing)
"""
import argparse, base64, json, os, re, sqlite3, subprocess, sys, time, shutil
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TZ = ZoneInfo("Asia/Taipei")
BOT_MAX_AGE_MIN = int(os.environ.get("BOT_MAX_AGE_MIN", 80))      # bot publishes hourly at ~:05-:13 (UTC+8)
SELF_MAX_AGE_MIN = int(os.environ.get("SELF_MAX_AGE_MIN", 50))    # after our own publish: refresh hourly
AI_CARRY_H = 3
SOURCE = "github-actions-fallback"
OUT = os.path.join(HERE, "out", "fallback")


def log(*a): print(*a, file=sys.stderr, flush=True)


def repo_json(path):
    import publish_btc as P
    import requests
    if not os.environ.get("GH_CHARTS_TOKEN"):   # local testing on the box: use the logged-in gh CLI
        return json.loads(subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw+json", f"repos/{P.GHR}/contents/{path}"], capture_output=True, check=True).stdout)
    r = requests.get(P.API + path, headers={**P.H(), "Accept": "application/vnd.github.raw+json"}, timeout=60)
    r.raise_for_status(); return json.loads(r.content)


def parse_tp(s):  # "2026-10-17 22:08" in UTC+8
    return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def staleness(cur, now=None):
    now = now or datetime.now(TZ)
    age = (now - parse_tp(cur["date"])).total_seconds() / 60
    mine = cur.get("source") == SOURCE
    lim = SELF_MAX_AGE_MIN if mine else BOT_MAX_AGE_MIN
    return age >= lim, age, mine, lim


# ---------- data bootstrap (same sources and row formats as the box collector) ----------
def okx_recent(con, pages):
    """posdata.okx() limited to the pages the 4H/180-bar grid and the 1D whale value need (posdata.okx has only 1 or 40 pages)."""
    import posdata as PD
    eps = {"ls_account": "long-short-account-ratio-contract", "top_ls_account": "long-short-account-ratio-contract-top-trader",
           "top_ls_position": "long-short-position-ratio-contract-top-trader", "oi": "open-interest-history"}
    n = 0
    for m, ep in eps.items():
        for tf, np_ in pages.items():
            end = None
            for _ in range(np_):
                d = PD.get(f"{PD.OKX}{ep}?instId={PD.INST}&period={tf}&limit=100" + (f"&end={end}" if end else ""))["data"]
                if not d: break
                if m == "oi":
                    rows = [("okx", "oi_usd", tf, int(r[0]) // 1000, float(r[3])) for r in d] + [("okx", "oi_btc", tf, int(r[0]) // 1000, float(r[2])) for r in d]
                else:
                    rows = [("okx", m, tf, int(r[0]) // 1000, float(r[1])) for r in d]
                PD.put(con, rows); n += len(rows); end = d[-1][0]
                if len(d) < 100: break
                time.sleep(0.25)
    return n


def bootstrap():
    data = os.path.join(HERE, "data"); os.makedirs(data, exist_ok=True)
    for f in ("positioning2.db", "macro.db", "sentiment.db"):
        p = os.path.join(data, f)
        if os.path.exists(p): os.remove(p)
    shutil.copy(os.path.join(HERE, "seed", "positioning2.db"), os.path.join(data, "positioning2.db"))  # Kraken daily history (box copy)
    shutil.copy(os.path.join(HERE, "seed", "macro.db"), os.path.join(data, "macro.db"))                # FRED CBBTCUSD + blockchain.info (static history)
    import posdata as PD
    con = PD.db(); now = int(time.time()); fails = []
    jobs = [("okx", lambda: okx_recent(con, {"4H": 2, "1D": 1})),
            ("gate", lambda: PD.gate(con, True)),
            ("hyperliquid", lambda: PD.hyperliquid(con)),
            ("kraken", lambda: PD.kraken_daily(con)),
            ("binance", lambda: PD.binance(con, since=(datetime.now(ZoneInfo("UTC")).date() - timedelta(days=33)).isoformat())),
            ("bitfinex", lambda: PD.bitfinex(con, (now - 32 * 86400) * 1000))]
    for k, f in jobs:
        t = time.time()
        try: log(f"bootstrap {k}: {f()} rows in {time.time()-t:.1f}s")
        except Exception as e: fails.append(k); log(f"bootstrap {k} FAILED: {e!r}")
    from btcsent import positioning as BP, common as BC
    t = time.time()
    try: log(f"bootstrap okx funding/positioning: {BP.update(BC.connect())} rows in {time.time()-t:.1f}s")
    except Exception as e: fails.append("okx_funding"); log(f"bootstrap okx funding FAILED: {e!r}")
    return fails


# ---------- text ----------
def ai_lines(prev_summary):
    """AI sentences inside 【散戶 vs 大戶】 of the last bot summary: {'散戶': '散戶：…', '大戶': '大戶：…', '解讀': '解讀：…'}."""
    out = {}
    for ln in (prev_summary or "").splitlines():
        for k in ("散戶", "大戶", "解讀"):
            if ln.startswith(k + "：") and k not in out: out[k] = ln.strip()
    return out


def summary(j, when, ai):
    """Same layout as the bot's current /btc summary (【散戶 vs 大戶】 version, 2026-10-15 22:21 Daniel-approved).
    Deterministic lines come from publish_btc.py's own formatters; AI sentences only from `ai` (carried over) or omitted."""
    import publish_btc as P
    g = j.get; pct, f1, busd, DIV = P.pct, P.f1, P.busd, P.DIV
    px = f"BTC {g('btc'):,.0f}" if g("btc") else "BTC -"
    def sec(*lines): return ["", DIV, *lines]
    w = g("巨鯨散戶差_okx_4H")
    whale = f"・巨鯨散戶差 {f1(w, 2)}（數字越負，大戶比散戶越偏空）" if w is not None else "・巨鯨散戶差 -"
    L = ["BTC 持倉數據", f"{when.strftime('%Y.%m.%d %H:%M')} (UTC+8)｜{px}"]
    blk = ["【散戶 vs 大戶】", ai.get("散戶", "散戶"),
           f"・OKX 全體 {pct(g('多空差值_全部_okx'))}｜Gate {pct(g('多空差值_全部_gate'))}", "",
           ai.get("大戶", "大戶"),
           f"・大戶持倉：OKX {pct(g('多空差值_okx_大戶持倉'))}｜Gate {pct(g('多空差值_gate_大戶持倉'))}",
           f"・大戶帳戶：OKX {pct(g('多空差值_okx_大戶帳戶'))}｜Gate {pct(g('多空差值_gate_大戶帳戶'))}", whale]
    if ai.get("解讀"): blk += ["", ai["解讀"]]
    L += sec(*blk)
    L += sec("【未平倉量】", "・" + "｜".join([f"OKX {busd(g('未平倉_okx_usd'))}", f"Gate {busd(g('未平倉_gate_usd'))}", f"HL {busd(g('未平倉_hyperliquid_usd'))}"]))
    L += sec("【資金費率】", f"・每 8h：OKX {pct(g('資金費率_okx_8h_pct'),4)}｜Gate {pct(g('資金費率_gate_pct'),4)}",
             f"・每 1h：HL {pct(g('資金費率_hyperliquid_1h_pct'),4)}")
    L += sec("【Bitfinex 槓桿】", f"・{pct(g('多空差值_bitfinex_pct'),2)}｜多 {g('bitfinex_多_btc') or 0:,.0f}／空 {g('bitfinex_空_btc') or 0:,.0f} BTC")
    ah = g("ahr999")
    zone = "" if ah is None else ("｜抄底區（<0.45）" if ah < 0.45 else "｜定投區（0.45–1.2）" if ah <= 1.2 else "｜高於定投線（>1.2）")
    L += sec("【Ahr999】", f"・{f1(ah,2,False)}{zone}")
    if g("binance_多空差值_大戶持倉") is not None:
        L += sec(f"【Binance（延遲，截至 {P.bdate(g('binance_asof'))}）】",
                 f"・全部 {pct(g('binance_多空差值_全部'))}｜大戶持倉 {pct(g('binance_多空差值_大戶持倉'))}",
                 f"・巨鯨vs散戶 {f1(g('binance_巨鯨散戶差'),2)}")
    # objective structure section (btc_structure.py via publish_btc.add_structure); omitted on any failure
    return P.add_structure(P.EMOJI.sub("", "\n".join(L))[:4900])


def build_images(png, stamp):
    """Exactly publish_btc.main()'s JPEG/preview loop."""
    from PIL import Image
    os.makedirs(OUT, exist_ok=True)
    big = os.path.join(OUT, f"btc_positioning_{stamp}.jpg"); pre = big[:-4] + "_preview.jpg"
    im = Image.open(png).convert("RGB")
    if im.width > 4000: im.thumbnail((4000, 4000))
    q = 90
    while True:
        im.save(big, "JPEG", quality=q, optimize=True)
        if os.path.getsize(big) < 9.5e6 or q < 50: break
        q -= 5
    p = im.copy(); p.thumbnail((1024, 1024)); q = 85
    while True:
        p.save(pre, "JPEG", quality=q, optimize=True)
        if os.path.getsize(pre) < 0.95e6 or q < 40: break
        q -= 10
    return big, pre


def gh_out(**kv):
    f = os.environ.get("GITHUB_OUTPUT")
    if f:
        with open(f, "a") as fh:
            for k, v in kv.items(): fh.write(f"{k}={v}\n")


def main():
    a = argparse.ArgumentParser(); a.add_argument("--mode", choices=["check", "dry-run", "publish"], default="check"); a.add_argument("--force", action="store_true")
    a = a.parse_args()
    cur = repo_json("btc/latest.json")
    stale, age, mine, lim = staleness(cur)
    log(f"btc/latest.json date={cur['date']} source={cur.get('source', 'bot')} age={age:.0f}min limit={lim}min -> {'STALE' if stale else 'FRESH'}")
    gh_out(stale=str(stale).lower())
    if a.mode == "check": print("STALE" if stale else "FRESH"); return
    if not stale and not a.force and a.mode == "publish": log("bot is up to date; nothing to do"); return

    fails = bootstrap()
    out = subprocess.run([sys.executable, os.path.join(HERE, "positioning_grid.py")], cwd=HERE, capture_output=True, text=True, timeout=600)
    j, png = {}, None
    for line in out.stdout.splitlines():
        if line.startswith("POSGRID_JSON "): j = json.loads(line[13:])
        elif line.strip().endswith(".png"): png = line.strip()
    if not j or not png: log(out.stderr[-3000:]); sys.exit("positioning_grid.py failed")
    log("grid fails:", j.get("fails"), "bootstrap fails:", fails)
    if fails and any(k in fails for k in ("okx", "gate")): sys.exit(f"core sources failed: {fails} (not publishing)")
    with open(os.path.join(HERE, "out", "posgrid_now.json"), "w") as fh: json.dump(j, fh, ensure_ascii=False)

    mt = re.search(r"(\d{8})_(\d{4,6})", os.path.basename(png))
    when = datetime.strptime(mt.group(1) + mt.group(2)[:4], "%Y%m%d%H%M")
    stamp = when.strftime("%Y%m%d_%H%M")
    # AI text carry-over window, anchored at the last BOT publish
    ai_from = cur.get("ai_text_from") if mine else cur["date"]
    ai_ok = bool(ai_from) and (datetime.now(TZ) - parse_tp(ai_from)) < timedelta(hours=AI_CARRY_H)
    ai = ai_lines(cur.get("summary_text")) if ai_ok else {}
    lead = (cur.get("lead_text") or "") if ai_ok else ""
    text = summary(j, when, ai)
    big, pre = build_images(png, stamp)
    log("built", big, os.path.getsize(big), pre, os.path.getsize(pre), "ai_carry", ai_ok, sorted(ai))
    with open(os.path.join(OUT, "summary_text.txt"), "w") as fh: fh.write(text)
    print(text)
    if a.mode == "dry-run":
        with open(os.path.join(OUT, "repo_summary_text.txt"), "w") as fh: fh.write(cur.get("summary_text", ""))
        with open(os.path.join(OUT, "posgrid.json"), "w") as fh: json.dump(j, fh, ensure_ascii=False, indent=1)
        import difflib
        old = cur.get("summary_text", "")
        log("DRY-RUN summary_text vs repo:", "IDENTICAL" if old == text else "DIFF (expected: time/price line + values that moved since the bot's run)")
        log("\n".join(difflib.unified_diff(old.splitlines(), text.splitlines(), "repo", "fallback", lineterm="", n=0)))
        return
    # re-check right before writing: the bot may have published while we were building
    cur2 = repo_json("btc/latest.json")
    if cur2.get("date") != cur.get("date") and not a.force: log("bot published meanwhile (", cur2.get("date"), "); abort without writing"); return
    import publish_btc as P
    n1 = "btc/" + os.path.basename(big); n2 = "btc/" + os.path.basename(pre)
    u1 = P.pinned(P.gh_put(n1, open(big, "rb").read(), "btc chart " + stamp + " (actions fallback)"), n1)
    u2 = P.pinned(P.gh_put(n2, open(pre, "rb").read(), "btc preview " + stamp + " (actions fallback)"), n2)
    cur3 = repo_json("btc/latest.json")
    if cur3.get("date") != cur.get("date") and not a.force: log("bot published meanwhile; keep the bot's latest.json (images stay, pruned later)"); return
    d = {"date": when.strftime("%Y-%m-%d %H:%M"), "summary_text": text, "change_text": "", "lead_text": lead[:4900], "image_url": u1, "preview_url": u2,
         "source": SOURCE, "ai_text_from": ai_from if ai_ok else None}
    P.gh_put("btc/latest.json", json.dumps(d, ensure_ascii=False, indent=1).encode(), "btc latest " + stamp + " (actions fallback)")
    log("PUBLISHED", u1)
    try: P.prune_one("btc_positioning_")
    except Exception as e: log("prune fail", e)


if __name__ == "__main__": main()

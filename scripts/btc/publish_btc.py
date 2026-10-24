#!/usr/bin/env python3
"""Publish BTC positioning overview + summary to GitHub (macro-outlook-charts/btc/) for the LINE /BTC command.
Usage:
  publish_btc.py                 # run positioning_grid.py now, auto summary, upload
  publish_btc.py --image X.png --json posgrid.json [--text summary.txt]
  publish_btc.py --dry-run       # build files, no upload
Never pushes LINE messages; the Worker only replies."""
import argparse, base64, glob, json, os, re, subprocess, sys, time
from datetime import datetime
import requests
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__))
GHR = "daniel414/macro-outlook-charts"
src = open("/workspace/big-players/line_push.py").read()
m = re.search(r'GHR="([^"]+)"', src); GHR = m.group(1) if m else GHR
KEEP = 120  # ~1 week (16 runs/day)
def H(): return {"Authorization": "Bearer " + os.environ["GH_CHARTS_TOKEN"], "Accept": "application/vnd.github+json"}
API = f"https://api.github.com/repos/{GHR}/contents/"
def gh_put(path, data, msg):
    g = requests.get(API + path, headers=H(), timeout=60)
    body = {"message": msg, "content": base64.b64encode(data).decode(), "branch": "main"}
    if g.status_code == 200: body["sha"] = g.json()["sha"]
    elif g.status_code != 404: raise Exception(f"gh get {g.status_code}")
    for i in range(3):
        r = requests.put(API + path, headers=H(), json=body, timeout=120)
        if r.status_code in (200, 201): return r.json()["commit"]["sha"]
        time.sleep(3)
    raise Exception(f"gh put {r.status_code} {r.text[:150]}")
def check(u, ct="image/jpeg"):
    r = requests.get(u, timeout=60); ok = r.status_code == 200 and r.headers.get("content-type", "").startswith(ct)
    print("check", u, r.status_code, r.headers.get("content-type"), len(r.content), file=sys.stderr); return ok
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
def prune():
    for pre in ("btc_positioning_", "btc_kol_"): prune_one(pre)
def prune_one(pre):
    r = requests.get(API + "btc", headers=H(), timeout=60)
    if r.status_code != 200: return
    imgs = sorted([f for f in r.json() if f["name"].startswith(pre) and f["name"].endswith(".jpg")], key=lambda f: f["name"])
    stamps = sorted({re.sub(r"(_preview)?\.jpg$", "", f["name"]) for f in imgs})
    old = set(stamps[:-KEEP]) if len(stamps) > KEEP else set()
    for f in imgs:
        if re.sub(r"(_preview)?\.jpg$", "", f["name"]) in old:
            d = requests.delete(API + f["path"], headers=H(), json={"message": "prune " + f["name"], "sha": f["sha"], "branch": "main"}, timeout=60)
            print("prune", f["name"], d.status_code, file=sys.stderr)
def f1(v, d=1, sign=True): return "-" if v is None else (f"{v:+,.{d}f}" if sign else f"{v:,.{d}f}")
def pct(v, d=1): return "-" if v is None else f1(v, d) + "%"
def busd(v): return "-" if v is None else f"${v/1e9:,.2f}B"
DIV = "──────────"
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
def bdate(s):
    m = re.match(r"\d{4}-(\d\d)-(\d\d) (\d\d:\d\d)", s or "")
    return f"{m.group(1)}/{m.group(2)} {m.group(3)}" if m else (s or "?")
CHANGE_FILE = os.path.join(HERE, "out/change_text.txt")
def change_text():
    try:
        if time.time() - os.path.getmtime(CHANGE_FILE) > 3 * 3600: return ""  # stale: skip
        return EMOJI.sub("", open(CHANGE_FILE).read()).strip()
    except Exception:
        return ""
LEAD_FILE = os.path.join(HERE, "out/lead_text.txt")
def lead_text():
    try:
        if time.time() - os.path.getmtime(LEAD_FILE) > 3 * 3600: return ""  # stale: skip
        return strip_hl(EMOJI.sub("", open(LEAD_FILE).read()).strip())[:4900]
    except Exception:
        return ""
def change_section(sec):
    t = change_text()
    lines = t.splitlines()
    if lines and lines[0].strip() == "【持倉變化】": lines = lines[1:]  # header added here; avoid duplicate
    while lines and not lines[0].strip(): lines = lines[1:]
    return sec("【持倉變化】", *lines) if lines else []
def kol_section(sec):
    try:
        d = json.load(open(os.path.join(HERE, "kol/latest_view.json")))
        acc = d["accounts"]
    except Exception:
        return []
    ts = datetime.strptime(d["updated_at"][:16], "%Y-%m-%d %H:%M").strftime("%m/%d %H:%M")
    n = lambda s: sum(1 for a in acc if a.get("stance") == s)
    tally = f"偏多 {n('偏多')}｜中性 {n('中性')}｜偏空 {n('偏空')}｜未談 {n('未談BTC')}"
    lines = [f"【分析師觀點】(更新 {ts})",
             f"・總結：{len(acc)} 個帳號中 {len(acc) - n('未談BTC')} 個談到 BTC" + (f"，{d['summary']}" if d.get("summary") else "") + "：" + tally]
    if d.get("common_levels"): lines.append("・共同價位：" + d["common_levels"])
    if d.get("divergence"): lines.append("・主要分歧：" + d["divergence"])
    return sec(*lines)
KOL_DIV = DIV
def kfmt(t):
    """80.4K -> 80,400; 126K -> 126,000 (thousands separators)."""
    k = lambda x: f"{round(float(x) * 1000):,}"
    t = re.sub(r"(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)K", lambda m: k(m.group(1)) + "–" + k(m.group(2)), t or "")
    t = re.sub(r"(\d+(?:\.\d+)?)K", lambda m: k(m.group(1)), t)
    return re.sub(r"(?<![\d,.])(\d{5,})(?![\d.])", lambda m: f"{int(m.group(1)):,}", t)
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
# 鋒哥 長期觀點（與 fengge_section.FAV_VIEWS long 同步，人工維護）
KOL_EXTRA_LONG = {"traderfengge": ["57,700 為本輪低點", "目標回前高約 130,000", "失效：跌回 70,000，再破 60,000 則反轉失敗"]}
def kol_text():
    """/btckol text: title+time, ━ blocks: 整體立場 / 共同關注價位 / 主要分歧 / 各分析師 (觀點+短線+長期 per analyst)."""
    try:
        d = json.load(open(os.path.join(HERE, "kol/latest_view.json"))); acc = d["accounts"]
    except Exception:
        return ""
    ts = datetime.strptime(d["updated_at"][:16], "%Y-%m-%d %H:%M").strftime("%Y.%m.%d %H:%M")
    st = lambda a: "未談BTC" if _quiet(a) else a.get("stance")
    n = lambda s: sum(1 for a in acc if st(a) == s)
    px = ""
    try:
        pf = os.path.join(HERE, "out/posgrid_now.json")
        if time.time() - os.path.getmtime(pf) < 7200:
            b = json.load(open(pf)).get("btc")
            if b: px = f"｜BTC {b:,.0f}"
    except Exception:
        px = ""
    L = ["BTC 分析師觀點", f"{ts} (UTC+8){px}", "", KOL_DIV, "【整體立場】",
         f"・看多 {n('偏多')}｜中性 {n('中性')}｜看空 {n('偏空')}｜未談 {n('未談BTC')}"]
    if d.get("summary"): L.append("・" + kfmt(d["summary"]).strip())
    if d.get("common_levels"):
        L += ["", KOL_DIV, "【共同關注價位】"] + ["・" + kfmt(x).strip() for x in d["common_levels"].split("｜") if x.strip()]
    if d.get("divergence"):
        L += ["", KOL_DIV, "【主要分歧】"] + ["・" + kfmt(x).strip().rstrip("。") for x in re.split(r"[；;]", d["divergence"]) if x.strip()]
    lab = {"偏多": "看多", "偏空": "看空", "中性": "中性", "未談BTC": "未談"}
    order = {"偏多": 0, "中性": 1, "偏空": 2, "未談BTC": 3}
    L += ["", KOL_DIV, "【各分析師】"]
    quiet = []
    for a in sorted(acc, key=lambda a: order.get(st(a), 9)):
        if st(a) == "未談BTC": quiet.append(a["handle"]); continue
        r = kfmt(a.get("reason", "")).split("；")[0].rstrip("。")
        segs = [x.strip() for x in (a.get("levels") or "").split("｜") if x.strip() and x.strip() != "-"]
        lo = [kfmt(x) for x in segs if _tf(x) == "long"] + KOL_EXTRA_LONG.get(a["handle"], []); sh = [kfmt(x) for x in segs if _tf(x) != "long"]
        L.append(f"・{a['handle']}｜{lab.get(a.get('stance'), a.get('stance', '-'))}" + (f"（{a['latest_post'][5:].replace('-', '/')}）" if a.get("latest_post") else ""))
        if r: L.append(f"  觀點：{r}")
        if sh: L.append("  短線：" + "；".join(sh))
        if lo: L.append("  長期：" + "；".join(lo))
    if quiet: L.append("・近期未談 BTC：" + "、".join(quiet))
    try:   # CryptoQuant top-5 BTC analysts (cq_consensus.py --kol, run as a script, reads the daily cache); optional
        cq = subprocess.run([sys.executable, os.path.join(HERE, "cq_consensus.py"), "--kol"], cwd=HERE, capture_output=True, text=True, timeout=90).stdout.strip()
        if cq.startswith("【CryptoQuant"): L += ["", KOL_DIV] + cq.splitlines()
    except Exception as e: print("cq kol skipped:", e, file=sys.stderr)
    L += ["", "僅整理公開觀點，非投資建議"]
    return EMOJI.sub("", "\n".join(L))[:4900]
def summary(j, when):
    g = j.get
    px = f"BTC {g('btc'):,.0f}" if g("btc") else "BTC -"
    def sec(*lines): return ["", DIV, *lines]
    L = ["BTC 持倉數據", f"{when.strftime('%Y.%m.%d %H:%M')} (UTC+8)｜{px}"]
    # Lead sections, in order: 1 分析師觀點, 2 持倉變化; then the positioning sections.
    for lead in (change_section,):   # analyst views moved to /analyst (btc/kol_latest.json)
        L += lead(sec)
    L += sec("【多空差值】",
             f"・全部帳戶：OKX {pct(g('多空差值_全部_okx'))}｜Gate {pct(g('多空差值_全部_gate'))}",
             f"・大戶持倉：OKX {pct(g('多空差值_okx_大戶持倉'))}｜Gate {pct(g('多空差值_gate_大戶持倉'))}",
             f"・大戶帳戶：OKX {pct(g('多空差值_okx_大戶帳戶'))}｜Gate {pct(g('多空差值_gate_大戶帳戶'))}")
    w = g('巨鯨散戶差_okx_4H')
    mean = "" if w is None else ("｜大戶較散戶偏多" if w > 0 else "｜大戶較散戶偏空" if w < 0 else "｜大戶與散戶持平")
    L += sec("【巨鯨 vs 散戶】", f"・OKX {f1(w, 2)}{mean}")
    def oi(name, k):
        c = g(k.replace("_usd", "_24h_pct"))
        return f"{name} {busd(g(k))}" + (f" ({pct(c)} 24h)" if c is not None else "")
    L += sec("【未平倉量】", "・" + "｜".join([oi("OKX", "未平倉_okx_usd"), oi("Gate", "未平倉_gate_usd")]))
    L += sec("【資金費率】",
             f"・每 8h：OKX {pct(g('資金費率_okx_8h_pct'),4)}｜Gate {pct(g('資金費率_gate_pct'),4)}")
    L += sec("【Bitfinex 槓桿】",
             f"・{pct(g('多空差值_bitfinex_pct'),2)}｜多 {g('bitfinex_多_btc') or 0:,.0f}／空 {g('bitfinex_空_btc') or 0:,.0f} BTC")
    ah = g('ahr999')
    zone = "" if ah is None else ("｜抄底區（<0.45）" if ah < 0.45 else "｜定投區（0.45–1.2）" if ah <= 1.2 else "｜高於定投線（>1.2）")
    L += sec("【Ahr999】", f"・{f1(ah,2,False)}{zone}")
    if g("binance_多空差值_大戶持倉") is not None:
        L += sec("【Binance】",
                 f"・全部 {pct(g('binance_多空差值_全部'))}｜大戶持倉 {pct(g('binance_多空差值_大戶持倉'))}",
                 f"・巨鯨vs散戶 {f1(g('binance_巨鯨散戶差'),2)}")
    t = EMOJI.sub("", "\n".join(L))[:4900]
    assert not EMOJI.search(t)
    return t
def publish_kol(when, stamp):
    """Analyst image + btc/kol_latest.json only. Never touches btc/latest.json."""
    os.makedirs(os.path.join(HERE, "out/publish"), exist_ok=True)
    try:   # analyst image (kol_chart.py, run as subprocess); optional
        kp = subprocess.run([os.path.join(HERE, ".venv/bin/python"), os.path.join(HERE, "kol_chart.py")], cwd=HERE, capture_output=True, text=True, timeout=120).stdout.strip().splitlines()
        if kp and kp[-1].endswith(".png"):
            ki = Image.open(kp[-1]).convert("RGB"); kb = os.path.join(HERE, f"out/publish/btc_kol_{stamp}.jpg"); ki.save(kb, "JPEG", quality=90, optimize=True)
            kq = ki.copy(); kq.thumbnail((1024, 1024)); kpv = kb[:-4] + "_preview.jpg"; kq.save(kpv, "JPEG", quality=85, optimize=True)
            m1 = "btc/" + os.path.basename(kb); m2 = "btc/" + os.path.basename(kpv)
            k = {"date": when.strftime("%Y-%m-%d %H:%M"), "kol_text": kol_text(),
                 "kol_image_url": pinned(gh_put(m1, open(kb, "rb").read(), "btc kol " + stamp), m1),
                 "kol_preview_url": pinned(gh_put(m2, open(kpv, "rb").read(), "btc kol preview " + stamp), m2)}
            gh_put("btc/kol_latest.json", json.dumps(k, ensure_ascii=False, indent=1).encode(), "btc kol latest " + stamp)
    except Exception as e:
        print("kol image skipped:", e, file=sys.stderr)
def add_structure(text):
    """Insert the objective structure section (btc_structure.py, run as a script) before 【Binance…】, else at the end.
    Title 【技術結構】 (old title 【技術結構（客觀數據，非投資建議）】 also recognised): any existing structure block is
    replaced by the fresh one, so it never appears twice. On any failure/timeout the text is returned unchanged."""
    try:
        m = re.search(r"｜BTC ([\d,]+(?:\.\d+)?)", "\n".join(text.splitlines()[:3]))
        cmd = [sys.executable, os.path.join(HERE, "btc_structure.py")] + (["--price", m.group(1).replace(",", "")] if m else [])
        sec = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=45).stdout.strip()
        if not sec.startswith("【技術結構") or len(sec.splitlines()) != 2: return text
        old = re.compile(r"\n*" + re.escape(DIV) + r"\n【技術結構(?:】|（客觀數據，非投資建議）】)\n(?:・[^\n]*(?:\n|$))*")
        t = old.sub("\n", text).rstrip()   # drop any existing structure block (new or old title)
        blk = "\n\n" + DIV + "\n" + sec
        i = t.find("\n\n" + DIV + "\n【Binance")
        return (t[:i] + blk + t[i:] if i >= 0 else t + blk)[:4900]
    except Exception as e:
        print("structure skipped:", e, file=sys.stderr); return text
def add_range(text):
    """Append the range_body.py (run as a script) consolidation line inside 【技術結構】, once. Unchanged on failure / no active range."""
    try:
        ln = subprocess.run([os.path.join(HERE, ".venv/bin/python"), os.path.join(HERE, "range_body.py"), "--line"], cwd=HERE, capture_output=True, text=True, timeout=60).stdout.strip()
        if not ln.startswith("盤整區 ") or "\n" in ln: return text
        t = re.sub(r"\n・盤整區 [^\n]*", "", text)
        m = re.search(r"【技術結構】\n(?:・[^\n]*(?:\n|$))*", t)
        if not m: return text
        blk = m.group(0).rstrip("\n")
        return (t[:m.start()] + blk + "\n・" + ln + t[m.start() + len(blk):])[:4900]
    except Exception as e:
        print("range skipped:", e, file=sys.stderr); return text
def add_consensus(text):
    """Insert 【分析師共識】 (cq_consensus.py --line, run as a script; CryptoQuant σ as a contrarian gauge, cache refreshed daily)
    right before 【技術結構】 (else before 【Binance…】, else at the end). Replaced if present, so it appears once. Unchanged on failure."""
    try:
        sec = subprocess.run([sys.executable, os.path.join(HERE, "cq_consensus.py"), "--line"], cwd=HERE, capture_output=True, text=True, timeout=90).stdout.strip()
        if not sec.startswith("【分析師共識】"): return text
        t = re.sub(r"\n*" + re.escape(DIV) + r"\n【分析師共識】.*?(?=\n\n" + re.escape(DIV) + r"\n|\Z)", "", text, flags=re.S).rstrip()
        i = t.find("\n\n" + DIV + "\n【技術結構")
        if i < 0: i = t.find("\n\n" + DIV + "\n【Binance")
        blk = "\n\n" + DIV + "\n" + sec
        return (t[:i] + blk + t[i:] if i >= 0 else t + blk)[:4900]
    except Exception as e:
        print("consensus skipped:", e, file=sys.stderr); return text
def add_fengge(text):
    """Insert 【短線交易模型】 (fengge_section.py, run as a script; Daniel 2026-10-24) after 【技術結構】, before 【Binance…】
    (else at the end). Replaced if present. Reference only, does not replace the main model. Unchanged on failure."""
    try:
        m = re.search(r"｜BTC ([\d,]+(?:\.\d+)?)", "\n".join(text.splitlines()[:3]))
        cmd = [sys.executable, os.path.join(HERE, "fengge_section.py")] + (["--price", m.group(1).replace(",", "")] if m else [])
        sec = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=60).stdout.strip()
        if not sec.startswith(("【交易模型】", "【短線交易模型】")): return text
        t = re.sub(r"\n*" + re.escape(DIV) + r"\n【(?:短線)?交易模型】.*?(?=\n\n" + re.escape(DIV) + r"\n|\Z)", "", text, flags=re.S).rstrip()
        i = t.find("\n\n" + DIV + "\n【Binance")
        blk = "\n\n" + DIV + "\n" + sec
        return (t[:i] + blk + t[i:] if i >= 0 else t + blk)[:4900]
    except Exception as e:
        print("fengge skipped:", e, file=sys.stderr); return text
FORECAST_IN_SUMMARY = False   # Worker sends forecast_parts as separate messages (2026-10-24 17:44)
FORECAST_FILE = os.path.join(HERE, "out/publish/forecast_send.txt")
MODEL_RE = r"\n*" + re.escape(DIV) + r"\n【(?:短線)?交易模型】.*?(?=\n\n" + re.escape(DIV) + r"\n|\Z)"
def strip_model(text):
    """Drop 【交易模型】 from the data message (it now lives in forecast_text)."""
    return re.sub(MODEL_RE, "", text, flags=re.S).rstrip()
def build_forecast(text):
    """BTC 走勢預測 (fengge_section.py --forecast, run as a script; Daniel 2026-10-24 16:58). "" on failure.
    The time line reuses the summary's timestamp so the Worker footer time is identical."""
    try:
        head = "\n".join(text.splitlines()[:3])
        m = re.search(r"｜BTC ([\d,]+(?:\.\d+)?)", head)
        cmd = [sys.executable, os.path.join(HERE, "fengge_section.py"), "--forecast-compact"] + (["--price", m.group(1).replace(",", "")] if m else [])
        fc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=60).stdout.strip()
        if not fc.startswith("BTC 走勢預測"): return ""
        ts = re.search(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}", text)
        if ts: fc = re.sub(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}", ts.group(0), fc, count=1)
        return fc   # no truncation: split into parts instead (Daniel 2026-10-24 17:38)
    except Exception as e:
        print("forecast skipped:", e, file=sys.stderr); return ""
LINE_MAX = 4950
def split_parts(t, lim=LINE_MAX):
    """Split on section boundaries (blank line + DIV); oversize sections fall back to line breaks. Each part <= lim chars."""
    secs = re.split(r"\n\n(?=" + re.escape(DIV) + r"\n)", t.strip()) if t else []
    units = []
    for x in secs:
        if len(x) <= lim: units.append(x); continue
        cur = ""
        for ln in x.split("\n"):
            while len(ln) > lim: units.append(ln[:lim]); ln = ln[lim:]
            if cur and len(cur) + 1 + len(ln) > lim: units.append(cur); cur = ln
            else: cur = cur + "\n" + ln if cur else ln
        if cur: units.append(cur)
    parts, cur = [], ""
    for u in units:
        if cur and len(cur) + 2 + len(u) > lim: parts.append(cur); cur = u
        else: cur = cur + "\n\n" + u if cur else u
    if cur: parts.append(cur)
    return parts
def write_forecast_parts(fc):
    """out/publish/forecast_send.txt = full forecast; forecast_part1.txt, part2... each <= 4950 chars (for chat push / future Worker)."""
    for f in glob.glob(os.path.join(HERE, "out/publish/forecast_part*.txt")): os.remove(f)
    parts = split_parts(fc)
    for i, x in enumerate(parts, 1):
        with open(os.path.join(HERE, f"out/publish/forecast_part{i}.txt"), "w") as fh: fh.write(x)
    return parts
def merge_summary(fc, data):
    """Forecast prepended while the merged text fits one LINE message; otherwise summary_text = data only and
    the forecast goes out as separate parts (forecast_send.txt / forecast_part*.txt / forecast_parts). Nothing dropped."""
    if not (FORECAST_IN_SUMMARY and fc): return data
    m = fc + "\n\n" + DIV + "\n\n" + data
    return m if len(m) <= LINE_MAX else data
BN_FRESH = 2 * 3600
def bn_latest():
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{os.path.join(HERE, 'data/positioning2.db')}?mode=ro", uri=True, timeout=20)
        r = c.execute("select ts,global_account,top_position from binance where global_account is not null and top_position is not null order by ts desc limit 1").fetchone(); c.close()
        return r
    except Exception:
        return None
def bn_header(r=None):
    """【Binance】 when the newest binance row is live (www.binance.com, <=2h old); else 【Binance（截至 MM/DD HH:MM）】 (daily-dump fallback)."""
    r = r or bn_latest(); ts = r[0] if r else None
    if ts and time.time() - ts <= BN_FRESH: return "【Binance】"
    return f"【Binance（截至 {datetime.fromtimestamp(ts).strftime('%m/%d %H:%M')}）】" if ts else "【Binance】"
def add_binance(text):
    """Rebuild the 【Binance…】 block from positioning2.db (same formulas as positioning_grid.py), so title and numbers match."""
    r = bn_latest()
    if not r: return text
    d = lambda x: (x - 1) / (x + 1) * 100
    blk = [bn_header(r), f"・全部 {pct(d(r[1]))}｜大戶持倉 {pct(d(r[2]))}", f"・巨鯨vs散戶 {f1(r[2] - r[1], 2)}"]
    t = re.sub(r"\n*" + re.escape(DIV) + r"\n【Binance[^\n]*】.*?(?=\n\n" + re.escape(DIV) + r"\n|\Z)", "", text, flags=re.S).rstrip()
    return (t + "\n\n" + DIV + "\n" + "\n".join(blk))[:4900]   # last section, after 【技術結構】
def strip_hl(text):
    """Hyperliquid removed 2026-10-20 (Daniel): drop HL from any supplied text (OI item, 1h funding line)."""
    t = re.sub(r"｜HL \$[^｜\n]*", "", text)
    t = re.sub(r"\n・每 1h：HL [^\n]*", "", t)
    return re.sub(r"\n[^\n]*(?:Hyperliquid|HL\*\*)[^\n]*", "", t)
NOTE_POS = "（看金額：大戶多單減空單部位差）"
NOTE_ACC = "（看人數：大戶多空帳戶數差）"
NOTE_BN = "（大戶持倉看金額：保證金前 20%）"
def add_notes(text):
    """One-line plain-language note under each 大戶持倉/大戶帳戶 % line (diff = long% - short%) and the Binance line. Idempotent."""
    out = []; L = text.splitlines()
    for i, ln in enumerate(L):
        out.append(ln)
        nx = L[i + 1] if i + 1 < len(L) else ""
        note = NOTE_POS if ln.startswith("・大戶持倉：") else NOTE_ACC if ln.startswith("・大戶帳戶：") else NOTE_BN if (ln.startswith("・全部 ") and "大戶持倉" in ln) else None
        if note and not ("看金額" in nx or "看人數" in nx): out.append(note)
    return "\n".join(out)[:4900]
def add_longshort(text):
    """Insert 【多空比】 (ls_section.py, run as a script) right before 【未平倉量】 (i.e. after 【散戶 vs 大戶】).
    Any existing 【多空比】 block is replaced, so it appears once. On failure the text is returned unchanged."""
    try:
        sec = subprocess.run([sys.executable, os.path.join(HERE, "ls_section.py")], cwd=HERE, capture_output=True, text=True, timeout=45).stdout.strip()
        if not sec.startswith("【多空比】"): return text
        t = re.sub(r"\n*" + re.escape(DIV) + r"\n【多空比】.*?(?=\n\n" + re.escape(DIV) + r"\n|\Z)", "", text, flags=re.S).rstrip()
        i = t.find("\n\n" + DIV + "\n【未平倉量】")
        if i < 0: return text
        return (t[:i] + "\n\n" + DIV + "\n" + sec + t[i:])[:4900]
    except Exception as e:
        print("longshort skipped:", e, file=sys.stderr); return text
RVW_KEYS = ("・全體帳戶：", "・OKX 全體", "・全體：", "・大戶持倉：", "・大戶帳戶：", "・巨鯨散戶差")
def add_rvw(text):
    """Rewrite the data bullets inside 【散戶 vs 大戶】 with every exchange in positioning2.db (ls_section.py --rvw, run as a script),
    same OI order as 【多空比】. Prose lines (散戶：/大戶：/解讀：) are kept. On any failure the text is returned unchanged."""
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, "ls_section.py"), "--rvw"], cwd=HERE, capture_output=True, text=True, timeout=45).stdout.strip()
        new = {}
        for ln in out.splitlines():
            for k in ("・全體帳戶：", "・大戶持倉：", "・大戶帳戶：", "・巨鯨散戶差："):
                if ln.startswith(k): new[k] = ln
        if not new: return text
        L = text.splitlines(); inblk = False; res = []
        for ln in L:
            if ln.startswith("【"): inblk = ln.startswith("【散戶 vs 大戶】")
            if inblk and ln.startswith(RVW_KEYS):
                k = "・全體帳戶：" if ln.startswith(("・全體帳戶：", "・OKX 全體", "・全體：")) else "・巨鯨散戶差：" if ln.startswith("・巨鯨散戶差") else ln[:6]
                if k in new:
                    m = re.search(r"（[^）]*）\s*$", ln) if k == "・巨鯨散戶差：" else None
                    res.append(new.pop(k) + (m.group(0) if m else "")); continue
            res.append(ln)
        return "\n".join(res)[:4900]
    except Exception as e:
        print("rvw skipped:", e, file=sys.stderr); return text
def main():
    a = argparse.ArgumentParser(); a.add_argument("--image"); a.add_argument("--json"); a.add_argument("--text"); a.add_argument("--dry-run", action="store_true"); a.add_argument("--kol-only", action="store_true", help="only analyst image + btc/kol_latest.json"); a = a.parse_args()
    if a.kol_only:
        now = datetime.now(); publish_kol(now, now.strftime("%Y%m%d_%H%M"))
        try: prune_one("btc_kol_")
        except Exception as e: print("prune fail", e, file=sys.stderr)
        print("KOL_PUBLISHED", file=sys.stderr); return
    j = {}
    if a.json: j = json.load(open(a.json))
    if not a.image and not a.json:
        out = subprocess.run([os.path.join(HERE, ".venv/bin/python"), os.path.join(HERE, "positioning_grid.py")], cwd=HERE, capture_output=True, text=True, timeout=600).stdout
        for line in out.splitlines():
            if line.startswith("POSGRID_JSON "): j = json.loads(line[13:])
            elif line.strip().endswith(".png"): a.image = line.strip()
        if not j or not a.image: sys.exit("positioning_grid.py failed")
    if not a.image:
        a.image = max(glob.glob(os.path.join(HERE, "out/positioning/btc_positioning_overview_*.png")), key=os.path.getmtime)
    mt = re.search(r"(\d{8})_(\d{4,6})", os.path.basename(a.image))
    when = datetime.strptime(mt.group(1) + mt.group(2)[:4], "%Y%m%d%H%M") if mt else datetime.now()
    if a.text: text = open(a.text).read().strip()[:4900]
    elif j: text = summary(j, when)
    else: sys.exit("need --text or --json (or run without args)")
    text = add_notes(strip_hl(add_binance(add_fengge(add_consensus(add_longshort(add_rvw(add_range(add_structure(text)))))))))
    fc = build_forecast(text)
    if fc: text = strip_model(text)
    stamp = when.strftime("%Y%m%d_%H%M")
    os.makedirs(os.path.join(HERE, "out/publish"), exist_ok=True)
    big = os.path.join(HERE, f"out/publish/btc_positioning_{stamp}.jpg"); pre = big[:-4] + "_preview.jpg"
    im = Image.open(a.image).convert("RGB")
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
    print("built", big, os.path.getsize(big), pre, os.path.getsize(pre), file=sys.stderr)
    with open(FORECAST_FILE, "w") as fh: fh.write(fc)
    fparts = write_forecast_parts(fc)
    merged = merge_summary(fc, text) != text
    print(text)
    print("FORECAST_FILE", FORECAST_FILE, len(fc), "parts", [len(x) for x in fparts], "merged_into_summary", merged, file=sys.stderr)
    if a.dry_run: return
    n1 = "btc/" + os.path.basename(big); n2 = "btc/" + os.path.basename(pre)
    u1 = pinned(gh_put(n1, open(big, "rb").read(), "btc chart " + stamp), n1)
    u2 = pinned(gh_put(n2, open(pre, "rb").read(), "btc preview " + stamp), n2)
    d = {"date": when.strftime("%Y-%m-%d %H:%M"), "summary_text": merge_summary(fc, text), "forecast_text": fc, "forecast_parts": fparts, "forecast_merged": merged, "change_text": change_text(), "lead_text": lead_text(), "image_url": u1, "preview_url": u2}
    publish_kol(when, stamp)
    gh_put("btc/latest.json", json.dumps(d, ensure_ascii=False, indent=1).encode(), "btc latest " + stamp)
    print("PUBLISHED", u1, u2, file=sys.stderr)
    try: prune()
    except Exception as e: print("prune fail", e, file=sys.stderr)
if __name__ == "__main__": main()

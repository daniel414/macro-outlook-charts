#!/usr/bin/env python3
"""Publish BTC positioning overview + summary to GitHub (macro-outlook-media/btc/) for the LINE /BTC command.
Usage:
  publish_btc.py                 # run positioning_grid.py now, auto summary, upload
  publish_btc.py --image X.png --json posgrid.json [--text summary.txt]
  publish_btc.py --dry-run       # build files, no upload
  MIDLONG_VOICE=0 publish_btc.py --dry-run   # 暫時關掉中長期口語（持久開關見 midlong_voice.json）
Never pushes LINE messages; the Worker only replies."""
import argparse, base64, glob, json, os, re, subprocess, sys, time
from datetime import datetime
import requests
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__))
GHR = "daniel414/macro-outlook-media"  # default media (Daniel 2026-10-29); charts is scripts-only
# Override order: GHR_OVERRIDE env > line_push.py literal > default media
try:
    src = open("/workspace/big-players/line_push.py").read()
    m = re.search(r'GHR="([^"]+)"', src); GHR = m.group(1) if m else GHR
except OSError: pass
GHR = os.environ.get("GHR_OVERRIDE") or GHR
KEEP = 120  # ~1 week (16 runs/day)
def H():
    t = os.environ.get("MEDIA_REPO_TOKEN") or os.environ["GH_CHARTS_TOKEN"]
    return {"Authorization": "Bearer " + t, "Accept": "application/vnd.github+json"}
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
    # Private-repo safe: verify via Contents API with MEDIA_REPO_TOKEN (fallback GH_CHARTS_TOKEN), still write the raw sha URL (Worker rewrites it to a signed /img/ URL).
    raw = f"https://raw.githubusercontent.com/{GHR}/{sha}/{path}"
    if not (os.environ.get("MEDIA_REPO_TOKEN") or os.environ.get("GH_CHARTS_TOKEN")):
        print("WARN pinned: MEDIA_REPO_TOKEN/GH_CHARTS_TOKEN not set, cannot verify " + path + ", using raw URL anyway", file=sys.stderr); return raw
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
PRICE = {}   # set by apply_chainlink(): old/new price strings, source label, check details
def apply_chainlink(text, ref=None):
    """Current price = Chainlink BTC/USD (Daniel 2026-10-27). Sanity: diff vs existing source < 0.5%, updatedAt <= 2h,
    else keep the existing (exchange kline) price. Source label stays in log/json only, not in user-facing text."""
    L = text.splitlines()
    m = re.search(r"｜BTC ([\d,]+(?:\.\d+)?)", L[1]) if len(L) > 1 else None
    if not m: return text
    old = m.group(1); ref = ref or float(old.replace(",", ""))
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "chainlink_price.py"), str(ref)], cwd=HERE, capture_output=True, text=True, timeout=60)
        c = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        c = {"ok": False, "reason": f"chainlink script failed: {type(e).__name__}"}
    PRICE.update(check=c, ref=ref)
    if c.get("ok"):
        new = f"{c['price']:,.0f}"; PRICE.update(old=old, new=new, source="Chainlink")
        text = text.replace(old, new)
    else:
        PRICE.update(source="交易所 K 線（Chainlink 未通過檢查）")
    print("PRICE_SOURCE", json.dumps({k: v for k, v in PRICE.items()}, ensure_ascii=False), file=sys.stderr)
    return strip_price_source(text)
def strip_price_source(t):
    """Remove user-facing price-source labels. Internal PRICE check is unchanged."""
    t = re.sub(r"｜現價來源：[^\n｜]*", "", t)
    t = re.sub(r"現價來源：[^\n｜]*", "", t)
    t = re.sub(r"｜[ \t]*$", "", t, flags=re.M)
    return t
def add_src(t):
    """Deprecated as an append: user-facing text must not show 現價來源. Still strips leftovers."""
    return strip_price_source(t)
def lead_text():
    try:
        if time.time() - os.path.getmtime(LEAD_FILE) > 3 * 3600: return ""  # stale: skip
        t = strip_hl(EMOJI.sub("", open(LEAD_FILE).read()).strip())
        # Always sync lead price to Chainlink new (not only when lead happens to contain PRICE["old"])
        if PRICE.get("new"):
            t = re.sub(r"BTC [\d,]+(?:\.\d+)?", "BTC " + PRICE["new"], t, count=1)
        elif PRICE.get("old") and PRICE.get("new"):
            t = t.replace(PRICE["old"], PRICE["new"])
        return t[:4900]
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
# Analyst name -> X handle (Daniel 2026-10-24: 分析師一律用 X 帳號；鋒哥 保留中文)
X_HANDLE = {"benjamincowen": "benjamincowen", "Benjamin Cowen": "benjamincowen", "Cowen": "benjamincowen",
            "KrownCryptoCave": "KrownCryptoCave", "Krown": "KrownCryptoCave", "KillaXBT": "KillaXBT", "Killa": "KillaXBT",
            "SailorManCrypto": "SailorManCrypto", "SailorMan": "SailorManCrypto", "Murphychen888": "Murphychen888", "Murphy": "Murphychen888",
            "market_beggar": "market_beggar", "qinbafrank": "qinbafrank", "0xCryptoChan": "0xCryptoChan", "damskotrades": "damskotrades",
            "rektcapital": "rektcapital", "RektProof": "RektProof", "WallStreet0Name": "WallStreet0Name", "jstin0502": "jstin0502", "zemummy1": "zemummy1", "十老板": "zemummy1"}
def xh_name(h):
    return "鋒哥" if h == "traderfengge" else "@" + X_HANDLE.get(h, h)
def xh(text):
    """Replace bare analyst names in free text with @handle (skip already-@ and parts of longer words)."""
    for k in sorted(X_HANDLE, key=len, reverse=True):
        text = re.sub(r"(?<![@A-Za-z0-9_])" + re.escape(k) + r"(?![A-Za-z0-9_])", "@" + X_HANDLE[k], text)
    return text
def _one(a, cap=46):
    """One short sentence per analyst (Daniel 2026-10-27): core view clipped at a clause boundary, plus up to 2 key levels not already in it."""
    r = xh(kfmt(a.get("reason", ""))).split("；")[0].rstrip("。").strip()
    r = re.sub(r"^YouTube\s*\d+/\d+[：:]\s*", "", r)
    if len(r) > cap:
        parts, out = re.split(r"(?<=[，、])", r), ""
        for x in parts:
            if out and len(out + x) > cap: break
            out += x
        r = out.rstrip("，、 ")
    segs = [kfmt(x.strip()) for x in (a.get("levels") or "").split("｜") if x.strip() and x.strip() != "-" and re.search(r"\d", x)]
    nums = lambda t: set(re.findall(r"\d[\d,]{3,}", t))
    keys = [x for x in segs if len(x) <= 22 and not (nums(x) and nums(x) <= nums(r))][:2] if len(r) < 40 else []
    return r + ("｜關鍵 " + "；".join(keys) if keys else "")
def kol_model():
    """Slim content model from kol_chart.py --slim-json (run as a script). None on failure."""
    try:
        r = subprocess.run([os.path.join(HERE, ".venv/bin/python"), os.path.join(HERE, "kol_chart.py"), "--slim-json"],
                           cwd=HERE, capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except Exception as e:
        print("kol model skipped:", e, file=sys.stderr); return None
KOL_LAB = {"偏多": "看多", "偏空": "看空", "中性": "中性"}
def kol_text_slim(m):
    """Slim /btckol text (2026-11-25): stance tally, <=6 common levels, divergence, one line per analyst who talked BTC.
    No 未談 list, no CryptoQuant block, no disclaimer. Same content model as the 3 images."""
    if m.get("banned_hits"):
        raise RuntimeError("kol slim text blocked: " + "; ".join(m["banned_hits"][:5]))
    ts = datetime.strptime(m["updated_at"], "%Y-%m-%d %H:%M")
    px = ""
    try:   # fresh positioning snapshot (< 2h) first
        pf = os.path.join(HERE, "out/posgrid_now.json")
        if time.time() - os.path.getmtime(pf) < 7200:
            b = json.load(open(pf)).get("btc")
            if b: px = f"｜BTC {b:,.0f}"
    except Exception:
        px = ""
    if not px:   # otherwise one Chainlink read (run as a script; ok only if updatedAt <= 2h)
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, "chainlink_price.py")], cwd=HERE, capture_output=True, text=True, timeout=60)
            c = json.loads(r.stdout.strip().splitlines()[-1])
            if c.get("ok") and c.get("price"): px = f"｜BTC {c['price']:,.0f}"
            else: print("kol price skipped:", c.get("reason"), file=sys.stderr)
        except Exception as e:
            print("kol price skipped:", type(e).__name__, file=sys.stderr)
    c = m["counts"]
    L = ["BTC 分析師觀點", f"{ts:%Y.%m.%d} 台北時間 {ts:%H:%M}{px}", "", KOL_DIV, "【整體立場】",
         f"・看多 {c['偏多']}｜中性 {c['中性']}｜看空 {c['偏空']}，本期 {m['n_talk']} 位談到 BTC"]
    if m.get("common_levels"):
        L += ["", KOL_DIV, "【共同關注價位】"]
        for x in m["common_levels"]:
            lvl, _, note = x.partition("｜")
            L.append("・" + kfmt(lvl).strip() + ("｜" + xh(kfmt(note)).strip() if note else ""))
    if m.get("divergence"):
        L += ["", KOL_DIV, "【主要分歧】", "・" + xh(kfmt(m["divergence"])).strip().rstrip("。")]
    L += ["", KOL_DIV, "【各分析師】"]
    for r in m["bull"] + m["bear_neutral"]:
        body = "；".join([r["line"]] + r["points"])
        L.append(f"・{xh_name(r['handle'])}｜{KOL_LAB.get(r['stance'], r['stance'])}：" + kfmt(body).rstrip("。"))
    t = EMOJI.sub("", "\n".join(L))
    if len(t) > 4900:   # never cut mid-line: drop whole analyst lines from the end
        keep = t.splitlines()
        while len("\n".join(keep)) > 4900: keep.pop()
        t = "\n".join(keep)
    return t
def kol_text():
    """/btckol text: title+time, ━ blocks: 整體立場 / 共同關注價位 / 主要分歧 / 各分析師 (one line per analyst)."""
    try:
        d = json.load(open(os.path.join(HERE, "kol/latest_view.json"))); acc = d["accounts"]
    except Exception:
        return ""
    m = kol_model()
    if m and m.get("enabled"):
        return kol_text_slim(m)
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
    if d.get("summary"): L.append("・" + xh(kfmt(d["summary"]).strip()))
    if d.get("common_levels"):
        L += ["", KOL_DIV, "【共同關注價位】"] + ["・" + kfmt(x).strip() for x in d["common_levels"].split("｜") if x.strip()]
    if d.get("divergence"):
        L += ["", KOL_DIV, "【主要分歧】"] + ["・" + xh(kfmt(x).strip()).rstrip("。") for x in re.split(r"[；;]", d["divergence"]) if x.strip()]
    lab = {"偏多": "看多", "偏空": "看空", "中性": "中性", "未談BTC": "未談"}
    order = {"偏多": 0, "中性": 1, "偏空": 2, "未談BTC": 3}
    L += ["", KOL_DIV, "【各分析師】"]
    quiet = []
    for a in sorted(acc, key=lambda a: order.get(st(a), 9)):
        if st(a) == "未談BTC": quiet.append(xh_name(a["handle"])); continue
        L.append(f"・{xh_name(a['handle'])}｜{lab.get(a.get('stance'), a.get('stance', '-'))}" + (f"（{a['latest_post'][5:].replace('-', '/')}）" if a.get("latest_post") else "") + (f"：{_one(a)}" if _one(a) else ""))
    if quiet: L.append("・近期未談 BTC：" + "、".join(quiet))
    try:   # CryptoQuant top-5 BTC analysts (cq_consensus.py --kol, run as a script, reads the daily cache); optional
        if summary_opts().get("omit_cq"): raise RuntimeError("omit_cq")
        cq = subprocess.run([sys.executable, os.path.join(HERE, "cq_consensus.py"), "--kol"], cwd=HERE, capture_output=True, text=True, timeout=90).stdout.strip()
        if cq.startswith("【CryptoQuant"): L += ["", KOL_DIV] + cq.splitlines()
    except Exception as e: print("cq kol skipped:", e, file=sys.stderr)
    L += ["", "僅整理公開觀點，非投資建議"]
    return EMOJI.sub("", "\n".join(L))[:4900]
def summary_opts():
    """Persistent switches for /btc 持倉摘要. Defaults follow the 11/17 cuts; file can flip a flag back."""
    d = {"omit_retail": True, "omit_bitfinex": True, "omit_consensus": True,
         "omit_binance_block": True, "omit_structure": True, "omit_price_source_text": True,
         "omit_okx": False, "omit_cq": False}   # omit_okx／omit_cq：授權移除開關（2026-11-26），預設關
    path = os.path.join(HERE, "btc_summary.json")
    try:
        if os.path.isfile(path):
            raw = json.loads(open(path, encoding="utf-8").read())
            if isinstance(raw, dict):
                d.update({k: bool(raw[k]) for k in d if k in raw})
    except Exception as e:
        print("summary_opts:", e, file=sys.stderr)
    for k, env in (("omit_okx", "BTC_OMIT_OKX"), ("omit_cq", "BTC_OMIT_CQ")):
        v = os.environ.get(env)
        if v not in (None, ""): d[k] = v.strip().lower() in ("1", "true", "yes", "on")
    return d

def _drop_section(text, title_pat):
    return re.sub(r"\n*" + re.escape(DIV) + r"\n" + title_pat + r".*?(?=\n\n" + re.escape(DIV) + r"\n|\Z)", "", text, flags=re.S).rstrip()

def report_when_line(when, px):
    """2026.11.17 台北時間 07:31｜BTC 65,197 — no (UTC+8)."""
    return f"{when.strftime('%Y.%m.%d')} 台北時間 {when.strftime('%H:%M')}｜{px}"

def sync_forecast_header(fc, text):
    """Copy the summary time/price line onto the forecast header."""
    if not fc or not text: return fc
    Lsum, Lfc = text.splitlines(), fc.splitlines()
    if len(Lsum) > 1 and len(Lfc) > 1:
        Lfc[1] = Lsum[1]
        return "\n".join(Lfc)
    return fc

def summary(j, when):
    g = j.get
    px = f"BTC {g('btc'):,.0f}" if g("btc") else "BTC -"
    def sec(*lines): return ["", DIV, *lines]
    L = ["BTC 持倉數據", report_when_line(when, px)]
    # Lead sections, in order: 1 分析師觀點, 2 持倉變化; then the positioning sections.
    for lead in (change_section,):   # analyst views moved to /analyst (btc/kol_latest.json)
        L += lead(sec)
    # 【大戶】 skeleton (add_rvw fills 大戶持倉／大戶帳戶). 散戶／全體帳戶／巨鯨散戶差已關。
    L += sec("【大戶】",
             "・大戶持倉：",
             "・大戶帳戶：",
             "・定義：Binance 保證金前 20%；Gate/Bitget 未公開。" if summary_opts().get("omit_okx")
             else "・定義：Binance 保證金前 20%；OKX 持倉前 5%；Gate/Bitget 未公開。")
    def oi(name, k):
        c = g(k.replace("_usd", "_24h_pct"))
        return f"{name} {busd(g(k))}" + (f" ({pct(c)} 24h)" if c is not None else "")
    oi_parts = []
    if g("binance_未平倉_usd") is not None: oi_parts.append(oi("Binance", "binance_未平倉_usd"))
    no_okx = summary_opts().get("omit_okx")
    oi_parts += [oi("Gate", "未平倉_gate_usd")] + ([] if no_okx else [oi("OKX", "未平倉_okx_usd")])
    L += sec("【未平倉量】", "・" + "｜".join(oi_parts))
    if no_okx:
        if g("資金費率_gate_pct") is not None:
            L += sec("【資金費率】", f"・Gate 每 8h {pct(g('資金費率_gate_pct'),4)}")
    else:
        L += sec("【資金費率】",
                 f"・每 8h：OKX {pct(g('資金費率_okx_8h_pct'),4)}｜Gate {pct(g('資金費率_gate_pct'),4)}")
    opt = summary_opts()
    if not opt.get("omit_bitfinex"):
        L += sec("【Bitfinex 槓桿】",
                 f"・{pct(g('多空差值_bitfinex_pct'),2)}｜多 {g('bitfinex_多_btc') or 0:,.0f}／空 {g('bitfinex_空_btc') or 0:,.0f} BTC")
    ah = g('ahr999')
    zone = "" if ah is None else ("｜抄底區（<0.45）" if ah < 0.45 else "｜定投區（0.45–1.2）" if ah <= 1.2 else "｜高於定投線（>1.2）")
    L += sec("【Ahr999】", f"・{f1(ah,2,False)}{zone}")
    if g("binance_多空差值_大戶持倉") is not None and not summary_opts().get("omit_binance_block"):
        L += sec("【Binance】",
                 f"・全部 {pct(g('binance_多空差值_全部'))}｜大戶持倉 {pct(g('binance_多空差值_大戶持倉'))}",
                 f"・巨鯨vs散戶 {f1(g('binance_巨鯨散戶差'),2)}")
    t = EMOJI.sub("", "\n".join(L))[:4900]
    assert not EMOJI.search(t)
    return t
def publish_kol_dry(when, stamp, out_dir):
    """--kol-only --dry-run: same render + JPEG/preview + kol_text as publish_kol, written to out_dir only.
    No GitHub upload, no kol_latest.json change, no LINE."""
    os.makedirs(out_dir, exist_ok=True)
    r = subprocess.run([os.path.join(HERE, ".venv/bin/python"), os.path.join(HERE, "kol_chart.py"),
                        "--out-prefix", os.path.join(out_dir, f"btc_kol_{stamp}")],
                       cwd=HERE, capture_output=True, text=True, timeout=180)
    if r.stderr: print(r.stderr.rstrip(), file=sys.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"kol_chart exit {r.returncode}")
    paths = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().endswith(".png")]
    imgs = []
    for i, png in enumerate(paths, 1):
        ki = Image.open(png).convert("RGB")
        kb = os.path.join(out_dir, f"p{i}.jpg"); ki.save(kb, "JPEG", quality=90, optimize=True)
        kq = ki.copy(); kq.thumbnail((1024, 1024)); kpv = os.path.join(out_dir, f"p{i}_preview.jpg"); kq.save(kpv, "JPEG", quality=85, optimize=True)
        ph = ki.copy(); ph = ph.resize((1080, round(ki.size[1] * 1080 / ki.size[0])), Image.Resampling.LANCZOS)
        kph = os.path.join(out_dir, f"p{i}_phone.jpg"); ph.save(kph, "JPEG", quality=88, optimize=True)
        imgs.append({"page": i, "image": kb, "preview": kpv, "phone": kph})
    txt = kol_text()
    open(os.path.join(out_dir, "kol_text.txt"), "w", encoding="utf-8").write(txt)
    k = {"date": when.strftime("%Y-%m-%d %H:%M"), "kol_text": txt, "kol_images": imgs, "dry_run": True}
    open(os.path.join(out_dir, "kol_latest.preview.json"), "w", encoding="utf-8").write(json.dumps(k, ensure_ascii=False, indent=1))
    print("KOL_DRY_RUN", len(imgs), out_dir, file=sys.stderr)
    return imgs
def publish_kol(when, stamp, require=False):
    """Analyst image(s) + btc/kol_latest.json only. Never touches btc/latest.json.
    Phone layout may yield 2 PNGs. Worker still reads kol_image_url/kol_preview_url (page 1).
    Extra pages go to kol_image_url_2 / kol_images (forward-compatible). On render/layout/overlap
    failure: do not upload; keep previous kol_latest. If require=True ( --kol-only ), re-raise/exit."""
    os.makedirs(os.path.join(HERE, "out/publish"), exist_ok=True)
    try:
        r = subprocess.run([os.path.join(HERE, ".venv/bin/python"), os.path.join(HERE, "kol_chart.py")],
                           cwd=HERE, capture_output=True, text=True, timeout=180)
        if r.stderr:
            print(r.stderr.rstrip(), file=sys.stderr)
        if r.returncode != 0:
            raise RuntimeError(f"kol_chart exit {r.returncode} (layout/overlap failed; kept previous kol_latest)")
        paths = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().endswith(".png")]
        if not paths:
            raise RuntimeError("kol_chart produced no PNG paths")
        uploaded = []
        for i, png in enumerate(paths, 1):
            ki = Image.open(png).convert("RGB")
            tag = f"_p{i}" if len(paths) > 1 else ""
            kb = os.path.join(HERE, f"out/publish/btc_kol_{stamp}{tag}.jpg")
            ki.save(kb, "JPEG", quality=90, optimize=True)
            kq = ki.copy(); kq.thumbnail((1024, 1024))
            kpv = kb[:-4] + "_preview.jpg"; kq.save(kpv, "JPEG", quality=85, optimize=True)
            m1 = "btc/" + os.path.basename(kb); m2 = "btc/" + os.path.basename(kpv)
            u1 = pinned(gh_put(m1, open(kb, "rb").read(), "btc kol " + stamp + tag), m1)
            u2 = pinned(gh_put(m2, open(kpv, "rb").read(), "btc kol preview " + stamp + tag), m2)
            uploaded.append({"page": i, "image_url": u1, "preview_url": u2, "local_jpg": kb, "local_png": png})
        # Worker-compatible primary fields = page 1; extras for multi-image clients
        k = {"date": when.strftime("%Y-%m-%d %H:%M"), "kol_text": kol_text(),
             "kol_image_url": uploaded[0]["image_url"],
             "kol_preview_url": uploaded[0]["preview_url"],
             "kol_images": [{"image_url": u["image_url"], "preview_url": u["preview_url"], "page": u["page"]} for u in uploaded]}
        if len(uploaded) > 1:
            k["kol_image_url_2"] = uploaded[1]["image_url"]
            k["kol_preview_url_2"] = uploaded[1]["preview_url"]
        gh_put("btc/kol_latest.json", json.dumps(k, ensure_ascii=False, indent=1).encode(), "btc kol latest " + stamp)
        print("KOL_IMAGES", len(uploaded), *[u["local_jpg"] for u in uploaded], file=sys.stderr)
        return uploaded
    except Exception as e:
        print("kol image skipped:", e, file=sys.stderr)
        if require:
            raise
        return None
def add_structure(text):
    """Insert the objective structure section (btc_structure.py, run as a script) before 【Binance…】, else at the end.
    If omit_structure, drop any existing block and do not insert.
    Title 【技術結構】 (old title 【技術結構（客觀數據，非投資建議）】 also recognised): any existing structure block is
    replaced by the fresh one, so it never appears twice. On any failure/timeout the text is returned unchanged."""
    if summary_opts().get("omit_structure"):
        return _drop_section(text, r"【技術結構(?:】|（客觀數據，非投資建議）】)")
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
    if summary_opts().get("omit_consensus") or summary_opts().get("omit_cq"):
        return _drop_section(text, r"【分析師共識】")
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
def _short_model_script():
    for rel in ("short_model.py", os.path.join("scripts", "short_model.py")):
        path = os.path.join(HERE, rel)
        if os.path.isfile(path):
            return path
    return None

def _short_model_out():
    return os.path.join(HERE, "out", "short_model")

# 這次執行短線模型的結果：ok=False 時發布會沿用舊短線段，必須寫 log、在輸出標記（不能默默發出去）
SHORT_RUN = {"ok": None, "reason": "", "warns": [], "fallback": ""}
SHORT_FALLBACK_LOG = os.path.join(HERE, "logs", "short_fallback.log")
def _short_fail(reason):
    SHORT_RUN.update(ok=False, reason=(reason or "").strip()[-300:])
def short_fallback_note(sp_path):
    """沿用舊段時的說明：舊段是哪個時間產生的、為什麼沒更新。"""
    if os.path.isfile(sp_path):
        age = datetime.fromtimestamp(os.path.getmtime(sp_path)).strftime("%Y-%m-%d %H:%M")
        return f"短線模型這次失敗，發布沿用 {age} 產生的舊短線段；原因：{SHORT_RUN['reason'] or '未知'}"
    return f"短線模型這次失敗，也沒有舊的短線段檔，沿用預測文原本的短線段；原因：{SHORT_RUN['reason'] or '未知'}"
def report_short_status(stamp, dry):
    """把短線 WARN 與沿用舊段印到 stdout 並寫 log；沒事就不輸出。"""
    lines = [f"SHORT_WARN {w}" for w in SHORT_RUN["warns"]]
    if SHORT_RUN["fallback"]:
        lines.append("SHORT_FALLBACK " + SHORT_RUN["fallback"])
    if not lines:
        return
    for ln in lines:
        print(ln)
        print(ln, file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(SHORT_FALLBACK_LOG), exist_ok=True)
        with open(SHORT_FALLBACK_LOG, "a", encoding="utf-8") as fh:
            for ln in lines:
                fh.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')} stamp={stamp}{' dry-run' if dry else ''} {ln}\n")
    except OSError as e:
        print("short_fallback log failed:", e, file=sys.stderr)
def run_short_model(price=None, when=None):
    """Run short_model.py as a script (never import). Returns the output dir or None.
    price／when 為發布標題的統一現價與時間，傳給 --price／--when，不再讓短線自己抓價。"""
    script = _short_model_script()
    if not script:
        print("short_model skipped: script not found", file=sys.stderr)
        _short_fail("找不到 short_model.py")
        return None
    out = _short_model_out()
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, script, "--out", out]
    if price is not None:
        cmd += ["--price", str(price)]
    if when is not None:
        ts = when.strftime("%Y-%m-%d %H:%M") if hasattr(when, "strftime") else str(when)
        cmd += ["--when", ts]
    try:
        r = subprocess.run(cmd, cwd=HERE,
                           capture_output=True, text=True, timeout=90)
        if r.stderr:
            print(r.stderr.rstrip(), file=sys.stderr)
        SHORT_RUN["warns"] = [ln.split("WARN short_model:", 1)[1].strip() for ln in (r.stderr or "").splitlines() if "WARN short_model:" in ln]
        if r.returncode != 0:
            print("short_model failed:", (r.stderr or r.stdout or "")[-500:], file=sys.stderr)
            _short_fail(f"exit {r.returncode}：" + " ".join(ln for ln in (r.stderr or r.stdout or "").splitlines() if "WARN short_model:" not in ln)[-250:])
            return None
        SHORT_RUN.update(ok=True, reason="")
        return out
    except Exception as e:
        print("short_model skipped:", e, file=sys.stderr)
        _short_fail(f"{type(e).__name__}: {e}")
        return None


def _midlong_voice_config_path():
    """和 publish_btc.py 同目錄的 midlong_voice.json（上線持久開關）；其次才看 data/。"""
    for rel in ("midlong_voice.json", os.path.join("data", "midlong_voice.json")):
        path = os.path.join(HERE, rel)
        if os.path.isfile(path):
            return path
    return os.path.join(HERE, "midlong_voice.json")

def midlong_voice_on():
    """是否用 midlong_model.py 取代【中期】【長期】。
    持久設定讀 midlong_voice.json 的 enabled。環境變數 MIDLONG_VOICE 若有設，暫時覆蓋。"""
    env = os.environ.get("MIDLONG_VOICE")
    if env is not None and str(env).strip() != "":
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    path = _midlong_voice_config_path()
    try:
        d = json.loads(open(path, encoding="utf-8").read())
        return bool(d.get("enabled"))
    except Exception:
        return False

def _midlong_model_script():
    for rel in (os.path.join("scripts", "midlong_model.py"), "midlong_model.py"):
        path = os.path.join(HERE, rel)
        if os.path.isfile(path):
            return path
    return None

def _midlong_model_out():
    return os.path.join(HERE, "out", "midlong")

def run_midlong_model(price=None):
    """Run midlong_model.py as a script (never import). Returns the output dir or None.
    price 為發布標題的統一現價，傳給 --price，不用 K 線快取裡的現價。"""
    script = _midlong_model_script()
    if not script:
        print("midlong_model skipped: script not found", file=sys.stderr)
        return None
    out = _midlong_model_out()
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, script, "--out", out]
    if price is not None:
        cmd += ["--price", str(price)]
    try:
        r = subprocess.run(cmd, cwd=HERE,
                           capture_output=True, text=True, timeout=90)
        if r.stderr:
            print(r.stderr.rstrip(), file=sys.stderr)
        if r.returncode != 0:
            print("midlong_model failed:", (r.stderr or r.stdout or "")[-500:], file=sys.stderr)
            return None
        return out
    except Exception as e:
        print("midlong_model skipped:", e, file=sys.stderr)
        return None

def fetch_levels(price=None, when=None, reuse=False):
    """short_model.py --levels first (script, never import); fall back to fengge_section.py --levels.
    reuse=True：直接讀剛剛 run_short_model 寫好的 levels.json，不再重跑。"""
    outdir = _short_model_out()
    lv_path = os.path.join(outdir, "levels.json")
    if reuse and os.path.isfile(lv_path):
        try:
            return json.loads(open(lv_path, encoding="utf-8").read())
        except Exception:
            pass
    try:
        script = _short_model_script()
        if script:
            cmd = [sys.executable, script, "--levels", "--out", outdir]
            if price is not None:
                cmd += ["--price", str(price)]
            if when is not None:
                ts = when.strftime("%Y-%m-%d %H:%M") if hasattr(when, "strftime") else str(when)
                cmd += ["--when", ts]
            out = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=90)
            if out.stderr:
                print(out.stderr.rstrip(), file=sys.stderr)
            text = (out.stdout or "").strip()
            for ln in reversed(text.splitlines()):
                if ln.startswith("{"):
                    return json.loads(ln)
            lv_path = os.path.join(outdir, "levels.json")
            if os.path.isfile(lv_path):
                return json.loads(open(lv_path).read())
    except Exception as e:
        print("short_model levels skipped:", e, file=sys.stderr)
    try:
        cmd = [sys.executable, os.path.join(HERE, "fengge_section.py"), "--levels"]
        if price is not None: cmd += ["--price", str(int(float(str(price).replace(",", ""))))]
        out = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=60).stdout.strip()
        for ln in reversed(out.splitlines()):
            if ln.startswith("{"): return json.loads(ln)
    except Exception as e:
        print("levels skipped:", e, file=sys.stderr)
    return None
def align_levels(text, lv):
    """Patch 摘要短線方向 + 解讀 short-term clause + 支撐｜壓力 so they match short_bias (lv from fetch_levels)."""
    if not text or not lv: return text
    bias = lv.get("bias") or ""
    watch = lv.get("watch") or ""
    sp = lv.get("support_pressure") or ""
    # 摘要 first line: BTC X，短線震盪/偏多/偏空 → same bias as forecast
    if bias:
        text = re.sub(r"(BTC [\d,]+(?:\.\d+)?，)短線(?:震盪|偏多|偏空|觀望)", r"\1短線" + bias, text, count=1)
        text = re.sub(r"(BTC [\d,]+(?:\.\d+)?，[^\n]*?)走勢偏[多空]", r"\1短線" + bias, text, count=1)
    if watch:
        def _jiedu(m):
            pre = m.group(1).rstrip("；。，、 \t")
            body = (pre + "；" if pre else "") + watch
            if not body.endswith("。"): body += "。"
            return "解讀：" + body
        # Match 短線仍偏 / 短線偏 / 短線傾向偏 (agent prose varies)
        text = re.sub(r"解讀：(.*?)短線(?:仍|傾向)?(?:偏[^\n]*|觀望[^\n]*)", _jiedu, text)
    if sp:
        text = re.sub(r"(?m)^(?:支撐\s+[^\n]*｜壓力\s+[^\n]*|壓力\s+[^\n]*｜支撐\s+[^\n]*)$", sp, text)
    return text
def align_forecast(fc, lv):
    """Force forecast 【短線】失效 line to lv['forecast_invalid'] (same short_bias as 摘要/數據)."""
    if not fc or not lv or not lv.get("forecast_invalid"): return fc
    return re.sub(
        r"(【短線】(?:數天)?\n方向：偏[多空]｜目標 [^｜\n]+｜失效：)[^\n]+",
        lambda m: m.group(1) + lv["forecast_invalid"],
        fc, count=1)
SUMMARY_SEND = os.path.join(HERE, "out/publish/summary_send.txt")
LEAD_SEND = os.path.join(HERE, "out/publish/lead_send.txt")
FORECAST_IN_SUMMARY = False   # Worker sends forecast_parts as separate messages (2026-10-24 17:44)
FORECAST_FILE = os.path.join(HERE, "out/publish/forecast_send.txt")

MODEL_RE = r"\n*" + re.escape(DIV) + r"\n【(?:短線)?交易模型】.*?(?=\n\n" + re.escape(DIV) + r"\n|\Z)"
def strip_model(text):
    """Drop 【交易模型】 from the data message (it now lives in forecast_text)."""
    return re.sub(MODEL_RE, "", text, flags=re.S).rstrip()
def build_forecast(text, price=None, when=None):
    """BTC 走勢預測 (fengge_section.py --forecast, run as a script; Daniel 2026-10-24 16:58). "" on failure.
    The time line reuses the summary's timestamp so the Worker footer time is identical.
    price／when 傳給 midlong（與已寫好的 short 段），與標題現價同一份。"""
    try:
        head = "\n".join(text.splitlines()[:3])
        m = re.search(r"｜BTC ([\d,]+(?:\.\d+)?)", head)
        cmd = [sys.executable, os.path.join(HERE, "fengge_section.py"), "--forecast-compact"] + (["--price", m.group(1).replace(",", "")] if m else [])
        fc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=60).stdout.strip()
        if not fc.startswith("BTC 走勢預測"): return ""
        ts = re.search(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}", text)
        if ts: fc = re.sub(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}", ts.group(0), fc, count=1)
        # 走勢預測「短線」改用 short_model.py；中期、長期由 midlong_voice.json（enabled）控制，環境變數可暫時覆蓋
        short = None
        sm_out = _short_model_out()
        sp = os.path.join(sm_out, "forecast_short.txt")
        if not os.path.isfile(sp) and SHORT_RUN["ok"] is None:
            run_short_model(price=price, when=when)
        if SHORT_RUN["ok"] is False:
            SHORT_RUN["fallback"] = short_fallback_note(sp)
        if os.path.isfile(sp):
            short = open(sp).read().strip()
        if short and short.startswith("【短線】"):
            fc, n = re.subn(r"【短線】(?:數天)?\n.*?(?=\n【中期】)", short.rstrip() + "\n", fc, count=1, flags=re.S)
            if not n:
                print("short_model splice: 找不到【短線】段", file=sys.stderr)
        else:
            print("short_model forecast not used; kept previous short section", file=sys.stderr)
        # 中期、長期：midlong_voice.json enabled 或 MIDLONG_VOICE=1 才改用 midlong_model.py
        if midlong_voice_on():
            ml_out = run_midlong_model(price=price)
            mp = os.path.join(ml_out or _midlong_model_out(), "forecast_midlong.txt")
            midlong = open(mp).read().strip() if os.path.isfile(mp) else ""
            if midlong.startswith("【中期】"):
                fc2, n2 = re.subn(r"【中期】\s*.*\Z", midlong.rstrip() + "\n", fc, count=1, flags=re.S)
                if n2:
                    fc = fc2
                else:
                    print("midlong_model splice: 找不到【中期】段", file=sys.stderr)
            else:
                print("midlong_model forecast not used; kept previous mid/long section", file=sys.stderr)
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
    if summary_opts().get("omit_binance_block"):
        return _drop_section(text, r"【Binance[^\n]*】")
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
    Any existing 【多空比】 block is replaced, so it appears once. On failure the text is returned unchanged.
    If omit_retail, drop the section (大戶百分比已在【大戶】，不再寫一份比值)."""
    if summary_opts().get("omit_retail"):
        return _drop_section(text, r"【多空比】")
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
            keys = ("・大戶持倉：", "・大戶帳戶：") if summary_opts().get("omit_retail") else ("・全體帳戶：", "・大戶持倉：", "・大戶帳戶：", "・巨鯨散戶差：")
            for k in keys:
                if ln.startswith(k): new[k] = ln
        if not new: return text
        L = text.splitlines(); inblk = False; res = []
        for ln in L:
            if ln.startswith("【"): inblk = ln.startswith("【散戶 vs 大戶】") or ln.startswith("【大戶】")
            if inblk and ln.startswith(RVW_KEYS):
                if summary_opts().get("omit_retail") and ln.startswith(("・全體帳戶：", "・OKX 全體", "・全體：", "・巨鯨散戶差")):
                    continue
                k = "・全體帳戶：" if ln.startswith(("・全體帳戶：", "・OKX 全體", "・全體：")) else "・巨鯨散戶差：" if ln.startswith("・巨鯨散戶差") else ln[:6]
                if k in new:
                    m = re.search(r"（[^）]*）\s*$", ln) if k == "・巨鯨散戶差：" else None
                    res.append(new.pop(k) + (m.group(0) if m else "")); continue
            res.append(ln)
        return "\n".join(res)[:4900]
    except Exception as e:
        print("rvw skipped:", e, file=sys.stderr); return text
def main():
    a = argparse.ArgumentParser(); a.add_argument("--image"); a.add_argument("--json"); a.add_argument("--text"); a.add_argument("--dry-run", action="store_true"); a.add_argument("--kol-text", action="store_true", help="print /btckol text only"); a.add_argument("--kol-only", action="store_true", help="only analyst image + btc/kol_latest.json"); a.add_argument("--out-dir", help="with --kol-only --dry-run: local output folder"); a = a.parse_args()
    if a.kol_text: print(kol_text()); return   # print /btckol text only, no upload
    if a.kol_only and a.dry_run:   # local trial: render + text only, no upload
        now = datetime.now(); stamp = now.strftime("%Y%m%d_%H%M")
        publish_kol_dry(now, stamp, a.out_dir or os.path.join(HERE, "out/kol_dry")); return
    if a.kol_only:
        now = datetime.now(); stamp = now.strftime("%Y%m%d_%H%M")
        uploaded = publish_kol(now, stamp, require=True)
        if not uploaded:
            sys.exit("kol publish failed; previous kol_latest kept")
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
    text = strip_price_source(apply_chainlink(text, j.get("btc") if j else None))
    text = add_notes(strip_hl(add_binance(add_fengge(add_consensus(add_longshort(add_rvw(add_range(add_structure(text)))))))))
    # 統一現價：標題（Chainlink 檢查後）的那一個數字與時間，傳給短線／中長期
    _pm = re.search(r"｜BTC ([\d,]+(?:\.\d+)?)", "\n".join(text.splitlines()[:3]))
    spot = None
    if PRICE.get("new"):
        try:
            spot = float(str(PRICE["new"]).replace(",", ""))
        except ValueError:
            spot = None
    if spot is None and _pm:
        spot = float(_pm.group(1).replace(",", ""))
    if spot is None and j.get("btc") is not None:
        spot = float(j.get("btc"))
    run_short_model(price=spot, when=when)
    lv = fetch_levels(spot, when=when, reuse=True)
    if lv: print("LEVELS", json.dumps({k: lv[k] for k in ("bias","targets","inv","nxt","forecast_invalid","support_pressure") if k in lv}, ensure_ascii=False), file=sys.stderr)
    text = align_levels(text, lv)
    fc = build_forecast(text, price=spot, when=when)
    if fc: fc = align_forecast(fc, lv)
    # 短線改版後不在預測文加「現價來源」，避免出現來源名字
    if fc:
        fc = strip_price_source(sync_forecast_header(fc, text))
    if fc: text = strip_model(text)
    lead = align_levels(lead_text(), lv)
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
    # Always overwrite send files (Daniel batch 2026-10-30) — even on --dry-run so trials can inspect
    with open(FORECAST_FILE, "w") as fh: fh.write(fc)
    with open(SUMMARY_SEND, "w") as fh: fh.write(text)
    with open(LEAD_SEND, "w") as fh: fh.write(lead)
    fparts = write_forecast_parts(fc)
    merged = merge_summary(fc, text) != text
    print(text)
    report_short_status(stamp, a.dry_run)
    print("FORECAST_FILE", FORECAST_FILE, len(fc), "parts", [len(x) for x in fparts], "merged_into_summary", merged, file=sys.stderr)
    print("SEND_FILES", SUMMARY_SEND, LEAD_SEND, FORECAST_FILE, file=sys.stderr)
    if a.dry_run: return
    n1 = "btc/" + os.path.basename(big); n2 = "btc/" + os.path.basename(pre)
    u1 = pinned(gh_put(n1, open(big, "rb").read(), "btc chart " + stamp), n1)
    u2 = pinned(gh_put(n2, open(pre, "rb").read(), "btc preview " + stamp), n2)
    d = {"date": when.strftime("%Y-%m-%d %H:%M"), "summary_text": merge_summary(fc, text), "forecast_text": fc, "forecast_parts": fparts, "forecast_merged": merged, "change_text": change_text(), "lead_text": lead, "image_url": u1, "preview_url": u2, "price_source": PRICE.get("source", "")}
    publish_kol(when, stamp)
    gh_put("btc/latest.json", json.dumps(d, ensure_ascii=False, indent=1).encode(), "btc latest " + stamp)
    print("PUBLISHED", u1, u2, "GHR", GHR, file=sys.stderr)
    try: prune()
    except Exception as e: print("prune fail", e, file=sys.stderr)
if __name__ == "__main__": main()

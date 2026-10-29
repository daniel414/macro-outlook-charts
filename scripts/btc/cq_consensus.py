#!/usr/bin/env python3
"""CryptoQuant Analyst Consensus numbers (consensus.cryptoquant.com, public key-free API) for /btc and /btckol.
  cq_consensus.py --fetch          # fetch now -> out/cq_consensus.json (BTC index + top-5 analysts' score and newest BTC call)
  cq_consensus.py --line           # /btc section 【分析師共識】; refetches only if cache > MAX_AGE_H old (= once a day)
  cq_consensus.py --kol            # /btckol block 【CryptoQuant 前 5 名分析師】 (numbers + our take only)
We only publish the public numbers (σ, bullish %, analyst count, date, stance, score) plus OUR OWN interpretation.
Never paste or closely paraphrase CryptoQuant analyst post bodies / titles. Source line only: 資料來源：CryptoQuant 公開共識頁.
Optional our takes: out/cq_views_zh.json {handle: {"link": url, "zh": "我們判讀…"}}; used only while link matches
the newest call. Free data only, no key."""
import html, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "out/cq_consensus.json"); ZH = os.path.join(HERE, "out/cq_views_zh.json")
BASE = "https://consensus.cryptoquant.com"
TOP5 = ["benjamincowen", "IT_Tech_PL", "jjcmoreno", "10xresearch", "DonAlt"]
MAX_AGE_H = 24; HARD_MAX_H = 72
HOT, HOT2, COLD = 0.8, 1.5, -1.5
TW = timezone(timedelta(hours=8))
SRC = "資料來源：CryptoQuant 公開共識頁"
def get(url, js=True):
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 btc-sentiment"}), timeout=30).read().decode()
    return json.loads(r) if js else r
def _js(p):
    try: return json.loads('"' + p + '"')
    except Exception: return p.encode().decode("unicode_escape", "ignore")
def latest_call(page_url):
    pg = get(page_url, js=False)
    t = "".join(_js(p) for p in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', pg, re.S))
    i = t.find('"calls":[{'); best = None
    for m in re.finditer(r'\{"key":"[^"]*","ids":\[[^\]]*\],"source":"[^"]*","link":"([^"]+)","title":(null|"(?:[^"\\]|\\.)*"),"body":(null|"(?:[^"\\]|\\.)*").{0,4000}?"publishedAt":"([^"]+)","labels":(\[[^\]]*\])', t[max(i, 0):], re.S):
        try: lab = [l for l in json.loads(m.group(5)) if l.get("asset") == "BTC" and l.get("sentiment") in ("bullish", "bearish", "bullish_nuance", "bearish_nuance")]
        except Exception: continue
        if not lab: continue
        body = json.loads(m.group(3)) if m.group(3) != "null" else (json.loads(m.group(2)) if m.group(2) != "null" else "")
        # excerpt kept in cache for internal use only; never printed by line()/kol()
        c = {"link": m.group(1), "published_at": m.group(4), "stance": lab[0]["sentiment"], "excerpt": html.unescape(re.sub(r"\s+", " ", body or "")).strip()[:200]}
        if not best or c["published_at"] > best["published_at"]: best = c
    return best
def fetch():
    c = get(BASE + "/api/v1/consensus?asset=BTC")
    out = {"fetched_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M"), "source": BASE + "/consensus/btc", "consensus": {k: c.get(k) for k in (
        "date", "consensus_index", "consensus_index_30d_ma", "z_score", "regime", "z_score_7d", "bullish_percent", "bearish_percent", "total_analysts", "as_of")}, "analysts": []}
    for h in TOP5:
        try:
            a = get(f"{BASE}/api/v1/analysts/{h}?asset=BTC"); tr = a.get("track_record") or {}; tier = tr.get("tier") or {}
            row = {"handle": h, "name": a.get("name"), "profile_url": a.get("profile_url"), "score": tr.get("accuracy_score"), "balanced": tr.get("balanced_score"),
                   "n": tr.get("accuracy_sample_size"), "rank": (tr.get("overall_rank") or {}).get("rank"), "of": (tr.get("overall_rank") or {}).get("total"), "badge": tier.get("label")}
            try: row["latest"] = latest_call(a.get("profile_url") or f"{BASE}/analyst/{h}")
            except Exception as e: row["latest"] = None; print("call fail", h, e, file=sys.stderr)
            out["analysts"].append(row)
        except Exception as e: print("analyst fail", h, e, file=sys.stderr)
        time.sleep(1)
    if c.get("z_score") is None: raise Exception("no z_score")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    if os.path.exists(CACHE): os.replace(CACHE, CACHE + ".prev")
    json.dump(out, open(CACHE + ".tmp", "w"), ensure_ascii=False, indent=1); os.replace(CACHE + ".tmp", CACHE)
    return out
def load(auto=True):
    age = (time.time() - os.path.getmtime(CACHE)) / 3600 if os.path.exists(CACHE) else 1e9
    if auto and age > MAX_AGE_H:
        try: return fetch()
        except Exception as e: print("fetch fail", e, file=sys.stderr)
    if age > HARD_MAX_H: return None
    return json.load(open(CACHE))
def verdict(z):
    """Our own reading of σ; not CryptoQuant site copy."""
    if z >= HOT2:
        return "市場分析師整體一面倒偏多，歷史上這種程度常伴隨短線過熱，留意回檔風險"
    if z >= HOT:
        return "市場分析師整體明顯偏多，歷史上這種程度常伴隨短線過熱，留意回檔"
    if z <= COLD:
        return "市場分析師整體極度偏空，歷史上這種程度常出現過度悲觀，逆向偏多訊號"
    return "市場分析師多空分歧大致平衡，未見明顯過熱或極度悲觀"
def stance_take(stance):
    """Fallback our take from stance label only — never quote post text."""
    return {
        "bullish": "我們判讀：立場看多",
        "bearish": "我們判讀：立場看空",
        "bullish_nuance": "我們判讀：立場偏多",
        "bearish_nuance": "我們判讀：立場偏空",
    }.get(stance, "我們判讀：立場不明")
def line():
    d = load()
    if not d: return ""
    c = d["consensus"]; z = c["z_score"]; dt = (c.get("date") or "")[5:].replace("-", "/")
    return "\n".join(["【分析師共識】",
        f"・共識指數 σ {z:+.2f}（{dt}）｜看多 {c.get('bullish_percent')}%｜{c.get('total_analysts')} 位分析師",
        f"・{verdict(z)}",
        f"・{SRC}"])
def kol():
    d = load()
    if not d or not d.get("analysts"): return ""
    try: zh = json.load(open(ZH))
    except Exception: zh = {}
    try: mine = {x["handle"].lower() for x in json.load(open(os.path.join(HERE, "kol/latest_view.json")))["accounts"]}   # read-only, dedupe
    except Exception: mine = set()
    L = ["【CryptoQuant 前 5 名分析師】",
         f"・分數為準確度分數（0–100，相對排名），不是勝率；{SRC}"]
    for a in d["analysts"]:
        la = a.get("latest") or {}; s = {"bullish": "看多", "bearish": "看空", "bullish_nuance": "偏多", "bearish_nuance": "偏空"}.get(la.get("stance"), "-")
        dt = la.get("published_at", "")[5:10].replace("-", "/")
        if a["handle"].lower() in mine:
            L.append(f"・{a['handle']}｜CQ 分數 {a.get('score')}｜已在追蹤名單，觀點見【各分析師】"); continue
        z = zh.get(a["handle"]) or {}
        # only our own summary in zh (must start with 我們判讀); never English excerpt / verbatim translation
        zh_ok = z.get("zh") and z.get("link") == la.get("link") and str(z.get("zh")).startswith("我們判讀")
        v = z["zh"] if zh_ok else stance_take(la.get("stance"))
        L.append(f"・{a['handle']}｜CQ 分數 {a.get('score')}｜{s}（{dt}）｜{v}")
    return "\n".join(L)
if __name__ == "__main__":
    if "--fetch" in sys.argv: print(json.dumps(fetch(), ensure_ascii=False)[:400])
    elif "--kol" in sys.argv: print(kol())
    else: print(line())

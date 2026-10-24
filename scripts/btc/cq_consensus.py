#!/usr/bin/env python3
"""CryptoQuant Analyst Consensus (consensus.cryptoquant.com, public key-free API) for /btc and /btckol.
  cq_consensus.py --fetch          # fetch now -> out/cq_consensus.json (BTC index + top-5 analysts' score and newest BTC call)
  cq_consensus.py --line           # /btc section 【分析師共識】 (2 lines); refetches only if cache > MAX_AGE_H old (= once a day)
  cq_consensus.py --kol            # /btckol block 【CryptoQuant 前 5 名分析師】
Contrarian use of σ (z_score = 30d MA of the index vs its 90-day norm; the site colours z>=+0.8 bullish, z<=-1.5 bearish):
  σ >= +1.5 明顯過熱 / >= +0.8 偏熱 -> 提醒可能太樂觀；σ <= -1.5 -> 共識極度悲觀（反向偏多）；otherwise 中性.
Chinese one-line views for the 5 analysts are optional: out/cq_views_zh.json {handle: {"link": url, "zh": "..."}} written by the
btc-3 routine; used only while the link matches the newest call, else the English excerpt is shown. Free data only, no key."""
import html, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "out/cq_consensus.json"); ZH = os.path.join(HERE, "out/cq_views_zh.json")
BASE = "https://consensus.cryptoquant.com"
TOP5 = ["benjamincowen", "IT_Tech_PL", "jjcmoreno", "10xresearch", "DonAlt"]
MAX_AGE_H = 24; HARD_MAX_H = 72
HOT, HOT2, COLD = 0.8, 1.5, -1.5
TW = timezone(timedelta(hours=8))
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
    if z >= HOT2: return "明顯過熱：共識一面倒看多，反向指標提醒可能太樂觀"
    if z >= HOT: return "偏熱：共識偏樂觀，反向指標提醒留意過度樂觀"
    if z <= COLD: return "極度悲觀：共識一面倒看空，反向指標偏多"
    return "中性：共識未過熱也未極度悲觀"
def line():
    d = load()
    if not d: return ""
    c = d["consensus"]; z = c["z_score"]; dt = (c.get("date") or "")[5:].replace("-", "/")
    return "\n".join(["【分析師共識】",
        f"・CryptoQuant 共識指數 σ {z:+.2f}（{dt}）｜看多 {c.get('bullish_percent')}%｜{c.get('total_analysts')} 位分析師",
        f"・{verdict(z)}（σ≥+{HOT} 偏熱、≥+{HOT2} 過熱）"])
def kol():
    d = load()
    if not d or not d.get("analysts"): return ""
    try: zh = json.load(open(ZH))
    except Exception: zh = {}
    try: mine = {x["handle"].lower() for x in json.load(open(os.path.join(HERE, "kol/latest_view.json")))["accounts"]}   # read-only, dedupe
    except Exception: mine = set()
    L = ["【CryptoQuant 前 5 名分析師】", "・分數為 CryptoQuant 準確度分數（0–100，相對排名），不是勝率"]
    for a in d["analysts"]:
        la = a.get("latest") or {}; s = {"bullish": "看多", "bearish": "看空", "bullish_nuance": "偏多", "bearish_nuance": "偏空"}.get(la.get("stance"), "-")
        dt = la.get("published_at", "")[5:10].replace("-", "/")
        if a["handle"].lower() in mine:
            L.append(f"・{a['handle']}｜CQ 分數 {a.get('score')}｜已在追蹤名單，觀點見【各分析師】"); continue
        z = zh.get(a["handle"]) or {}
        v = z["zh"] if z.get("zh") and z.get("link") == la.get("link") else (la.get("excerpt") or "")[:90].rstrip() + ("…" if len(la.get("excerpt") or "") > 90 else "")
        L.append(f"・{a['handle']}｜CQ 分數 {a.get('score')}｜{s}（{dt}）｜{v}")
    return "\n".join(L)
if __name__ == "__main__":
    if "--fetch" in sys.argv: print(json.dumps(fetch(), ensure_ascii=False)[:400])
    elif "--kol" in sys.argv: print(kol())
    else: print(line())

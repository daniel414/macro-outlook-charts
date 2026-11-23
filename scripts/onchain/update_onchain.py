#!/usr/bin/env python3
"""LINE /btccheckonchain 正式入口（2026-11-17 起改用自有圖：onchain_six）。
資料只讀自家網站鏈上頁的公開免費資料檔，不再抓外部圖表 CDN；舊版（外部圖表＋訊號回測段）備份於
data/backups/onchain_launch_20261117/golive_1128/update_onchain.py。
產出：onchain_text.txt、onchain_summary.png（兩排總覽）、charts/（單張圖）、onchain_state.json（midlong 讀）、
<ROOT>/out/onchain_alert_state.json（內部通知用，不推播 LINE），並發布 btc/onchain_latest.json + jpg 到媒體 repo（保留最近 60 份）。
不 import publish_btc.py / short_model.py / midlong_model.py。
用法：update_onchain.py [--dry-run]"""
import argparse, base64, json, os, re, shutil, sys, time
from datetime import datetime
import requests
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
KEEP = 60
GHR = "daniel414/macro-outlook-charts"
try:
    m = re.search(r'GHR="([^"]+)"', open("/workspace/big-players/line_push.py").read()); GHR = m.group(1) if m else GHR
except Exception: pass
GHR = os.environ.get("GHR_OVERRIDE") or GHR
API = f"https://api.github.com/repos/{GHR}/contents/"
def H():
    t = os.environ.get("MEDIA_REPO_TOKEN") or os.environ["GH_CHARTS_TOKEN"]
    return {"Authorization": "Bearer " + t, "Accept": "application/vnd.github+json"}

def _api_err(kind, r):
    # 只留狀態與短訊息，避免把回應裡可能出現的敏感內容整段印出
    try:
        msg = r.json().get("message") or ""
    except Exception:
        msg = (r.text or "").strip().splitlines()[0] if r.text else ""
    msg = "".join(ch for ch in msg[:180] if ch.isprintable())
    return f"{kind} {r.status_code}" + (f": {msg}" if msg else "")

def gh_put(path, data, msg):
    g = requests.get(API + path, headers=H(), timeout=60)
    body = {"message": msg, "content": base64.b64encode(data).decode(), "branch": "main"}
    if g.status_code == 200: body["sha"] = g.json()["sha"]
    elif g.status_code != 404: raise Exception(_api_err("gh get", g))
    r = None
    for _ in range(3):
        r = requests.put(API + path, headers=H(), json=body, timeout=120)
        if r.status_code in (200, 201): return r.json()["commit"]["sha"]
        time.sleep(3)
    raise Exception(_api_err("gh put", r))
def pinned(sha, path):
    # 私有 repo：用 Contents API 驗證，寫入 raw sha 網址（Worker 會改寫成簽名 /img/ 網址）。
    raw = f"https://raw.githubusercontent.com/{GHR}/{sha}/{path}"
    for _ in range(3):
        try:
            r = requests.get(API + path, params={"ref": sha}, headers=H(), timeout=60)
            if r.status_code == 200 and r.json().get("type") == "file" and r.json().get("size", 0) > 0: return raw
            print("pinned check", path, r.status_code, file=sys.stderr)
        except Exception as e: print("pinned check err", path, e, file=sys.stderr)
        time.sleep(3)
    print("WARN pinned: could not verify " + path + ", using raw URL anyway", file=sys.stderr)
    return raw
def prune(pre="btc_onchain_"):
    r = requests.get(API + "btc", headers=H(), timeout=60)
    if r.status_code != 200: return
    imgs = [f for f in r.json() if f["name"].startswith(pre) and f["name"].endswith(".jpg")]
    stamps = sorted({re.sub(r"(_preview)?\.jpg$", "", f["name"]) for f in imgs}); old = set(stamps[:-KEEP])
    for f in imgs:
        if re.sub(r"(_preview)?\.jpg$", "", f["name"]) in old:
            requests.delete(API + f["path"], headers=H(), json={"message": "prune " + f["name"], "sha": f["sha"], "branch": "main"}, timeout=60)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    import onchain_six as O
    R = O.produce(os.path.join(HERE, "charts")); T = R["T"]
    if R["errs"]:
        print(T); sys.exit("self-check FAIL, not writing/publishing: " + "; ".join(R["errs"]))
    open(os.path.join(HERE, "onchain_text.txt"), "w").write(T)
    png = os.path.join(HERE, "onchain_summary.png"); shutil.copyfile(R["overview"], png)
    json.dump(R["state"], open(os.path.join(HERE, "onchain_state.json"), "w"), ensure_ascii=False, indent=1, default=str)
    od = os.path.join(O.ROOT, "out"); os.makedirs(od, exist_ok=True)
    json.dump(R["alert"], open(os.path.join(od, "onchain_alert_state.json"), "w"), ensure_ascii=False, indent=1)
    print(T); print("fresh:", R["fresh"], "data date:", R["state"]["data_date"], "alert cross:", R["alert"]["crossed"], file=sys.stderr)
    if a.dry_run: return
    from PIL import Image
    now = R["now"]; stamp = now.strftime("%Y%m%d_%H%M"); pd_ = os.path.join(HERE, "publish"); os.makedirs(pd_, exist_ok=True)
    im = Image.open(png).convert("RGB"); jb = os.path.join(pd_, f"btc_onchain_{stamp}.jpg"); im.save(jb, "JPEG", quality=92, optimize=True)
    q = im.copy(); q.thumbnail((1024, 1024)); jp = jb[:-4] + "_preview.jpg"; q.save(jp, "JPEG", quality=85, optimize=True)
    assert os.path.getsize(jb) <= 10_000_000 and os.path.getsize(jp) <= 1_000_000, "LINE image size limit"
    m1, m2 = "btc/" + os.path.basename(jb), "btc/" + os.path.basename(jp)
    j = {"date": now.strftime("%Y-%m-%d %H:%M"), "data_date": R["state"]["data_date"], "fresh": R["fresh"], "text": T,
         "image_url": pinned(gh_put(m1, open(jb, "rb").read(), "btc onchain " + stamp), m1),
         "preview_url": pinned(gh_put(m2, open(jp, "rb").read(), "btc onchain preview " + stamp), m2)}
    j["levels"] = {k: R["state"][k] for k in ("data_date", "cycle", "sth", "tmm", "realized", "lth", "bb_overheated", "bb_heated", "bb_cool", "bb_over",
                                               "cycle_low", "cycle_low_date", "ath", "ath_date", "stood_sth", "stood_sth_date", "sth_bbz")}
    sha = gh_put("btc/onchain_latest.json", json.dumps(j, ensure_ascii=False, indent=1).encode(), "btc onchain latest " + stamp)
    prune(); print("published", sha, j["image_url"], file=sys.stderr)
if __name__ == "__main__": main()

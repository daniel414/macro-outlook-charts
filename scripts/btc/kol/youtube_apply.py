#!/usr/bin/env python3
"""把鋒哥 YouTube 影片摘要寫進 latest_view.json（traderfengge 條目）＋ stance_history.jsonl，並標記影片已處理。
用法：youtube_apply.py <summary.json>
summary.json：{"id","stance"(偏多/中性/偏空),"stance_note","levels"(用｜分隔),"reason"(以「YouTube M/D：」開頭，；後可接 X 補充)}
其餘欄位（video_date/title/url）從 youtube/pending.json 帶入。會先備份 latest_view.json 到 latest_view.prev.json。"""
import json, os, sys, shutil, subprocess
H = os.path.dirname(os.path.abspath(__file__)); s = json.load(open(sys.argv[1]))
pend = json.load(open(os.path.join(H, "youtube/pending.json"))); p = next(x for x in pend if x["id"] == s["id"])
lv = os.path.join(H, "latest_view.json"); shutil.copy(lv, os.path.join(H, "latest_view.prev.json")); d = json.load(open(lv))
a = next((x for x in d["accounts"] if x["handle"] == "traderfengge"), None)
if a is None: a = {"handle": "traderfengge"}; d["accounts"].append(a)
a.update({k: s[k] for k in ("stance", "stance_note", "levels", "reason")})
a["latest_post"] = max(a.get("latest_post", ""), p["video_date"])
a["youtube"] = {"video_date": p["video_date"], "title": p["title"], "url": p["url"], "method": p["method"]}
json.dump(d, open(lv, "w"), ensure_ascii=False, indent=1)
with open(os.path.join(H, "stance_history.jsonl"), "a") as f:
    f.write(json.dumps({"date": p["video_date"], "handle": "traderfengge", "stance": s["stance"], "levels": s["levels"], "source": "youtube", "video_id": p["id"]}, ensure_ascii=False) + "\n")
subprocess.run([sys.executable, os.path.join(H, "youtube_check.py"), "--done", p["id"]])
print("applied", p["id"], p["video_date"], s["stance"])

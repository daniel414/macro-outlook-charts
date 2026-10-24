#!/usr/bin/env python3
"""鋒哥（traderfengge）YouTube 新影片檢查＋免費轉錄。
頻道：比特币峰哥 / 「峰哥」比特币行情分析  @Traderfengge  UCqKGLcPxejYCq0rKN51sDJg（說明欄連到官方推特 traderfengge）
流程：yt-dlp 列最新影片 -> 未處理者先試字幕（zh），沒有則下載音訊用 faster-whisper（本機，免費）轉錄
輸出：kol/youtube/<id>.txt（轉錄全文）、kol/youtube/state.json（已處理清單）、kol/youtube/pending.json（待摘要的新影片）
用法：youtube_check.py [--max-new 1] [--model small]
之後由 btc-3 routine 讀 pending.json 的轉錄，摘出 BTC 方向/價位/理由，寫進 latest_view.json 的 traderfengge 條目（加 video_date/video_title/video_url），
並 append stance_history.jsonl（source: youtube），完成後執行 youtube_check.py --done <id>。"""
import json, os, sys, subprocess, glob, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "youtube")
YTDLP = "/home/box/.local/bin/yt-dlp"; PY = "/home/box/venv-whisper/bin/python"
CH = "https://www.youtube.com/@Traderfengge/videos"; CHID = "UCqKGLcPxejYCq0rKN51sDJg"
os.makedirs(D, exist_ok=True); SF = os.path.join(D, "state.json"); PF = os.path.join(D, "pending.json")
st = json.load(open(SF)) if os.path.exists(SF) else {"done": [], "seen": {}}
pend = json.load(open(PF)) if os.path.exists(PF) else []
a = sys.argv[1:]
def save(): json.dump(st, open(SF, "w"), ensure_ascii=False, indent=1); json.dump(pend, open(PF, "w"), ensure_ascii=False, indent=1)
if a[:1] == ["--done"]:
    for v in a[1:]:
        if v not in st["done"]: st["done"].append(v)
    pend[:] = [p for p in pend if p["id"] not in a[1:]]; save(); print("done", a[1:]); sys.exit()
maxnew = int(a[a.index("--max-new")+1]) if "--max-new" in a else 1
model = a[a.index("--model")+1] if "--model" in a else "small"
r = subprocess.run([YTDLP, "--flat-playlist", "--playlist-end", "5", "--print", "%(id)s|%(title)s", CH], capture_output=True, text=True, timeout=120)
vids = [l.split("|", 1) for l in r.stdout.strip().splitlines() if "|" in l]
if not vids: print("youtube: 列表失敗", r.stderr[-300:]); sys.exit(1)
known = set(st["done"]) | {p["id"] for p in pend}
new = []
for v in vids:   # 只取比最近已知影片更新的（列表由新到舊）
    if v[0] in known: break
    new.append(v)
if not st["done"] and not pend: new = vids[:1]   # 首次只處理最新一支
new = new[:maxnew][::-1]   # 舊的先處理
if not new: print("youtube: 沒有新影片（最新 %s %s）" % tuple(vids[0])); sys.exit()
for vid, title in new:
    url = f"https://www.youtube.com/watch?v={vid}"; base = os.path.join(D, vid)
    m = subprocess.run([YTDLP, "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "zh.*", "--sub-format", "vtt",
        "--print", "%(upload_date)s|%(channel_id)s|%(duration)s", "--no-simulate", "-o", base, url], capture_output=True, text=True, timeout=180)
    up, chid, dur = (m.stdout.strip().splitlines() or ["||"])[-1].split("|")
    if chid != CHID: print("youtube: 頻道不符，略過", vid); continue
    txt = ""; method = ""
    subs = glob.glob(base + "*.vtt")
    if subs:
        lines = [l.strip() for l in open(subs[0]) if l.strip() and "-->" not in l and not l.startswith(("WEBVTT", "Kind:", "Language:"))]
        out = []; [out.append(l) for l in lines if not out or out[-1] != l]; txt = "\n".join(out); method = "字幕"
    else:
        au = base + ".m4a"
        subprocess.run([YTDLP, "-f", "bestaudio[ext=m4a]/bestaudio", "-o", au, url], capture_output=True, timeout=600)
        # 注意：本機 CPU 上 int8 會輸出亂碼，必須用 float32；用 ffmpeg 解碼（PyAV 版本不相容）
        code = ("import numpy as np,subprocess,sys\nfrom faster_whisper import WhisperModel\n"
                "a=np.frombuffer(subprocess.run(['ffmpeg','-nostdin','-loglevel','error','-i',sys.argv[1],'-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True).stdout,np.float32)\n"
                f"m=WhisperModel('{model}',device='cpu',compute_type='float32',cpu_threads=6)\n"
                "s,_=m.transcribe(a,language='zh',beam_size=5,condition_on_previous_text=False,initial_prompt='以下是普通话的比特币行情分析。')\n"
                "print('\\n'.join('[%02d:%02d] %s'%(x.start//60,x.start%60,x.text.strip()) for x in s))")
        w = subprocess.run([PY, "-c", code, au], capture_output=True, text=True, timeout=3000)
        txt = w.stdout.strip(); method = f"faster-whisper {model}"
        if os.path.exists(au): os.remove(au)
    if not txt: print("youtube: 轉錄失敗", vid); continue
    open(base + ".txt", "w").write(txt)
    vd = f"{up[:4]}-{up[4:6]}-{up[6:]}"
    pend.append({"id": vid, "title": title, "url": url, "video_date": vd, "duration": dur, "method": method,
                 "transcript": base + ".txt", "fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
    st["seen"][vid] = vd; save(); print(f"youtube: 新影片 {vd} {vid} {title}（{method}，{len(txt)} 字）")

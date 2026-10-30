#!/usr/bin/env python3
"""Analyst-view image (Coinglass dark theme) from kol/latest_view.json, phone-readable, 2160 px wide.
  python kol_chart.py [--out X.png]   -> prints PNG path (exit 1 if JSON missing or text boxes overlap)
Layout uses measured wrapped-text heights (textbbox). Every drawn string records a bbox;
any pairwise overlap fails the run (non-zero exit, no PNG written)."""
import argparse, json, os, sys
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
TH = dict(bg="#0e1117", grid="#27292f", text="#cbced3", muted="#9b9ea4", title="#ffffff",
          price="#dfbb68", up="#2ebd85", down="#f6465d", neutral="#9b9ea4", quiet="#5c5f66")
SC = {"偏多": TH["up"], "偏空": TH["down"], "中性": TH["neutral"], "未談BTC": TH["quiet"]}
FR, FB = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
F = lambda sz, b=False: ImageFont.truetype(FB if b else FR, sz, index=3)  # index 3 = TC
W, M = 2160, 90
GAP_LINE = 10          # padding below a measured text line before the next
GAP_BLOCK = 28         # padding below a KOL block before the separator
GAP_AFTER_SEP = 28     # padding after separator before next handle
HANDLE_STANCE_GAP = 48 # min gap between handle right edge and stance left edge


def wrap(d, t, f, w):
    out, cur = [], ""
    for ch in t:
        if d.textlength(cur + ch, font=f) > w and cur:
            out.append(cur); cur = ch.lstrip()
        else:
            cur += ch
    return out + ([cur] if cur else [])


def boxes_overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def check_overlaps(boxes):
    """Return list of (label_i, label_j, bbox_i, bbox_j) for every overlapping pair."""
    hits = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes_overlap(boxes[i][1], boxes[j][1]):
                hits.append((boxes[i][0], boxes[j][0], boxes[i][1], boxes[j][1]))
    return hits


def render(v, out):
    acc = v["accounts"]
    order = {"偏多": 0, "偏空": 1, "中性": 2, "未談BTC": 3}
    rows = sorted(acc, key=lambda a: order.get(a.get("stance"), 9))
    ts = datetime.strptime(v["updated_at"][:16], "%Y-%m-%d %H:%M").strftime("%m/%d %H:%M")
    boxes = []  # (label, bbox)

    def put(d, xy, text, font, fill, anchor="lt", label=""):
        """Draw text with measured bbox; always record the ink box. Default anchor=lt (top-left)."""
        bb = d.textbbox(xy, text, font=font, anchor=anchor)
        boxes.append((label, bb))
        d.text(xy, text, font=font, fill=fill, anchor=anchor)
        return bb

    # Pass 0: measure final height; pass 1: draw into exact-size canvas
    H0 = 6000
    im = None
    for pas in (0, 1):
        boxes.clear()
        H = H0 if pas else 8000
        im = Image.new("RGB", (W, H), TH["bg"])
        d = ImageDraw.Draw(im)
        y = 70
        bw = W - 2 * M

        bb = put(d, (M, y), "分析師觀點", F(84, True), TH["title"], label="title")
        put(d, (W - M, y + 8), f"更新 {ts} UTC+8", F(46), TH["muted"], anchor="rt", label="ts")
        y = bb[3] + 40

        cnt = [(k, sum(1 for a in acc if a.get("stance") == k)) for k in ("偏多", "中性", "偏空", "未談BTC")]
        x = M
        row_bottom = y
        for k, n in cnt:
            lab = f"{'未談' if k == '未談BTC' else k} {n}"
            bb = put(d, (x, y), lab, F(56, True), SC[k], label=f"cnt:{k}")
            x += d.textlength(lab, font=F(56, True)) + 70
            row_bottom = max(row_bottom, bb[3])
        y = row_bottom + 24

        x = M
        for k, n in cnt:
            if n:
                d.rectangle([x, y, x + bw * n / len(acc), y + 26], fill=SC[k])
                x += bw * n / len(acc)
        y += 26 + 36

        for lab, key, col in (("共同價位", "common_levels", TH["price"]), ("主要分歧", "divergence", TH["text"])):
            if v.get(key):
                f = F(48)
                for i, ln in enumerate(wrap(d, f"{lab}：{v[key]}", f, bw)):
                    bb = put(d, (M, y), ln, f, col, label=f"{key}:{i}")
                    y = bb[3] + GAP_LINE
                y += 8

        y += 12
        d.line([M, y, W - M, y], fill=TH["grid"], width=3)
        y += 28

        for a in rows:
            st = a.get("stance", "-")
            col = SC.get(st, TH["text"])
            handle = a["handle"]
            stl = a.get("stance_note") or ("未談" if st == "未談BTC" else st)
            hf, sf = F(56, True), F(52, True)

            # Right column for stance: must not invade handle + gap
            hw = d.textlength(handle, font=hf)
            stance_left = M + hw + HANDLE_STANCE_GAP
            stance_max_w = max(80, (W - M) - stance_left)
            stance_lines = wrap(d, stl, sf, stance_max_w) or [stl]

            h_bb = put(d, (M, y), handle, hf, TH["title"], label=f"handle:{handle}")
            stance_bottom = y
            for i, ln in enumerate(stance_lines):
                # Stack wrapped stance lines under the header row start; right-aligned
                bb = put(d, (W - M, y if i == 0 else stance_bottom + GAP_LINE), ln, sf, col,
                         anchor="rt", label=f"stance:{handle}:{i}")
                stance_bottom = bb[3]
            y = max(h_bb[3], stance_bottom) + 18

            lv = a.get("levels") or "-"
            body = (f"價位：{lv}　" if lv != "-" else "") + (a.get("reason") or "")
            bf = F(46)
            body_fill = TH["text"] if st != "未談BTC" else TH["muted"]
            for i, ln in enumerate(wrap(d, body, bf, bw)):
                bb = put(d, (M, y), ln, bf, body_fill, label=f"body:{handle}:{i}")
                y = bb[3] + GAP_LINE
            y += GAP_BLOCK
            d.line([M, y, W - M, y], fill=TH["grid"], width=2)
            y += GAP_AFTER_SEP

        foot = f"來源：{len(acc)} 個 X 帳號近期貼文，每日 06:00 / 14:00 / 22:00 更新"
        bb = put(d, (M, y + 4), foot, F(36), TH["muted"], label="footer")
        H0 = bb[3] + 40

    hits = check_overlaps(boxes)
    if hits:
        print(f"OVERLAP {len(hits)} pair(s):", file=sys.stderr)
        for a, b, ba, bb_ in hits[:20]:
            print(f"  {a} {ba} vs {b} {bb_}", file=sys.stderr)
        sys.exit(1)

    im.save(out, "PNG", optimize=True)
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--out"); a = p.parse_args()
    try:
        v = json.load(open(os.path.join(HERE, "kol/latest_view.json"))); assert v.get("accounts")
    except Exception as e:
        sys.exit(f"no kol view: {e}")
    os.makedirs(os.path.join(HERE, "out/kol"), exist_ok=True)
    out = a.out or os.path.join(HERE, f"out/kol/btc_kol_{datetime.now():%Y%m%d_%H%M%S}.png")
    print(render(v, out))

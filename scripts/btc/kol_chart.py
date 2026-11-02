#!/usr/bin/env python3
"""Analyst-view image(s) from kol/latest_view.json — phone layout (2 pages).
  python kol_chart.py [--out-prefix PATH]
Prints one PNG path per line on stdout. Exit 1 if overlap or layout check fails (no publishable PNGs).
"""
import argparse, json, os, re, sys
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
TH = dict(bg="#0e1117", grid="#27292f", text="#cbced3", muted="#9b9ea4", title="#ffffff",
          price="#dfbb68", up="#2ebd85", down="#f6465d", neutral="#9b9ea4", quiet="#5c5f66")
SC = {"偏多": TH["up"], "偏空": TH["down"], "中性": TH["neutral"], "未談BTC": TH["quiet"]}
FR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FB = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
F = lambda sz, b=False: ImageFont.truetype(FB if b else FR, sz, index=3)

ORIG_W, ORIG_BODY = 2160, 46
ORIG_RATIO = ORIG_BODY / ORIG_W
PAD_X, PAD_Y = 700, 500
CONTENT_W = 2160
TOTAL_W = CONTENT_W + 2 * PAD_X
SCALE = TOTAL_W / ORIG_W
ASPECT_MAX = 2.25

TOKEN_RE = re.compile(r"[A-Za-z0-9.,%+\-–/@_]+")
KINSOKU_LEAD = set("，。；：、）」』》〉】〗〕!,.:;?%…—–-）﹒")

# Auto-condense fillers (keep levels / direction / timeframe)
FILLER_RE = re.compile(
    r"(之後未再更新[；。]?"
    r"|之後貼文偏[^；。]*[；。]?"
    r"|後續貼文偏[^；。]*[；。]?"
    r"|未改[^；。]*判斷[；。]?"
    r"|未改[^；。]*方向[；。]?"
    r"|延續[^；。]*計畫[；。]?"
    r"|計畫不變[；。]?"
    r"|並稱|自稱"
    r"|並同步[^；。]*[；。]?"
    r"|@XiaoYao_Capital[^；。]*[；。]?"
    r"|美股另記：[^｜|]*"
    r"|YouTube\s*"
    r"|偏人生／[^；。]*[；。]?"
    r")"
)
LABEL_MAP = [("短線／中期", "短中"), ("短線/中期", "短中"), ("短線：", "短："), ("中期：", "中："),
             ("長期：", "長："), ("短中期", "短中"), ("高週期", "HTF")]


def fs(n):
    return max(12, int(round(n * SCALE)))


GAP_LINE = max(8, int(round(10 * SCALE)))
GAP_BLOCK = max(16, int(round(28 * SCALE)))
GAP_AFTER_SEP = max(16, int(round(28 * SCALE)))
HANDLE_STANCE_GAP = max(32, int(round(48 * SCALE)))
M = max(60, int(round(90 * SCALE)))


def tokenize(text):
    out, i = [], 0
    while i < len(text):
        m = TOKEN_RE.match(text, i)
        if m:
            out.append(m.group()); i = m.end()
        else:
            out.append(text[i]); i += 1
    return out


def wrap(d, t, f, w):
    """Atomic-token wrap with kinsoku: never start a line with KINSOKU_LEAD chars.
    Peel leading punct onto the previous line even after an empty-cur restart."""
    if not t:
        return []
    tokens = tokenize(t)
    lines, cur = [], ""

    def peel():
        nonlocal cur
        while cur and cur[0] in KINSOKU_LEAD and lines:
            lines[-1] = lines[-1] + cur[0]
            cur = cur[1:]
        if cur:
            cur = cur.lstrip(" ")

    for tok in tokens:
        if cur and d.textlength(cur + tok, font=f) <= w:
            cur = cur + tok
            continue
        if cur:
            lines.append(cur)
            cur = ""
        cur = tok
        peel()
        # if still empty after peel (tok was all kinsoku), stay empty for next tok
    if cur:
        peel()
        if cur:
            lines.append(cur)
    return lines


def check_wrap_quality(src, lines, kind="body"):
    bad = []
    if not lines:
        return bad
    for ln in lines:
        if ln and ln[0] in KINSOKU_LEAD:
            bad.append(f"{kind}:leading_punct:{ln[:40]!r}")
    joined = "\n".join(lines)
    for tok in tokenize(src):
        if len(tok) <= 1 or not TOKEN_RE.fullmatch(tok):
            continue
        if not any(tok in ln for ln in lines):
            for i in range(1, len(tok)):
                if f"{tok[:i]}\n{tok[i:]}" in joined:
                    bad.append(f"{kind}:split_token:{tok!r}"); break
        else:
            for i in range(1, len(tok)):
                if f"{tok[:i]}\n{tok[i:]}" in joined:
                    bad.append(f"{kind}:split_token:{tok!r}"); break
    return bad


def boxes_overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def check_overlaps(boxes):
    hits = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes_overlap(boxes[i][1], boxes[j][1]):
                hits.append((boxes[i][0], boxes[j][0], boxes[i][1], boxes[j][1]))
    return hits


def _squash(s):
    s = (s or "").strip()
    s = FILLER_RE.sub("", s)
    for a, b in LABEL_MAP:
        s = s.replace(a, b)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"[；;]{2,}", "；", s)
    s = re.sub(r"｜{2,}", "｜", s)
    return s.strip(" ；;｜|")


def condense_field(s, max_len):
    s = _squash(s)
    if len(s) <= max_len:
        return s
    # prefer cut at punctuation keeping numbers-rich head
    cut = s[:max_len]
    for sep in ("；", "。", "｜", "，", "、", " "):
        i = cut.rfind(sep)
        if i >= max_len // 2:
            return cut[:i].rstrip(" ；。，、")
    return cut.rstrip()


def _shorten_level_seg(seg):
    """Keep timeframe + key levels/direction; drop narrative filler inside a segment."""
    s = _squash(seg)
    reps = [
        (r"已見止跌／緩漲；?", ""),
        (r"支撐\s*", "撐 "),
        (r"壓力\s*", "壓 "),
        (r"突破後看\s*", "→"),
        (r"需站穩\s*", "站穩 "),
        (r"才視空頭結束、趨勢反轉", "才結束空頭"),
        (r"才確認轉強", "才轉強"),
        (r"才視[^；｜]*反轉", "才轉強"),
        (r"期中選舉前仍可能橫盤", "選前或橫盤"),
        (r"回踩確認仍處牛市", "回踩仍牛"),
        (r"失效若有效跌破\s*", "失守 "),
        (r"清算多頭", "失效"),
        (r"最多看到\s*", "最多看 "),
        (r"均價約\s*", "~"),
        (r"抄底\s*\d+WU\s*BTC\s*多單；?", "抄底；"),
        (r"\s{2,}", " "),
    ]
    for a, b in reps:
        s = re.sub(a, b, s)
    s = _squash(s)
    return condense_field(s, 40)


def condense_stance(note, stance):
    if not note:
        return "未談" if stance == "未談BTC" else stance
    s = _squash(note)
    s = re.sub(r"才確認轉強", "才轉強", s)
    s = re.sub(r"空單止盈後翻多；?", "翻多後", s)
    s = re.sub(r"後面不做空只做多", "只做多不做空", s)
    # target ~14–18 CJK for right-side single line
    return condense_field(s, 18)


def condense_levels(levels):
    if not levels or levels.strip() == "-":
        return "-"
    parts = []
    for seg in re.split(r"[｜|]", levels):
        seg = _shorten_level_seg(seg)
        if not seg or seg == "-":
            continue
        parts.append(seg)
    return "｜".join(parts) if parts else "-"


def condense_reason(reason):
    s = _squash(reason)
    m = re.match(r"^([^｜]*?)(?:\s*｜|$)", s)
    head = m.group(1) if m else s
    head = re.sub(r"[「「][^」」]{20,}[」」]", "…", head)
    return condense_field(head, 52)


def common_levels_lines(v):
    if v.get("common_levels_lines"):
        return [_squash(x) for x in v["common_levels_lines"] if _squash(x)]
    raw = v.get("common_levels") or ""
    lines = []
    for big in re.split(r"[｜|]", raw):
        big = big.strip()
        if not big:
            continue
        # split support/pressure clusters on ／ when long
        if "／" in big and len(big) > 36:
            bits = [b.strip() for b in big.split("／") if b.strip()]
            # keep shared prefix word (撐/壓/上) on each if present
            prefix = ""
            m = re.match(r"^(撐|壓|上|支撐|壓力)[：:\s]*", bits[0])
            if m:
                prefix = m.group(1)
            for i, b in enumerate(bits):
                b = _squash(b)
                if i > 0 and prefix and not b.startswith(("撐", "壓", "上", "支撐", "壓力")):
                    b = f"{prefix} {b}"
                lines.append(condense_field(b, 42))
        else:
            lines.append(condense_field(_squash(big), 48))
    return lines


def condense_view(v):
    """Return a shallow-copied view with condensed account fields (does not write disk)."""
    out = dict(v)
    out["common_levels_lines"] = common_levels_lines(v)
    if v.get("divergence"):
        out["divergence"] = condense_field(_squash(v["divergence"]), 90)
    if v.get("summary"):
        out["summary"] = condense_field(_squash(v["summary"]), 80)
    acc = []
    for a in v.get("accounts") or []:
        b = dict(a)
        b["stance_note"] = condense_stance(a.get("stance_note"), a.get("stance"))
        b["levels"] = condense_levels(a.get("levels"))
        b["reason"] = condense_reason(a.get("reason"))
        acc.append(b)
    out["accounts"] = acc
    return out


def render_page(v, rows, out, page_i, page_n, show_summary, layout_violations):
    ts = datetime.strptime(v["updated_at"][:16], "%Y-%m-%d %H:%M").strftime("%m/%d %H:%M")
    boxes, drawn_lines = [], []
    acc_all = v["accounts"]
    left = PAD_X + M
    right = PAD_X + CONTENT_W - M
    bw = right - left

    def put(d, xy, text, font, fill, anchor="lt", label=""):
        bb = d.textbbox(xy, text, font=font, anchor=anchor)
        boxes.append((label, bb)); drawn_lines.append((label, text))
        d.text(xy, text, font=font, fill=fill, anchor=anchor)
        return bb

    H0 = 8000
    im = None
    for pas in (0, 1):
        boxes.clear(); drawn_lines.clear()
        H = H0 if pas else 12000
        im = Image.new("RGB", (TOTAL_W, H), TH["bg"])
        d = ImageDraw.Draw(im)
        y = PAD_Y + 70

        title = "分析師觀點" + (f"（{page_i}/{page_n}）" if page_n > 1 else "")
        bb = put(d, (left, y), title, F(fs(84), True), TH["title"], label="title")
        y = bb[3] + GAP_LINE
        bb = put(d, (left, y), f"更新 {ts} UTC+8", F(fs(46)), TH["muted"], label="ts")
        y = bb[3] + 36

        if show_summary:
            cnt = [(k, sum(1 for a in acc_all if a.get("stance") == k)) for k in ("偏多", "中性", "偏空", "未談BTC")]
            x = left; row_bottom = y
            for k, n in cnt:
                lab = f"{'未談' if k == '未談BTC' else k} {n}"
                bb = put(d, (x, y), lab, F(fs(56), True), SC[k], label=f"cnt:{k}")
                x += d.textlength(lab, font=F(fs(56), True)) + 70
                row_bottom = max(row_bottom, bb[3])
            y = row_bottom + 24
            x = left
            for k, n in cnt:
                if n:
                    d.rectangle([x, y, x + bw * n / max(1, len(acc_all)), y + 26], fill=SC[k])
                    x += bw * n / max(1, len(acc_all))
            y += 26 + 36

            f = F(fs(48))
            bb = put(d, (left, y), "共同價位：", f, TH["price"], label="common_levels:h")
            y = bb[3] + GAP_LINE
            for i, ln0 in enumerate(v.get("common_levels_lines") or []):
                wrapped = wrap(d, ln0, f, bw)
                layout_violations.extend(check_wrap_quality(ln0, wrapped, f"common_levels:{i}"))
                for j, wln in enumerate(wrapped):
                    bb = put(d, (left, y), wln, f, TH["price"], label=f"common_levels:{i}:{j}")
                    y = bb[3] + GAP_LINE
            y += 8
            if v.get("divergence"):
                src = f"主要分歧：{v['divergence']}"
                wrapped = wrap(d, src, f, bw)
                layout_violations.extend(check_wrap_quality(src, wrapped, "divergence"))
                for i, ln in enumerate(wrapped):
                    bb = put(d, (left, y), ln, f, TH["text"], label=f"divergence:{i}")
                    y = bb[3] + GAP_LINE
                y += 8
            y += 12
            d.line([left, y, right, y], fill=TH["grid"], width=3)
            y += 28
        else:
            note = f"續上頁 · 共 {len(acc_all)} 位"
            bb = put(d, (left, y), note, F(fs(46)), TH["muted"], label="cont")
            y = bb[3] + 28
            d.line([left, y, right, y], fill=TH["grid"], width=3)
            y += 28

        for a in rows:
            st = a.get("stance", "-")
            col = SC.get(st, TH["text"])
            handle = a["handle"]
            stl = a.get("stance_note") or ("未談" if st == "未談BTC" else st)
            hf, sf = F(fs(56), True), F(fs(52), True)
            hw = d.textlength(handle, font=hf)
            stance_max_w = max(80, right - (left + hw + HANDLE_STANCE_GAP))
            st_w = d.textlength(stl, font=sf)

            h_bb = put(d, (left, y), handle, hf, TH["title"], label=f"handle:{handle}")
            if st_w <= stance_max_w:
                bb = put(d, (right, y), stl, sf, col, anchor="rt", label=f"stance:{handle}")
                y = max(h_bb[3], bb[3]) + 18
            else:
                y = h_bb[3] + 12
                wrapped = wrap(d, stl, sf, bw)
                if len(wrapped) > 1:
                    layout_violations.append(f"stance_wrapped:{handle}:{wrapped!r}")
                layout_violations.extend(check_wrap_quality(stl, wrapped, f"stance:{handle}"))
                for i, ln in enumerate(wrapped):
                    bb = put(d, (left, y), ln, sf, col, label=f"stance_under:{handle}:{i}")
                    y = bb[3] + GAP_LINE
                y += 6

            lv = a.get("levels") or "-"
            body = (f"價位：{lv}　" if lv != "-" else "") + (a.get("reason") or "")
            bf = F(fs(46))
            body_fill = TH["text"] if st != "未談BTC" else TH["muted"]
            wrapped = wrap(d, body, bf, bw)
            layout_violations.extend(check_wrap_quality(body, wrapped, f"body:{handle}"))
            for i, ln in enumerate(wrapped):
                bb = put(d, (left, y), ln, bf, body_fill, label=f"body:{handle}:{i}")
                y = bb[3] + GAP_LINE
            y += GAP_BLOCK
            d.line([left, y, right, y], fill=TH["grid"], width=2)
            y += GAP_AFTER_SEP

        foot = f"來源：{len(acc_all)} 個帳號近期貼文 · {page_i}/{page_n}"
        bb = put(d, (left, y + 4), foot, F(fs(36)), TH["muted"], label="footer")
        H0 = bb[3] + PAD_Y

    for lab, text in drawn_lines:
        if text and text[0] in KINSOKU_LEAD:
            layout_violations.append(f"drawn_leading_punct:{lab}:{text[:40]!r}")

    hits = check_overlaps(boxes)
    if hits:
        print(f"OVERLAP {len(hits)} pair(s):", file=sys.stderr)
        for a, b, ba, bb_ in hits[:20]:
            print(f"  {a} {ba} vs {b} {bb_}", file=sys.stderr)
        return None, layout_violations

    im.save(out, "PNG", optimize=True)
    return {
        "path": out,
        "size": list(im.size),
        "aspect": round(im.size[1] / im.size[0], 3),
        "body_font": fs(46),
        "text_vs_orig_pct": round(100.0 * (fs(46) / im.size[0]) / ORIG_RATIO, 1),
        "page": f"{page_i}/{page_n}",
    }, layout_violations


def split_rows(rows, n_pages):
    if n_pages <= 1:
        return [rows]
    k, m = divmod(len(rows), n_pages)
    out, i = [], 0
    for p in range(n_pages):
        take = k + (1 if p < m else 0)
        out.append(rows[i:i + take]); i += take
    return out


def render_all(v, out_prefix):
    v = condense_view(v)
    order = {"偏多": 0, "偏空": 1, "中性": 2, "未談BTC": 3}
    rows = sorted(v["accounts"], key=lambda a: order.get(a.get("stance"), 9))
    layout_violations = []

    probe = out_prefix + "_probe.png"
    info, layout_violations = render_page(v, rows, probe, 1, 1, True, layout_violations)
    if info is None:
        try: os.remove(probe)
        except OSError: pass
        print("OVERLAP_CHECK FAIL", file=sys.stderr); return None
    n_pages = 1
    if info["aspect"] > ASPECT_MAX:
        n_pages = 2 if info["aspect"] / 2 <= ASPECT_MAX else 3
    try: os.remove(probe)
    except OSError: pass
    layout_violations.clear()

    chunks = split_rows(rows, n_pages)
    results, paths = [], []
    for i, chunk in enumerate(chunks, 1):
        out = f"{out_prefix}_p{i}of{n_pages}.png" if n_pages > 1 else f"{out_prefix}.png"
        meta, layout_violations = render_page(v, chunk, out, i, n_pages, i == 1, layout_violations)
        if meta is None:
            for p in paths:
                try: os.remove(p)
                except OSError: pass
            print("OVERLAP_CHECK FAIL", file=sys.stderr); return None
        results.append(meta); paths.append(out)

    uniq = sorted(set(layout_violations))
    if uniq:
        for p in paths:
            try: os.remove(p)
            except OSError: pass
        print(f"LAYOUT_CHECK FAIL {len(uniq)}", file=sys.stderr)
        for x in uniq[:40]:
            print(" ", x, file=sys.stderr)
        return None

    print("LAYOUT_CHECK PASS", file=sys.stderr)
    print("OVERLAP_CHECK PASS", file=sys.stderr)
    for m in results:
        print(json.dumps({"page": m["page"], "size": m["size"], "aspect": m["aspect"],
                          "text_vs_orig_pct": m["text_vs_orig_pct"]}, ensure_ascii=False), file=sys.stderr)
    return paths


def main():
    p = argparse.ArgumentParser(); p.add_argument("--out-prefix"); a = p.parse_args()
    try:
        raw = json.load(open(os.path.join(HERE, "kol/latest_view.json"))); assert raw.get("accounts")
    except Exception as e:
        sys.exit(f"no kol view: {e}")
    os.makedirs(os.path.join(HERE, "out/kol"), exist_ok=True)
    prefix = a.out_prefix or os.path.join(HERE, f"out/kol/btc_kol_{datetime.now():%Y%m%d_%H%M%S}")
    paths = render_all(raw, prefix)
    if not paths:
        sys.exit(1)
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()

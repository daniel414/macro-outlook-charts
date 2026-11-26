#!/usr/bin/env python3
"""Analyst-view image(s) from kol/latest_view.json.
  python kol_chart.py [--out-prefix PATH] [--layout auto|slim|legacy] [--no-slim-file] [--slim-json]
  slim (3 pages: 總覽／看多／看空與中性) when kol/kol_layout.json has "slim": true or KOL_SLIM=1; else legacy.
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


def is_numeric_sign_lead(s):
    """A leading minus that starts a number (e.g. -54, -55%) is a sign, not kinsoku."""
    return bool(s) and s[0] in "-–" and len(s) > 1 and s[1].isdigit()


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
            if is_numeric_sign_lead(cur):
                break
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
        if ln and ln[0] in KINSOKU_LEAD and not is_numeric_sign_lead(ln):
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
        if text and text[0] in KINSOKU_LEAD and not is_numeric_sign_lead(text):
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



# =====================================================================
# Slim layout (2026-11-25, Daniel via Manager): 3 fixed pages
#   p1 總覽 (stance bar, <=6 common levels, main divergence)
#   p2 看多, p3 看空與中性. One stance line + 2-4 points per analyst.
# Content source, in order:
#   1) kol/latest_slim.json written by the btc-3 tracking run, used only when
#      its as_of equals latest_view.json updated_at (otherwise stale -> ignored)
#   2) automatic fallback derived from latest_view.json on every run
# Switch: kol/kol_layout.json {"slim": true|false}; env KOL_SLIM=1/0 overrides.
# =====================================================================
from datetime import date, timedelta

SLIM_PATH = os.path.join(HERE, "kol/latest_slim.json")
LAYOUT_PATH = os.path.join(HERE, "kol/kol_layout.json")
RECENT_DAYS = 7          # analyst counts as "談到 BTC" if latest_post within this window
LEVEL_DAYS = 3           # auto fallback: dated log segments newer than this are "recent"
MAX_COMMON = 6
MAX_POINTS, MIN_POINTS = 4, 2
WATERMARK = "4dan.app"
DISPLAY = {"traderfengge": "鋒哥"}   # Daniel 2026-10-24: X handles, 鋒哥 stays Chinese
# Rendered / text content must never contain these
BANNED = ["未談", "標的無法確認", "無法確認", "不列入", "不列為", "屬個股", "未註明", "未給 BTC", "數天", "數週",
          "數日", "http", "www.", "t.me", "youtube.com", "距今", "距離", "距現價"]
# Auto fallback: drop log segments that carry these (non-BTC, unconfirmed, promo, empty)
SEG_DROP = re.compile(r"未談|標的無法確認|無法確認|不列入|不列為|屬個股|未註明|未給 ?BTC|英偉達|NVDA|A 股|美股另記|"
                      r"課程|優惠|贈|抽獎|giveaway|附圖」?$|距|連結內|發布影片|訪談|未給價位|附圖對照|貼圖|貼出|"
                      r"未給.{0,12}方向|沒有?給.{0,12}方向|未給出|未(?:談|提及?|提供)")
DATE_RE = re.compile(r"^(?:YouTube\s*)?(\d{1,2})/(\d{1,2})(?:[–-]\d{1,2})?(?:\s*[^：:｜]{0,4})?[：:]\s*")


def display_name(h):
    return DISPLAY.get(h, h)


def slim_enabled():
    env = os.environ.get("KOL_SLIM")
    if env in ("0", "1"):
        return env == "1"
    try:
        return bool(json.load(open(LAYOUT_PATH)).get("slim"))
    except Exception:
        return False


def _vdate(v):
    return datetime.strptime(v["updated_at"][:16], "%Y-%m-%d %H:%M")


def _seg_date(seg, ref):
    m = DATE_RE.match(seg)
    if not m:
        return None
    mo, dy = int(m.group(1)), int(m.group(2))
    try:
        d0 = date(ref.year, mo, dy)
    except ValueError:
        return None
    if d0 > ref.date() + timedelta(days=1):
        d0 = date(ref.year - 1, mo, dy)
    return d0


def _clean(s):
    s = (s or "").strip()
    s = re.sub(r"^(?:YouTube\s*)?\d{1,2}/\d{1,2}(?:[–-]\d{1,2})?(?:\s*[^：:｜]{0,4})?[：:]\s*", "", s)
    s = re.sub(r"（[^（）]{0,40}）", "", s)
    s = s.replace("數週", "一段時間").replace("數天", "短期內").replace("數日", "短期內")
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ；;，,。")


def _clauses(s, cap):
    """Whole clauses only (split on ；/。 then ，), never cut inside a clause."""
    s = _clean(s)
    out = ""
    for big in re.split(r"(?<=[；。])", s):
        big = big.strip()
        if not big:
            continue
        cand = (out + big) if out else big
        if len(cand.rstrip("；。")) <= cap:
            out = cand
            continue
        if not out:   # first clause too long: take leading comma-clauses
            acc = ""
            for small in re.split(r"(?<=[，、])", big):
                if acc and len((acc + small).rstrip("，、；。")) > cap:
                    break
                acc += small
            out = acc
        break
    return out.strip().rstrip("，、；。 ")


def _sim(a, b):
    """Bigram overlap relative to the shorter string (0..1)."""
    g = lambda t: {t[i:i + 2] for i in range(len(t) - 1)}
    A, B = g(a), g(b)
    return len(A & B) / max(1, min(len(A), len(B)))


def _nums(t):
    return set(re.findall(r"\d+(?:\.\d+)?K|\d[\d,]{2,}", t or ""))


def auto_account(a, ref):
    line = _clauses(a.get("stance_note") or a.get("reason") or a.get("stance", ""), 22) or a.get("stance", "")
    segs = [x.strip() for x in re.split(r"[｜|]", a.get("levels") or "") if x.strip() and x.strip() != "-"]
    dated, undated = [], []
    for s in segs:
        if SEG_DROP.search(s):
            continue
        d0 = _seg_date(s, ref)
        if d0 is None:
            if not re.match(r"^(先前|11/\d|10/\d)", s):
                undated.append(s)
        elif (ref.date() - d0).days <= LEVEL_DAYS:
            dated.append(s)
    pool = dated + (undated if len(dated) < MIN_POINTS else [])
    pts, seen = [], _nums(line)
    for strict in (True, False):
        for s in [a.get("reason", "")] + pool:
            if len(pts) >= 3 or (not strict and len(pts) >= MIN_POINTS):
                break
            if s and SEG_DROP.search(s):
                continue
            p = _clauses(s, 40)
            if len(p) < 6 or p == line or p in pts:
                continue
            n = _nums(p)
            if strict and n and n <= seen:
                continue
            if any(p in q or q in p or _sim(p, q) > 0.45 for q in pts) or (strict and _sim(p, line) > 0.6):
                continue
            pts.append(p); seen |= n
    return {"handle": a["handle"], "stance": a["stance"], "line": line, "points": pts}


def _split_top(s, sep="、"):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "（(「":
            depth += 1
        elif ch in "）)」":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return [x.strip() for x in out if x.strip()]


def auto_common(v, ref):
    """First 支撐 / 壓力 clusters of common_levels; keep recent dated items; whole first clause of each note."""
    groups = [g.strip() for g in re.split(r"[｜|]", v.get("common_levels") or "") if g.strip()]
    picked = {"撐": [], "壓": []}
    for g in groups[:2]:
        m = re.match(r"^(支撐|壓力|撐|壓)\s*", g)
        if not m:
            continue
        kind = "撐" if "撐" in m.group(1) else "壓"
        for it in _split_top(g[m.end():]):
            mm = re.match(r"^([^（(]+)[（(](.*)[）)]$", it)
            if not mm:
                continue
            lvl, inner = mm.group(1).strip(), mm.group(2)
            if SEG_DROP.search(inner) or "／" in lvl:
                continue
            who_m = re.match(r"^([^：:]+)[：:](.*)$", inner)
            if not who_m:
                continue
            who, note = who_m.group(1), who_m.group(2)
            dm = re.search(r"(\d{1,2})/(\d{1,2})", who)
            if dm:
                try:
                    d0 = date(ref.year, int(dm.group(1)), int(dm.group(2)))
                    if (ref.date() - d0).days > LEVEL_DAYS:
                        continue
                except ValueError:
                    pass
            who = re.sub(r"\s*\d{1,2}/\d{1,2}\S*", "", who).replace("@", "").strip("、 ")
            who = "、".join(display_name(w.strip()) for w in who.split("、") if w.strip())
            note = _clauses(note, 20)
            if not note:
                continue
            picked[kind].append(f"{kind} {lvl}｜{who}：{note}")
    sup, res = picked["撐"][:3], picked["壓"][:3]
    while len(sup) + len(res) < MAX_COMMON and (len(picked["撐"]) > len(sup) or len(picked["壓"]) > len(res)):
        if len(picked["撐"]) > len(sup):
            sup = picked["撐"][:len(sup) + 1]
        elif len(picked["壓"]) > len(res):
            res = picked["壓"][:len(res) + 1]
    return res + sup


def auto_divergence(v):
    s = (v.get("divergence") or "").replace("@", "")
    s = re.sub(r"（[^）]*）", "", s)
    return _clean(s).rstrip("。") + "。" if s else ""


def build_model(v, use_slim=True):
    ref = _vdate(v)
    warnings = []
    slim = None
    if use_slim and os.path.isfile(SLIM_PATH):
        try:
            slim = json.load(open(SLIM_PATH, encoding="utf-8"))
            if slim.get("as_of", "")[:16] != v["updated_at"][:16]:
                warnings.append(f"slim_stale:{slim.get('as_of')}!={v['updated_at']}")
                slim = None
        except Exception as e:
            warnings.append(f"slim_unreadable:{e}"); slim = None
    elif use_slim:
        warnings.append("slim_missing")
    sacc = {x["handle"]: x for x in (slim or {}).get("accounts", []) if x.get("handle")}
    rows = []
    for a in v.get("accounts") or []:
        if a.get("stance") not in ("偏多", "偏空", "中性"):
            continue
        try:
            age = (ref.date() - date.fromisoformat(a.get("latest_post", "")[:10])).days
        except Exception:
            age = 99
        if age > RECENT_DAYS:
            continue
        s = sacc.get(a["handle"])
        if s and s.get("omit"):
            continue
        if s and s.get("line") and s.get("points"):
            r = {"handle": a["handle"], "stance": a["stance"], "line": s["line"].strip(),
                 "points": [p.strip() for p in s["points"] if p.strip()][:MAX_POINTS], "src": "slim"}
        else:
            if slim:
                warnings.append(f"slim_account_missing:{a['handle']}")
            r = auto_account(a, ref); r["src"] = "auto"
        if not r["points"]:
            warnings.append(f"no_points:{a['handle']}")
        rows.append(r)
    cl = [x.strip() for x in (slim or {}).get("common_levels", []) if x.strip()][:MAX_COMMON] if slim else auto_common(v, ref)
    dv = (slim or {}).get("divergence", "").strip() if slim else auto_divergence(v)
    cnt = {k: sum(1 for r in rows if r["stance"] == k) for k in ("偏多", "中性", "偏空")}
    order = {"偏空": 0, "中性": 1}
    return {"updated_at": v["updated_at"][:16], "source": "slim" if slim else "auto", "counts": cnt,
            "n_talk": len(rows), "common_levels": cl, "divergence": dv,
            "bull": [r for r in rows if r["stance"] == "偏多"],
            "bear_neutral": sorted([r for r in rows if r["stance"] != "偏多"], key=lambda r: order[r["stance"]]),
            "warnings": warnings}


def model_strings(m):
    out = list(m["common_levels"]) + [m["divergence"]]
    for r in m["bull"] + m["bear_neutral"]:
        out += [r["line"]] + r["points"]
    return out


# Empty-info sentences (Manager 2026-11-25): 「未給…方向」「未給出…」「未談…」「沒有給…方向」「未提供…看法」
EMPTY_INFO_RE = re.compile(r"未給.{0,12}方向|沒有?給.{0,12}方向|未給出|未(?:談|提及?|提供)|沒(?:有)?(?:談|提)到?(?:BTC|比特幣|大餅)")


def banned_hits(strings):
    hits = []
    for s in strings:
        for b in BANNED:
            if b in s:
                hits.append(f"banned:{b}:{s[:30]!r}")
        m = EMPTY_INFO_RE.search(s)
        if m:
            hits.append(f"empty_info:{m.group()}:{s[:30]!r}")
    return hits


def self_test():
    bad = ["之後未給新的 BTC 方向", "未給出價位", "未談 BTC", "沒有給明確方向", "未提供後續看法", "未提 BTC"]
    ok = ["未平倉量日變化偏高", "未來數月看區間".replace("數月", "幾個月"), "未翻轉前缺乏高週期多頭論點",
          "尚未買進", "未有效翻轉前", "止跌條件未出現前先持有", "反彈尚未確認轉折"]
    fails = [x for x in bad if not banned_hits([x])] + [x for x in ok if banned_hits([x])]
    raw = json.load(open(os.path.join(HERE, "kol/latest_view.json")))
    live = banned_hits(model_strings(build_model(raw)))
    print("SELF_TEST", "PASS" if not fails and not live else "FAIL", fails, live)
    return not fails and not live


def _watermark(im):
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.text((im.size[0] // 2, im.size[1] // 2), WATERMARK, font=F(fs(110), True), fill=(255, 255, 255, 40), anchor="mm")
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def render_slim_page(m, kind, out, page_i, page_n, max_points, viol):
    boxes = []
    left, right = PAD_X + M, PAD_X + CONTENT_W - M
    bw = right - left
    ts = datetime.strptime(m["updated_at"], "%Y-%m-%d %H:%M")
    tag = {"overview": "總覽", "bull": "看多", "bear_neutral": "看空與中性"}[kind]
    n_talk = m["n_talk"]

    def put(d, xy, text, font, fill, anchor="lt", label=""):
        bb = d.textbbox(xy, text, font=font, anchor=anchor)
        boxes.append((label, bb)); d.text(xy, text, font=font, fill=fill, anchor=anchor)
        return bb

    def para(d, y, text, font, fill, label, x0=None, width=None):
        x0 = left if x0 is None else x0
        w0 = width or (right - x0)
        wrapped = wrap(d, text, font, w0)
        for shrink in (0.94, 0.88, 0.82):          # avoid a 1–2 character last line
            if len(wrapped) < 2 or len(wrapped[-1].strip()) > 2:
                break
            alt = wrap(d, text, font, w0 * shrink)
            if len(alt) == len(wrapped):
                wrapped = alt
        viol.extend(check_wrap_quality(text, wrapped, label))
        if "".join(wrapped).replace(" ", "") != text.replace(" ", ""):
            viol.append(f"{label}:text_lost")
        for i, ln in enumerate(wrapped):
            bb = put(d, (x0, y), ln, font, fill, label=f"{label}:{i}")
            y = bb[3] + GAP_LINE
        return y, len(wrapped)

    H = 9000
    im = Image.new("RGB", (TOTAL_W, H), TH["bg"])
    d = ImageDraw.Draw(im)
    y = PAD_Y + 70
    bb = put(d, (left, y), f"分析師觀點（{page_i}/{page_n}）", F(fs(84), True), TH["title"], label="title")
    y = bb[3] + GAP_LINE
    bb = put(d, (left, y), f"更新 {ts:%m/%d} 台北時間 {ts:%H:%M}　{tag}", F(fs(46)), TH["muted"], label="ts")
    y = bb[3] + 36

    if kind == "overview":
        x, rb = left, y
        items = [("偏多", m["counts"]["偏多"]), ("中性", m["counts"]["中性"]), ("偏空", m["counts"]["偏空"])]
        for k, n in items:
            t = f"{k} {n}"
            bb = put(d, (x, y), t, F(fs(56), True), SC[k], label=f"cnt:{k}")
            x += d.textlength(t, font=F(fs(56), True)) + 70; rb = max(rb, bb[3])
        y = rb + 24; x = left
        for k, n in items:
            if n:
                w = bw * n / max(1, n_talk)
                d.rectangle([x, y, x + w, y + 26], fill=SC[k]); x += w
        y += 46
        bb = put(d, (left, y), f"本期談到 BTC 共 {n_talk} 位", F(fs(40)), TH["muted"], label="n_talk")
        y = bb[3] + 40
        f, fb = F(fs(48)), F(fs(48), True)
        bb = put(d, (left, y), "共同價位", F(fs(52), True), TH["price"], label="cl:h")
        y = bb[3] + 20
        for i, item in enumerate(m["common_levels"]):
            lvl, _, note = item.partition("｜")
            lb = put(d, (left, y), lvl, fb, TH["price"], label=f"cl:{i}:lvl")
            x0 = left + d.textlength(lvl, font=fb) + 40
            parts = [x.strip() for x in note.split("；") if x.strip()]
            if len(parts) == 1 and d.textlength(note, font=f) <= right - x0:   # level + note on one line
                y2, nl = para(d, y, note, f, TH["text"], f"cl:{i}", x0=x0)
            else:                                     # one line per attribution under the level
                y2, nl = lb[3] + GAP_LINE, 0
                for k, part in enumerate(parts):
                    y2, n_ = para(d, y2, part, f, TH["text"], f"cl:{i}:{k}", x0=left + fs(40)); nl += n_
            if nl > 3:
                viol.append(f"cl:{i}:too_long:{nl}_lines")
            y = max(lb[3] + GAP_LINE, y2) + 18
        y += 16
        bb = put(d, (left, y), "主要分歧", F(fs(52), True), TH["title"], label="dv:h")
        y = bb[3] + 20
        y, _ = para(d, y, m["divergence"], f, TH["text"], "dv")
        y += 20
    else:
        cnt_line = "　·　".join(f"{'看多' if k == '偏多' else '看空' if k == '偏空' else '中性'} {m['counts'][k]} 位"
                                for k in (("偏多",) if kind == "bull" else ("偏空", "中性")))
        bb = put(d, (left, y), f"{cnt_line}　·　本期談到 BTC 共 {n_talk} 位", F(fs(46)), TH["muted"], label="sub")
        y = bb[3] + 28
        d.line([left, y, right, y], fill=TH["grid"], width=3); y += 28
        rows = m[kind]
        last = None
        for r in rows:
            st = r["stance"]
            if kind == "bear_neutral" and st != last:
                y += 8
                bb = put(d, (left, y), "看空" if st == "偏空" else "中性", F(fs(52), True), SC[st], label=f"sec:{st}")
                y = bb[3] + 20; last = st
            name = display_name(r["handle"])
            hf, sf = F(fs(56), True), F(fs(50), True)
            hb = put(d, (left, y), name, hf, TH["title"], label=f"h:{r['handle']}")
            sw = d.textlength(r["line"], font=sf)
            if sw <= right - (left + d.textlength(name, font=hf) + HANDLE_STANCE_GAP):
                sb = put(d, (right, y), r["line"], sf, SC[st], anchor="rt", label=f"st:{r['handle']}")
                y = max(hb[3], sb[3]) + 18
            else:
                y = hb[3] + 12
                y, _ = para(d, y, r["line"], sf, SC[st], f"st:{r['handle']}")
                y += 6
            bf = F(fs(46))
            for j, p in enumerate(r["points"][:max_points]):
                asc = d.textbbox((0, 0), "中", font=bf, anchor="lt")
                cy_, rr = y + (asc[3] - asc[1]) / 2, fs(7)
                d.ellipse([left + fs(14) - rr, cy_ - rr, left + fs(14) + rr, cy_ + rr], fill=TH["muted"])
                y, _ = para(d, y, p, bf, TH["text"], f"p:{r['handle']}:{j}", x0=left + fs(56))
            y += GAP_BLOCK
            d.line([left, y, right, y], fill=TH["grid"], width=2); y += GAP_AFTER_SEP
    bb = put(d, (left, y + 4), f"{WATERMARK}　·　整理 {n_talk} 位分析師近期談到 BTC 的重點　·　{page_i}/{page_n}",
             F(fs(36)), TH["muted"], label="foot")
    Hc = bb[3] + PAD_Y
    if Hc > H:
        viol.append(f"page{page_i}:canvas_overflow")
    im = im.crop((0, 0, TOTAL_W, min(Hc, H)))
    hits = check_overlaps(boxes)
    for a_, b_, _, _ in hits[:20]:
        viol.append(f"overlap:{a_}~{b_}")
    before = im.copy()
    im = _watermark(im)
    cx, cy = im.size[0] // 2, im.size[1] // 2
    reg = (cx - 300, cy - 60, cx + 300, cy + 60)
    wm_ok = before.crop(reg).tobytes() != im.crop(reg).tobytes()
    if not wm_ok:
        viol.append(f"page{page_i}:watermark_missing")
    im.save(out, "PNG", optimize=True)
    return {"page": f"{page_i}/{page_n}", "kind": kind, "path": out, "size": list(im.size),
            "aspect": round(im.size[1] / im.size[0], 3), "watermark": wm_ok}


def render_slim(v, out_prefix, use_slim=True):
    m = build_model(v, use_slim)
    for w in m["warnings"]:
        print("SLIM_WARN", w, file=sys.stderr)
    viol = banned_hits(model_strings(m))
    if not m["n_talk"]:
        viol.append("no_analysts")
    if not m["common_levels"]:
        viol.append("no_common_levels")
    pages = [("overview", 1), ("bull", 2), ("bear_neutral", 3)]
    metas, paths = [], []
    for kind, i in pages:
        out = f"{out_prefix}_p{i}of3.png"
        for mp in (MAX_POINTS, 3, 2):
            pv = []
            meta = render_slim_page(m, kind, out, i, 3, mp, pv)
            if meta["aspect"] <= ASPECT_MAX:
                break
        if meta["aspect"] > ASPECT_MAX:
            pv.append(f"page{i}:aspect {meta['aspect']}>{ASPECT_MAX}")
        meta["max_points"] = mp
        viol.extend(pv); metas.append(meta); paths.append(out)
    uniq = sorted(set(viol))
    if uniq:
        for p in paths:
            try: os.remove(p)
            except OSError: pass
        print(f"LAYOUT_CHECK FAIL {len(uniq)}", file=sys.stderr)
        for x in uniq[:40]:
            print(" ", x, file=sys.stderr)
        return None, m
    print("LAYOUT_CHECK PASS", file=sys.stderr)
    print("OVERLAP_CHECK PASS", file=sys.stderr)
    print("SLIM_SOURCE", m["source"], file=sys.stderr)
    for x in metas:
        print(json.dumps({k: x[k] for k in ("page", "kind", "size", "aspect", "watermark", "max_points")},
                         ensure_ascii=False), file=sys.stderr)
    return paths, m


def main():
    p = argparse.ArgumentParser(); p.add_argument("--out-prefix")
    p.add_argument("--slim-json", action="store_true", help="print the slim content model (JSON) and exit, no image")
    p.add_argument("--layout", choices=["auto", "slim", "legacy"], default="auto", help="auto = kol/kol_layout.json / KOL_SLIM")
    p.add_argument("--no-slim-file", action="store_true", help="ignore kol/latest_slim.json (test the automatic fallback)")
    p.add_argument("--self-test", action="store_true", help="banned / empty-info rule test + today's slim content")
    a = p.parse_args()
    if a.self_test:
        sys.exit(0 if self_test() else 1)
    try:
        raw = json.load(open(os.path.join(HERE, "kol/latest_view.json"))); assert raw.get("accounts")
    except Exception as e:
        sys.exit(f"no kol view: {e}")
    if a.slim_json:
        m = build_model(raw, not a.no_slim_file)
        m["enabled"] = slim_enabled() if a.layout == "auto" else a.layout == "slim"
        m["banned_hits"] = banned_hits(model_strings(m))
        print(json.dumps(m, ensure_ascii=False)); return
    os.makedirs(os.path.join(HERE, "out/kol"), exist_ok=True)
    prefix = a.out_prefix or os.path.join(HERE, f"out/kol/btc_kol_{datetime.now():%Y%m%d_%H%M%S}")
    slim = slim_enabled() if a.layout == "auto" else a.layout == "slim"
    if slim:
        paths, _ = render_slim(raw, prefix, not a.no_slim_file)
    else:
        paths = render_all(raw, prefix)
    if not paths:
        sys.exit(1)
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()

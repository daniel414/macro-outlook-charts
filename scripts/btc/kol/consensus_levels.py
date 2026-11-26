#!/usr/bin/env python3
"""Fair common levels for /btckol (2026-11-26, Daniel: 共同價位要公正、整份關注名單、不偏重某人).

Input : kol/latest_view.json -> accounts[].price_levels  (written by the 6:00/14:00/22:00 tracking routine)
        each entry {"d": "11/26", "k": "壓"|"撐"|"區", "lo": 64, "hi": 66}   values in K (thousand USD); hi optional
          壓/撐 = resistance / support the analyst named (target, defence, stop all count); 區 = a range whose lower edge is
          support and upper edge is resistance.
Rules : one analyst = one vote per cluster (any number of entries / posts collapses to one);
        entries older than LEVEL_DAYS (vs updated_at) expire; ranges wider than WIDE_PCT count at their two edges only;
        an entry is its midpoint price; a cluster is a window of +-TOL_PCT around an anchor price; the cluster needs MIN_VOTES distinct analysts;
        support and resistance are clustered separately; output ordered by votes, no names in public text.
Run   : python3 kol/consensus_levels.py [--explain] [--apply]
        --explain  print every cluster with analyst names (internal only)
        --apply    rewrite latest_slim.json common_levels, latest_view.json common_levels (legacy string), latest_view.md line
"""
import json, os, re, sys, statistics
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
VIEW = os.path.join(HERE, "latest_view.json")
SLIM = os.path.join(HERE, "latest_slim.json")
MDF = os.path.join(HERE, "latest_view.md")

TOL_PCT = 0.015      # window = anchor +- 1.5%
MIN_VOTES = int(os.environ.get("KOL_CONSENSUS_MIN", "3"))
LEVEL_DAYS = 7       # price_levels older than this are ignored
WIDE_PCT = 0.05      # ranges wider than 5% count only at their edges
MAX_LINES = 6


def _age_ok(d, ref):
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})", d or "")
    if not m:
        return False
    try:
        d0 = date(ref.year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return False
    if d0 > ref.date():
        d0 = date(ref.year - 1, d0.month, d0.day)
    return (ref.date() - d0).days <= LEVEL_DAYS


def segments(view):
    """-> [(handle, role, lo, hi)] with role in 撐/壓; one analyst's entries are not merged here."""
    ref = datetime.strptime(view["updated_at"][:16], "%Y-%m-%d %H:%M")
    out = []
    for a in view.get("accounts") or []:
        if a.get("stance") not in ("偏多", "偏空", "中性"):
            continue
        for e in a.get("price_levels") or []:
            if not _age_ok(e.get("d"), ref):
                continue
            lo = float(e["lo"]); hi = float(e.get("hi", lo))
            if hi < lo:
                lo, hi = hi, lo
            k = e.get("k")
            if k == "區":
                out += [(a["handle"], "撐", lo, lo), (a["handle"], "壓", hi, hi)]
            elif k in ("撐", "壓"):
                if lo != hi and (hi - lo) / lo > WIDE_PCT:
                    out += [(a["handle"], k, lo, lo), (a["handle"], k, hi, hi)]
                else:
                    out.append((a["handle"], k, lo, hi))
    return out


def _window(c):
    return c * (1 - TOL_PCT), c * (1 + TOL_PCT)


def _overlap(s, w):
    """a segment belongs to a window when its midpoint falls inside (a 62-64K zone is the price 63K, not 62K)."""
    return w[0] <= (s[2] + s[3]) / 2 <= w[1]


def _reps(segs, w, c):
    """one representative price per analyst inside window w: that analyst's own price nearest the anchor c."""
    by = {}
    for s in segs:
        if _overlap(s, w):
            by.setdefault(s[0], []).append((s[2] + s[3]) / 2)
    return {h: min(sorted(v), key=lambda x: abs(x - c)) for h, v in by.items()}


def cluster_role(segs):
    segs = list(segs); res = []
    while segs:
        cands = sorted({round(v, 4) for s in segs for v in (s[2], s[3], (s[2] + s[3]) / 2)})
        best = None
        for c in cands:
            w = _window(c); r = _reps(segs, w, c)
            key = (len(r), -(max(r.values()) - min(r.values())) if r else 0, -c)
            if best is None or key > best[0]:
                best = (key, c, w, r)
        _, c, w, r = best
        res.append({"votes": len(r), "who": sorted(r), "lo": min(r.values()), "hi": max(r.values()), "anchor": c})
        segs = [s for s in segs if not _overlap(s, w)]
    return res


def consensus(view, min_votes=None):
    mv = MIN_VOTES if min_votes is None else min_votes
    segs = segments(view)
    out = {}
    for role in ("壓", "撐"):
        cl = cluster_role([s for s in segs if s[1] == role])
        out[role] = sorted(cl, key=lambda x: (-x["votes"], x["anchor"] if role == "壓" else -x["anchor"]))
    keep = {r: [c for c in out[r] if c["votes"] >= mv] for r in out}
    return keep, out


def _k(v):
    t = f"{round(v, 1):.1f}"
    return t[:-2] if t.endswith(".0") else t


def level_text(c):
    a, b = _k(c["lo"]), _k(c["hi"])
    return f"{a}K" if a == b else f"{a}–{b}K"


_CN = "零一二三四五六七八九十"


def _cn(n):
    return _CN[n] if 0 < n <= 10 else str(n)   # Chinese numerals so a vote count never repeats a price digit


def common_lines(view, min_votes=None):
    """public lines for latest_slim.common_levels: 壓 first then 撐, each side ordered by votes; no names."""
    keep, _ = consensus(view, min_votes)
    lines = []
    for role in ("壓", "撐"):
        for c in keep[role]:
            lines.append((c["votes"], f"{role} {level_text(c)}｜{_cn(c['votes'])}人提到"))
    return [t for _, t in lines][:MAX_LINES] or [f"目前沒有價位達{_cn(MIN_VOTES)}人共識"]


def has_levels(view):
    return any(a.get("price_levels") for a in view.get("accounts") or [])


def legacy_string(view, min_votes=None):
    keep, _ = consensus(view, min_votes)
    parts = []
    if keep["撐"]:
        parts.append("支撐 " + "、".join(level_text(c) for c in keep["撐"]))
    if keep["壓"]:
        parts.append("壓力 " + "、".join(level_text(c) for c in keep["壓"]))
    return "｜".join(parts)


def explain(view):
    keep, allc = consensus(view)
    n = sum(1 for a in view["accounts"] if a.get("stance") in ("偏多", "偏空", "中性"))
    has = sum(1 for a in view["accounts"] if a.get("stance") in ("偏多", "偏空", "中性") and a.get("price_levels"))
    print(f"analysts talking BTC={n}, with price_levels={has}, MIN_VOTES={MIN_VOTES}, TOL={TOL_PCT:.1%}")
    for role in ("壓", "撐"):
        for c in allc[role]:
            flag = "SHOWN" if c["votes"] >= MIN_VOTES else "below"
            print(f"  {role} {level_text(c):<12} votes={c['votes']} {flag:<5} {','.join(c['who'])}")


def apply():
    view = json.load(open(VIEW, encoding="utf-8"))
    lines = common_lines(view)
    slim = json.load(open(SLIM, encoding="utf-8"))
    slim["common_levels"] = lines
    json.dump(slim, open(SLIM, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    view["common_levels"] = legacy_string(view)
    json.dump(view, open(VIEW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    md = open(MDF, encoding="utf-8").read().split("\n")
    for i, ln in enumerate(md):
        if ln.startswith("共同價位："):
            md[i] = "共同價位：" + view["common_levels"]
    open(MDF, "w", encoding="utf-8").write("\n".join(md))
    print("APPLIED", json.dumps(lines, ensure_ascii=False))


if __name__ == "__main__":
    v = json.load(open(VIEW, encoding="utf-8"))
    if "--explain" in sys.argv:
        explain(v)
    if "--apply" in sys.argv:
        apply()
    if len(sys.argv) == 1:
        print(json.dumps(common_lines(v), ensure_ascii=False, indent=1))

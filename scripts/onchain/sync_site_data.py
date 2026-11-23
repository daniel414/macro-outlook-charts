#!/usr/bin/env python3
"""Copy the on-chain JSON files used by fallback-onchain into scripts/onchain/site_data/.

Run this on a machine that can read the local 4dan-site data dir or https://4dan.app/data
(the box can; GitHub Actions currently cannot, because 4dan.app returns 403).
Picks the fresher of local vs site. Does not invent values. Exits non-zero if any required stem is missing.
"""
import json, os, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import onchain_selfchart as S0
import onchain_six  # updates S0.SERIES

STEMS = sorted(set(S0.SERIES.values()) | {"bv-hodl-waves"})
DEST = HERE / "site_data"
LOCAL = Path(os.environ.get("ONCHAIN_DATA_DIR") or S0.SITE_LOCAL)
UA = {"User-Agent": "4dan-fallback-onchain-sync/1.0"}


def last_time(obj):
    return S0._last_time(obj) or ""


def try_local(stem):
    p = LOCAL / (stem + ".json")
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8")), "local"


def try_site(stem):
    req = urllib.request.Request(S0.SITE_URL + stem + ".json", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")), "site"


def load_stem(stem):
    cands = []
    for fn in (try_local, try_site):
        try:
            got = fn(stem)
        except Exception as e:
            print("skip", fn.__name__, stem, e, file=sys.stderr)
            continue
        if got:
            cands.append((last_time(got[0]), got[1], got[0]))
    if not cands:
        raise RuntimeError("no source")
    return max(cands, key=lambda x: x[0])


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    missing = []
    for stem in STEMS:
        try:
            last, src, obj = load_stem(stem)
        except Exception as e:
            print("MISS", stem, e, file=sys.stderr)
            missing.append(stem)
            continue
        (DEST / (stem + ".json")).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(stem, src, last)
    if missing:
        sys.exit("missing " + ", ".join(missing))
    print("wrote", len(STEMS), "files to", DEST)


if __name__ == "__main__":
    main()

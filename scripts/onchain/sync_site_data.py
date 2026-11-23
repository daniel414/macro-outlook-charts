#!/usr/bin/env python3
"""Copy the on-chain JSON files used by fallback-onchain into scripts/onchain/site_data/.

Run this on a machine that can read the local 4dan-site data dir or https://4dan.app/data
(the box can; GitHub Actions currently cannot, because 4dan.app returns 403).
Does not invent values. Exits non-zero if any required stem is missing.
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


def load_stem(stem):
    name = stem + ".json"
    p = LOCAL / name
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8")), "local"
    req = urllib.request.Request(S0.SITE_URL + name, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")), "site"


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    missing = []
    for stem in STEMS:
        try:
            obj, src = load_stem(stem)
        except Exception as e:
            print("MISS", stem, e, file=sys.stderr)
            missing.append(stem)
            continue
        (DEST / (stem + ".json")).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(stem, src, S0._last_time(obj))
    if missing:
        sys.exit("missing " + ", ".join(missing))
    print("wrote", len(STEMS), "files to", DEST)


if __name__ == "__main__":
    main()

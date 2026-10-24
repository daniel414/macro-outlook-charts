#!/usr/bin/env python3
"""OKX long/short account ratio + realized funding -> data/positioning2.db table `positioning`.
Run every 15 min by run_collect.sh (split out of collect.py on 2026-10-24 when social-post collection was removed)."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btcsent.positioning import update, pos_con
t0 = time.time(); n = update(pos_con())
print(f"[{time.strftime('%F %T')}] okx positioning: ok, {n} rows upserted in {time.time()-t0:.1f}s")

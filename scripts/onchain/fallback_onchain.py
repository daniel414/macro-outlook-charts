#!/usr/bin/env python3
"""GitHub Actions FALLBACK for LINE /btccheckonchain. Runs the box's update_onchain.py (vendored unchanged; since 2026-11-17 it
builds the report from the public site data files at 4dan.app/data via onchain_six.py: templated text + two-column overview)
only when check_stale.py says the bot missed its 07:00 (UTC+8) run.  --mode dry-run: build, compare with the repo's current text, no writes.  --mode publish: publish."""
import argparse, difflib, json, os, subprocess, sys, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
a = argparse.ArgumentParser(); a.add_argument("--mode", choices=["dry-run", "publish"], default="dry-run"); a = a.parse_args()
cmd = [sys.executable, os.path.join(HERE, "update_onchain.py")] + (["--dry-run"] if a.mode == "dry-run" else [])
r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=900)
sys.stderr.write(r.stderr[-4000:])
if r.returncode: sys.exit(f"update_onchain.py failed ({r.returncode})")
if a.mode == "dry-run":
    req = urllib.request.Request(f"https://api.github.com/repos/{os.environ.get('GHR_OVERRIDE') or os.environ['GITHUB_REPOSITORY']}/contents/btc/onchain_latest.json",
                                 headers={"Authorization": f"Bearer {os.environ['GH_CHARTS_TOKEN']}", "Accept": "application/vnd.github.raw+json", "User-Agent": "fallback"})
    cur = json.load(urllib.request.urlopen(req, timeout=60))["text"]; new = open(os.path.join(HERE, "onchain_text.txt")).read()
    print("IDENTICAL" if cur == new else "DIFF (expected: time/price line + any site data updated since the bot's run)")
    print("\n".join(difflib.unified_diff(cur.splitlines(), new.splitlines(), "repo", "fallback", lineterm="", n=0)))

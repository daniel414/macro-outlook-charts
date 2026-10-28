#!/usr/bin/env python3
"""Stdlib-only staleness check (runs before any dependency install, so a FRESH run costs ~1 billed minute).
  check_stale.py btc       -> btc/latest.json: bot >= BOT_MAX_AGE_MIN (65) old, or our own fallback >= SELF_MAX_AGE_MIN (50)
  check_stale.py onchain   -> btc/onchain_latest.json older than today's ONCHAIN_DUE (06:30 UTC+8; bot routine 07:00)
Writes stale=true|false to $GITHUB_OUTPUT. FORCE=1 forces stale=true."""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone
TZ = timezone(timedelta(hours=8))
repo = os.environ["GITHUB_REPOSITORY"]; tok = os.environ["GH_TOKEN"]
def get(path):
    r = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{path}", headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw+json", "User-Agent": "fallback-check"})
    return json.load(urllib.request.urlopen(r, timeout=60))
tp = lambda s: datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
now = datetime.now(TZ); what = sys.argv[1]
if what == "btc":
    d = get("btc/latest.json"); mine = d.get("source") == "github-actions-fallback"
    lim = int(os.environ.get("SELF_MAX_AGE_MIN" if mine else "BOT_MAX_AGE_MIN", 50 if mine else 65))
    if os.environ.get("SIMULATE_AGE_MIN"):  # test only (workflow_dispatch simulate_stale): the fallback step is then forced to dry-run
        d["date"] = (now - timedelta(minutes=int(os.environ["SIMULATE_AGE_MIN"]))).strftime("%Y-%m-%d %H:%M"); mine = False; lim = int(os.environ.get("BOT_MAX_AGE_MIN", 65)); print("SIMULATE_AGE_MIN set")
    age = (now - tp(d["date"])).total_seconds() / 60; stale = age >= lim
    print(f"btc/latest.json date={d['date']} (UTC+8) source={'fallback' if mine else 'bot'} age={age:.0f}min limit={lim}min")
elif what == "onchain":
    d = get("btc/onchain_latest.json"); hh, mm = map(int, os.environ.get("ONCHAIN_DUE", "06:30").split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < due: due -= timedelta(days=1)
    stale = tp(d["date"]) < due
    print(f"btc/onchain_latest.json date={d['date']} (UTC+8) due={due:%Y-%m-%d %H:%M}")
else: sys.exit("usage: check_stale.py btc|onchain")
if os.environ.get("FORCE") == "1": stale = True; print("FORCE=1")
print("STALE -> fallback runs" if stale else "FRESH -> bot is publishing, nothing to do")
if os.environ.get("GITHUB_OUTPUT"):
    open(os.environ["GITHUB_OUTPUT"], "a").write(f"stale={str(stale).lower()}\n")

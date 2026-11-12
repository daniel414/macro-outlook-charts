#!/usr/bin/env python3
"""Stdlib-only staleness check (runs before any dependency install, so a FRESH run costs ~1 billed minute).
  check_stale.py btc       -> btc/latest.json: today's daily /btc is missing.
      Stale if last publish (bot or fallback) is before today's BTC_DUE (06:00 UTC+8),
      or age >= BOT_MAX_AGE_MIN (1560 min = 26h). A same-day bot or fallback publish is
      FRESH, so the 07:35/12:35/18:35 slots write at most once per day.
  check_stale.py onchain   -> btc/onchain_latest.json older than today's ONCHAIN_DUE (06:30 UTC+8; bot routine 07:00)
Writes stale=true|false to $GITHUB_OUTPUT. FORCE=1 forces stale=true."""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
SOURCE = "github-actions-fallback"


def tp(s):
    return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def get(path, repo, tok):
    r = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw+json", "User-Agent": "fallback-check"},
    )
    return json.load(urllib.request.urlopen(r, timeout=60))


def btc_due(now):
    hh, mm = map(int, os.environ.get("BTC_DUE", "06:00").split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < due:
        due -= timedelta(days=1)
    return due


def btc_staleness(d, now=None):
    """Return (stale, age_min, mine, lim_min). Stale if last publish (any source) is before today's 06:00, or age >= 26h."""
    now = now or datetime.now(TZ)
    mine = d.get("source") == SOURCE
    # Unified 26h cap for bot and fallback, so a fallback write is not refreshed later the same day.
    lim = int(os.environ.get("BOT_MAX_AGE_MIN", os.environ.get("SELF_MAX_AGE_MIN", 1560)))
    pub = tp(d["date"])
    age = (now - pub).total_seconds() / 60
    stale = pub < btc_due(now) or age >= lim
    return stale, age, mine, lim


def onchain_staleness(d, now=None):
    now = now or datetime.now(TZ)
    hh, mm = map(int, os.environ.get("ONCHAIN_DUE", "06:30").split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < due:
        due -= timedelta(days=1)
    stale = tp(d["date"]) < due
    return stale, due


def main():
    repo = os.environ.get("GHR_OVERRIDE") or os.environ["GITHUB_REPOSITORY"]
    tok = os.environ["GH_TOKEN"]
    now = datetime.now(TZ)
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "btc":
        d = get("btc/latest.json", repo, tok)
        if os.environ.get("SIMULATE_AGE_MIN"):
            d = dict(d)
            d["date"] = (now - timedelta(minutes=int(os.environ["SIMULATE_AGE_MIN"]))).strftime("%Y-%m-%d %H:%M")
            d["source"] = "bot"
            print("SIMULATE_AGE_MIN set")
        stale, age, mine, lim = btc_staleness(d, now)
        due = btc_due(now)
        print(f"btc/latest.json date={d['date']} (UTC+8) source={'fallback' if mine else 'bot'} age={age:.0f}min limit={lim}min due={due:%Y-%m-%d %H:%M}")
    elif what == "onchain":
        d = get("btc/onchain_latest.json", repo, tok)
        stale, due = onchain_staleness(d, now)
        print(f"btc/onchain_latest.json date={d['date']} (UTC+8) due={due:%Y-%m-%d %H:%M}")
    else:
        sys.exit("usage: check_stale.py btc|onchain")
    if os.environ.get("FORCE") == "1":
        stale = True
        print("FORCE=1")
    print("STALE -> fallback runs" if stale else "FRESH -> bot is publishing, nothing to do")
    if os.environ.get("GITHUB_OUTPUT"):
        open(os.environ["GITHUB_OUTPUT"], "a").write(f"stale={str(stale).lower()}\n")


if __name__ == "__main__":
    main()

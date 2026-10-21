#!/usr/bin/env bash
# Self-check before each /btc routine run. Restores missing scripts from daniel414/macro-outlook-charts scripts/btc,
# recreates out/ dirs, rebuilds .venv, restarts the collector. Never overwrites a local file that differs from repo.
# Output: one "SELFHEAL ..." summary line; "WARN ..." lines on failure.
cd "$(dirname "$0")"
export TZ=Asia/Taipei
REPO=https://github.com/daniel414/macro-outlook-charts.git
TMP=/tmp/selfheal_repo
SYNC="btc_structure.py ls_section.py posdata.py positioning_grid.py range_body.py whale_delta_chart.py selfheal.sh"
LOCALONLY="publish_btc.py config.json"   # repo copy is the GitHub Actions fallback / older calibration: restore only if missing
fixed=(); diff=(); warn=0
w(){ echo "WARN selfheal: $*"; warn=1; }
rm -rf "$TMP"; git clone -q --depth 1 "$REPO" "$TMP" 2>/dev/null || w "repo clone failed"
R="$TMP/scripts/btc"
for f in $SYNC $LOCALONLY; do
  if [ ! -f "$f" ]; then
    if [ -f "$R/$f" ]; then cp "$R/$f" "$f" && fixed+=("restored:$f") || w "restore $f failed"
      case " $LOCALONLY " in *" $f "*) w "$f restored from repo fallback copy; check it";; esac
    else w "$f missing and not in repo"; fi
  elif [ -f "$R/$f" ] && ! case " $LOCALONLY " in *" $f "*) true;; *) false;; esac; then
    [ "$(sha1sum <"$f")" = "$(sha1sum <"$R/$f")" ] || diff+=("$f")
  fi
done
[ -d btcsent ] || { cp -r "$R/btcsent" btcsent 2>/dev/null && fixed+=("restored:btcsent") || w "btcsent missing"; }
for d in out out/positioning out/publish out/grid out/kol; do [ -d "$d" ] || { mkdir -p "$d"; fixed+=("mkdir:$d"); }; done
if ! .venv/bin/python -c "import requests,numpy,pandas,matplotlib,PIL,bs4" 2>/dev/null; then
  python3 -m venv .venv && .venv/bin/pip install -q requests numpy pandas matplotlib pillow beautifulsoup4 yt_dlp curl_cffi scrapling >/dev/null 2>&1 \
    && fixed+=("venv-rebuilt") || w ".venv rebuild failed"
fi
tz=$(date +%Z%z); [ "$tz" = "CST+0800" ] || w "TZ is $tz (expected CST+0800)"
if [ "$(readlink -f /etc/localtime)" != "/usr/share/zoneinfo/Asia/Taipei" ]; then
  (sudo -n ln -sf /usr/share/zoneinfo/Asia/Taipei /etc/localtime 2>/dev/null && fixed+=("system-tz")) || w "system tz not Asia/Taipei (script uses TZ env)"
fi
pid=$(cat data/.collect_loop.pid 2>/dev/null)
if ! { [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; }; then ./ensure_collector.sh >/dev/null 2>&1; fixed+=("collector-restarted"); fi
pid=$(cat data/.collect_loop.pid 2>/dev/null); kill -0 "$pid" 2>/dev/null || w "collector not running"
[ ${#diff[@]} -gt 0 ] && echo "SELFHEAL note: local differs from repo (not overwritten): ${diff[*]}"
echo "SELFHEAL $([ $warn = 0 ] && echo OK || echo WARN) tz=$tz collector=${pid:-none} fixed=[${fixed[*]}] differ=[${diff[*]}]"

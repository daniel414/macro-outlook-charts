#!/usr/bin/env bash
# Periodic job (every 15 min): positioning data only. Social-post scraping/scoring (collect.py) removed 2026-10-24.
cd "$(dirname "$0")"
exec 9>data/.collect.lock
flock -n 9 || { echo "previous run still active, skipping"; exit 0; }
. .venv/bin/activate
# OKX 收集已停（2026-11-26 授權移除，Manager 核准）：okx_positioning.py 不再執行
# positioning data for positioning_grid.py (Gate/Bitget/Deribit/Binance/Kraken, keyless; OKX off; Bitfinex skipped unless POSDATA_BITFINEX=1)
timeout 300 python posdata.py >> logs/posdata.log 2>&1 || echo "[$(date '+%F %T')] posdata exit $?" >> logs/posdata.log

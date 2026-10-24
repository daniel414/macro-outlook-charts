#!/usr/bin/env bash
# Periodic job (every 15 min): positioning data only. Social-post scraping/scoring (collect.py) removed 2026-10-24.
cd "$(dirname "$0")"
exec 9>data/.collect.lock
flock -n 9 || { echo "previous run still active, skipping"; exit 0; }
. .venv/bin/activate
# OKX long/short account ratio + funding -> positioning2.db table positioning
timeout 120 python okx_positioning.py >> logs/okx_positioning.log 2>&1 || echo "[$(date '+%F %T')] okx_positioning exit $?" >> logs/okx_positioning.log
# positioning data for positioning_grid.py (OKX/Gate/Bitfinex/Bitget/Deribit/Binance/Kraken, keyless)
timeout 300 python posdata.py >> logs/posdata.log 2>&1 || echo "[$(date '+%F %T')] posdata exit $?" >> logs/posdata.log

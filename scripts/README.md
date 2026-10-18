# GitHub Actions 備援（fallback）

Grok Bot 用量用完、box 暫停時，讓 LINE 指令的非 AI 內容繼續更新。**bot 仍是主要發佈者**；備援只在檔案過期時才寫入。

| workflow | 排程（UTC+8） | 過期規則 | 寫入 |
|---|---|---|---|
| fallback-btc | 每小時 :35 | btc/latest.json：bot 發佈 ≥ 80 分鐘，或備援自己發佈 ≥ 50 分鐘 | btc/btc_positioning_*.jpg、btc/latest.json（加 `source: github-actions-fallback`、`ai_text_from`） |
| fallback-onchain | 每天 08:40、12:40 | btc/onchain_latest.json 早於當天 06:30 | btc/btc_onchain_*.jpg、btc/onchain_latest.json |

- 腳本是 box 上 `/workspace/btc-sentiment/` 的原檔複製（posdata.py、positioning_grid.py、whale_delta_chart.py、btcsent/、config.json、onchain/update_onchain.py 完全相同；publish_btc.py 只改了讀 box 專用檔 `/workspace/big-players/line_push.py` 那一行）。box 上的腳本改了，請同步複製到這裡。
- AI 文字（lead_text、【散戶 vs 大戶】裡的「散戶：／大戶：／解讀：」句子）不會產生；沿用 bot 最後一次的版本最多 3 小時（同 publish_btc.py 對 lead_text.txt 的規則），之後省略。
- 寫入用 Contents API（和 bot 一樣），寫 latest.json 前會再檢查一次，bot 若剛好發佈就放棄。
- 手動測試：Actions → workflow → Run workflow，mode=dry-run（只產生、比對，不寫入）。
- 【技術結構（客觀數據，非投資建議）】（2026-10-18 加入，Daniel 經 Manager 核准）：`btc/btc_structure.py`（只用標準庫）算 BTC 現價相對今年／本月／本週開盤價（UTC 00:00 日線、週一開盤）與日線 MA120（最近 120 根已收日線收盤平均）的位置，並依最近一根已收日線收盤在 MA120 上方／下方標「多方結構／空方結構」（只用這兩個詞，不含做多／做空／進場、勝率或回測）；資料 OKX BTC-USDT 1Dutc，抓不到改 Coinbase BTC-USD。`publish_btc.py` 的 `add_structure()` 用 subprocess 執行它，插在【Binance…】之前（沒有 Binance 段就放最後）；`fallback_btc.py` 的 `summary()` 也會呼叫。任何失敗或逾時（45 秒）整段省略，不影響發佈。

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

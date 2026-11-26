# kol/latest_slim.json（/btckol 精簡版內容，btc-3 每次更新 latest_view.json 後一併寫）

- as_of：必須與 latest_view.json 的 updated_at 完全相同（YYYY-MM-DD HH:MM），不同就視為過期，kol_chart.py 改用自動精簡並印 SLIM_WARN。
- common_levels：最多 6 條，由高到低，格式「壓 64–65K｜KillaXBT：說明；SailorManCrypto：說明」。每條說明 ≤ 2 個人、每人一句完整短句，不截斷。
- divergence：一段 ≤ 90 字的主要分歧。
- accounts：只放立場為偏多／偏空／中性、且 7 天內有談 BTC 的人；未談 BTC 的人不要放。
  - handle：與 latest_view.json 相同。
  - line：一句立場，≤ 16 字。
  - points：2–4 條重點，每條 ≤ 30 字，只留近 3 天仍有效的價位；不寫舊日誌、個股、標的不明的貼文、重複內容。
  - omit：true 表示這次不顯示此人（選填）。
- 禁用字（出現就整組圖不發）：未談、標的無法確認、無法確認、不列入、不列為、屬個股、未註明、未給 BTC、數天、數週、數日、網址、距今、距離、距現價。
- 價位寫 K（例 58.4–58.7K），文字訊息會自動轉成 58,400–58,700。鋒哥寫「鋒哥」，其他人寫 X 帳號、不加 @。

## common_levels 改為程式計算（2026-11-26，Daniel：要公正）
- slim 的 common_levels 不再由 routine 判讀後手寫。kol_chart.py 每次出圖／出文字時，用 kol/consensus_levels.py 從 latest_view.json 每位分析師的 price_levels 重算（一人一票、±1.5% 合併、至少 3 人）。
- routine 只需要維護每位分析師的 price_levels（見 kol/README.md）；slim 裡的 common_levels 可留空或照舊，會被覆蓋。
- 沒有任何帳號有 price_levels 時才退回 slim 原本的清單，並印 SLIM_WARN consensus_no_price_levels。
- 手動同步檔案：python3 kol/consensus_levels.py --apply（寫 latest_slim.json、latest_view.json 的 common_levels、latest_view.md 共同價位）。查各價位有誰提：python3 kol/consensus_levels.py --explain

KOL X posts (tracked accounts, see kol_accounts.json). Files: kol_accounts.json (id, since_id), raw/posts.jsonl (deduped), ingest.py.
Incremental fetch per account (x MCP, write_to_file=true):
  get_users_posts id=<id> max_results=25 exclude=replies,retweets post.fields=created_at,public_metrics,note_tweet since_id=<since_id>
then: python3 ingest.py <username> <result_file> ...   (updates since_id, prints quota left)

Account roster (as of 2026-11-26): read usernames from kol_accounts.json.
Do NOT fetch @xtony1314 (removed earlier; same numeric id as former @xy_xtony).
Do NOT fetch @xy_xtony, alias @XiaoYao_Capital, or Telegram t.me/s/XiaoYao_Cap (托尼老師 removed from /btckol 2026-11-26).
Do NOT fetch @Vida_BWE (removed from /btckol 2026-11-02; X account is protected).

Special-case accounts:
  - @zemummy1 — 十老板；normal X fetch

Cost: one get_users_posts per top-level account in kol_accounts.json, plus one per aliases[] entry that has an id, per full round. Analysis 2026-10-14 in analysis_20261014.md.
YouTube（鋒哥 traderfengge，頻道 UCqKGLcPxejYCq0rKN51sDJg @Traderfengge）：每次 KOL 更新先跑 python3 youtube_check.py
  -> 有新影片會轉錄（字幕優先，沒有就 faster-whisper small，float32；int8 在本機出亂碼）到 youtube/<id>.txt 並列入 youtube/pending.json
  -> 讀轉錄摘 BTC 方向/價位/理由，寫 summary.json 後跑 python3 youtube_apply.py summary.json（更新 latest_view.json traderfengge 條目、stance_history、標記完成）

Phone image layout (live 2026-11-02): kol_chart.py renders 1–2 pages (safe pad 700/500, atomic token wrap, kinsoku, single-line stance labels, one-level-per-line common levels). Auto-condenses levels/reason/stance from latest_view.json. Layout + overlap checks must PASS or the run exits non-zero and publish_btc keeps the previous kol_latest. Worker still uses kol_image_url (page 1); page 2 is kol_image_url_2 / kol_images[].

Classification rule (asset filter, 2026-11-13):
  判讀每則貼文時先確認標的。內文、附圖、交易對或前後文出現股票代號或公司名（如 NVDA、NVDAUSDT、英偉達、輝達、NVIDIA、AAPL、蘋果、TSLA、特斯拉及其他個股；股票代號＋USDT 的合約同樣視為該股）時，該則不得寫入 BTC 價位、理由、總結、共同價位或主要分歧，也不得用來判定 BTC 立場。
  MSTR／Strategy 與 BTC 相關者可保留為 BTC 背景（例如指數剔除、持倉均價），但不得把 MSTR 股價交易本身當成 BTC 交易。
  有附圖的交易貼文（開倉、止損、減倉、加倉、平倉、喊單）必須先確認標的（看圖中交易對、商品名，或同一串貼文的前文／圖說）才能歸為 BTC 交易；只寫「開多／止損／減倉」而標的無法確認者，不列為 BTC 交易。
  若某人近期只談個股或其他市場、沒有可確認的 BTC 內容，個人段落只留先前真正談 BTC 的內容，或標「未談BTC」；不得把個股交易寫進該人的 BTC 段落。立場只依 BTC 內容重判。

price_levels（2026-11-26，公正共同價位）：latest_view.json 每位分析師可有 price_levels: [{"d":"11/26","k":"壓|撐|區","lo":64,"hi":66}]，單位 K。
  k=壓／撐：該分析師明確點名的壓力／支撐（目標價、防守價、止損價都算）；k=區：一個區間，下緣算撐、上緣算壓。
  d 為貼文日期，超過 7 天自動失效。同一人同一價位講幾次只算一票。價位跟著最新貼文維護：被本人收回或明顯失效的就刪掉。
  共同價位由 kol/consensus_levels.py 算：一人一票、中點落在 ±1.5% 內併成一區、至少 3 人才列、依人數排序、支撐壓力分開、不寫名字。

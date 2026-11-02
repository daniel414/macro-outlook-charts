KOL X posts (tracked accounts, see kol_accounts.json). Files: kol_accounts.json (id, since_id), raw/posts.jsonl (deduped), ingest.py.
Incremental fetch per account (x MCP, write_to_file=true):
  get_users_posts id=<id> max_results=25 exclude=replies,retweets post.fields=created_at,public_metrics,note_tweet since_id=<since_id>
then: python3 ingest.py <username> <result_file> ...   (updates since_id, prints quota left)

Account roster (as of 2026-11-02 golive): read usernames from kol_accounts.json.
Do NOT fetch @xtony1314 (removed; same numeric id as @xy_xtony — use @xy_xtony).
Do NOT fetch @Vida_BWE (removed from /btckol 2026-11-02; X account is protected).

Special-case accounts:
  - @xy_xtony — main 托尼老師 view; also read aliases @XiaoYao_Capital
  - @XiaoYao_Capital — 托尼老師資金／公告號；BTC 併入 xy_xtony 時可略過獨立價位
  - @zemummy1 — 十老板；normal X fetch

Telegram (托尼老師 official channel, public preview — no join required):
  curl -sL -A "Mozilla/5.0" https://t.me/s/XiaoYao_Cap > /tmp/tg_xiaoyao.html
  Parse recent public messages for BTC / QQQ / size / levels; messages may auto-delete (≈1 week–1 month).
  Channel URL: https://t.me/XiaoYao_Cap | Preview: https://t.me/s/XiaoYao_Cap
  Fold TG BTC notes into @xy_xtony stance (sources include tg:XiaoYao_Cap).

Cost: one get_users_posts per account in kol_accounts.json per full round. Analysis 2026-10-14 in analysis_20261014.md.
YouTube（鋒哥 traderfengge，頻道 UCqKGLcPxejYCq0rKN51sDJg @Traderfengge）：每次 KOL 更新先跑 python3 youtube_check.py
  -> 有新影片會轉錄（字幕優先，沒有就 faster-whisper small，float32；int8 在本機出亂碼）到 youtube/<id>.txt 並列入 youtube/pending.json
  -> 讀轉錄摘 BTC 方向/價位/理由，寫 summary.json 後跑 python3 youtube_apply.py summary.json（更新 latest_view.json traderfengge 條目、stance_history、標記完成）

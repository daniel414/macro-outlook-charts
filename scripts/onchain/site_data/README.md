# site_data（Actions 後援資料）

GitHub Actions 跑 fallback-onchain 時，本機 box 路徑不存在，且 4dan.app/data 會對 runner IP 回 403。
這裡放正式站同一批可公開讀取的資料檔，供 load_one / load_hodl 在 runner 上讀取。

更新方式（在有本機 4dan-site 或能連 4dan.app 的環境）：

    python3 scripts/onchain/sync_site_data.py

不要在這個目錄改數字或自行估算鏈上值。

# X Article 補掃報告 — 2026-04-05

## 結論
已完成一輪保守補掃，針對 `memory/bookmarks/**/*.md` 中疑似 X Article 空殼書籤做正文補全。
本輪只處理高信心命中項目，避免誤把普通推文當成 X Article 重寫。

## 掃描結果
- 書籤總數：237
- 候選空殼書籤：133
- 確認為 X Article 空殼：10
- 成功補全：10
- 跳過：123（多數為普通推文、登入頁噪音或非 Article）

## 成功補全的 10 個檔案
- `memory/bookmarks/03-video-prompts/2039260164252180797.md`
- `memory/bookmarks/01-openclaw-workflows/2021247940321247553.md`
- `memory/bookmarks/01-openclaw-workflows/2023776478446436696.md`
- `memory/bookmarks/01-openclaw-workflows/2023957499183829467.md`
- `memory/bookmarks/01-openclaw-workflows/2033408970556096980.md`
- `memory/bookmarks/01-openclaw-workflows/2029237187599036671.md`
- `memory/bookmarks/01-openclaw-workflows/2024951908864328030.md`
- `memory/bookmarks/01-openclaw-workflows/2023152379571626312.md`
- `memory/bookmarks/02-seo-geo/2036970499927138762.md`
- `memory/bookmarks/02-seo-geo/2029159691272765880.md`

## 修改方式
每個命中檔案都遵守以下原則：
- 保留原有 frontmatter 與既有 metadata
- 將主內容替換為 `fxtwitter API -> tweet.article.content.blocks` 抽回的正文
- 保留原本已有的 `## 🧵 Thread 全文`、`## 📝 AI 濃縮` 等補充區塊
- 同步更新 `memory/bookmarks/search_index.json` 中對應 entry 的 title / summary

## 跳過原因（摘要）
- 普通推文，但 Jina 抓到登入頁噪音
- `source: failed`，但 thread 內容已足夠，不值得硬改
- 純 t.co 連結，但不是可還原的 X Article
- 真正空殼，且 fxtwitter 也無法補出正文

## 流程更新
本次也已把 X Article 支援正式併入 x-knowledge-base：
- enrichment 支援用 fxtwitter API 讀 X Article 長文
- 若主 tweet 幾乎只有 t.co 且導向 X Article，正文會直接升級為主內容源，而不只是外鏈補充

## 備註
- `memory/bookmarks/` 目前不在 git 追蹤內，所以這批資料修復本身沒有 git commit hash
- 可追蹤的流程程式變更已另行 push 到 x-knowledge-base GitHub repo

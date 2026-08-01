
## 2026-07-13 架構改進（與 Claude 對談整理，主人核准項目 1/3/4）

- [x] **回音室防護（provenance 降權）**：對話蒸餾回庫的知識標記來源（external / self-derived），claim 等級新增 Self-derived 級；recall 評分時 self-derived 降權，wiki absorb gate 對引用自身的內容從嚴。防止「收藏 → 召回 → 討論 → 再入庫」閉環讓既有觀點自我強化。
- [x] **論點級 embedding**：build_vector_index 除標題＋摘要外，將卡片「關鍵論點」section 逐條單獨 embed，vector 指回同一張卡。召回粒度從卡片級提升到論點級。現有約 1,300 卡規模下向量數約 x3-4，成本無感。
- [x] **長文 map-reduce ingest**：卡片生成目前截斷 4,000 字元，對論文／長影片損失不可逆。長文來源改分段摘要再合併（map-reduce），或至少保留分段中間產物供重建。

> 2026-07-14 更新：以下三項已由 Claude 實作完成並通過 smoke test
> （報告：/tmp/xkb-smoke-20260714.md，備份：scripts/*.bak-20260714）

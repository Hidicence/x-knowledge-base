<p align="right">
  <a href="./README.md">English</a> · <strong>繁體中文</strong>
</p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="XKB — 讓你的 Agent 共用同一層知識：Claude Code、OpenClaw、Codex 連進同一個本機優先服務，共享證據卡、wiki 主題與對話軌跡">
</p>

<p align="center">
  <a href="#快速開始"><strong>快速開始</strong></a> ·
  <a href="#接上你的-agent"><strong>接上 Agent</strong></a> ·
  <a href="#召回怎麼決定"><strong>召回怎麼決定</strong></a> ·
  <a href="./docs/xkb-memory-service.md"><strong>服務 API</strong></a> ·
  <a href="./docs/data-flow.md"><strong>隱私</strong></a> ·
  <a href="https://youtu.be/JWgm6ky_pys"><strong>概念影片</strong></a>
</p>

<p align="center">
  <a href="./LICENSE"><img alt="授權：PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-E07A3F"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="Local-first" src="https://img.shields.io/badge/data-local--first-59D8C8">
</p>

## 每個 Agent 都從零開始

你手上不只一個 AI Agent。每一個開場時都不知道你上週決定了什麼、哪條路已經試過而且失敗、你這半年在讀什麼。於是你重講一次，它們重新推導一次。

**XKB 是它們共用的那一層知識。** 它把你的來源轉成可追溯出處的證據卡，把耐久的結論蒸餾進 wiki，再透過同一個本機優先的 API 供應出去——讓 Claude Code、OpenClaw、Codex 讀的是同一份記憶，而不是各自為政。

---

## 一次召回實際回傳什麼

```console
$ curl -s localhost:18972/v1/recall -H "Authorization: Bearer $TOKEN" \
       -H 'Content-Type: application/json' --data-binary @query.json
```

```jsonc
{
  "retrieval_mode": "xbrain_hybrid",       // 確實跑了向量搜尋
  "count": 5,
  "dropped_as_irrelevant": 5,              // 而且丟掉了一半不夠相關的
  "records": [
    { "record_type": "knowledge_chunk",
      "score": 0.604,                      // 實測餘弦相似度，不是排名分數
      "rank_score": 0.888,                 // backend 原本用來排序的值
      "source_url": "https://…" }
  ],
  "filtered_counts": { "total": 0, "by_layer": { "card": 0, "wiki": 0 } },
  "warnings": ["5 semantic results dropped below the relevance floor"]
}
```

問一件你的知識庫根本沒有的事，它會說沒有，而不是把最不糟的十筆丟給你：

```jsonc
{ "count": 0, "retrieval_mode": "keyword_fallback",
  "warnings": ["semantic results found but 10 dropped below the relevance floor"] }
```

這個區別就是重點。「沒有資料」和「檢索壞掉」看起來一模一樣，而事後要分辨非常困難。

---

## 為什麼結果不一樣

### 相關度是量出來的，不是假設的

混合檢索回傳的是**排名**分數：它說的是「這筆排第一」，不是「這筆相關」。在真實的知識庫上，第一名永遠落在 `0.88` 附近，不管有沒有相關內容——實測時一個離題問題拿到 `0.863`，一個切題問題只有 `0.862`。XKB 會重新計算真實的查詢/文件餘弦相似度，低於門檻就丟掉；所以不相關的問題回傳空的，也不花錢。

### 每個說法都留著它的憑據

來源會變成九段式知識卡，帶著原始網址與**可信度分級**——`Attested`、`Scholarship`、`Inference`。半年後這張卡再被撈出來時，你看得出它當初是哪一種說法、誰說的。

### 蒸餾兩個方向都有閘門

卡片是證據，wiki 主題是理解。這條線不會自動跨越：吸收閘門會評估主題契合度與重複度，而且**故障時關閉**——閘門跑不動就什麼都不吸收。從 Agent 捕捉的對話只會變成**候選**，通過審查前不算知識。

### 知識可以退場

召回會記錄每筆知識被撈出來後，有沒有任何一次夠格被使用。被反覆撈出卻從未達標的，會列進退場候選——**只列出，不刪除**。可追溯性才是產品本身，不為了整潔丟掉證據。

---

## 快速開始

最小可用路徑：攝取本機 Markdown 並搜尋它。不需要 X cookies、不需要 Postgres、不需要排程。

**1 · Clone 並建立私有工作區**

```bash
git clone https://github.com/Hidicence/x-knowledge-base.git
cd x-knowledge-base
python3 scripts/xkb_init.py          # 產生 .xkb.json，已在 gitignore
```

**2 · 指定一個模型**

```bash
export LLM_API_URL="https://your-provider.example/v1"
export LLM_API_KEY="your-key"
export LLM_MODEL="your-model"
```

> 憑證屬於執行期狀態，不是版本庫內容。XKB 不綁定供應商，本機模型也可以。

**3 · 攝取、建索引、提問**

```bash
python3 scripts/local_ingest.py demo/sample-notes --category learning --limit 3
bash    scripts/build_search_index.sh
python3 scripts/xkb_ask.py "這些筆記之間有什麼共通模式？"
```

卡片與索引寫進你的工作區，不會寫進這個版本庫。

---

## 接上你的 Agent

啟動服務，然後安裝 hook。召回與回存就變成自動的——不必指望 Agent 記得要呼叫什麼。

```bash
python3 scripts/xkb_knowledge_service.py          # 127.0.0.1:18972
python3 scripts/xkb_install_agent_hook.py --install
```

```text
UserPromptSubmit  →  turns/start     →  召回的知識注入上下文
Stop              →  turns/complete  →  這次交談成為 L1 證據
```

安裝是冪等的，不會動到你其他的 hook，設定檔以原子方式寫入。`--uninstall` 移除的正好是它加的東西。

**讀取一律 fail-open。** 服務連不上時 hook 靜默放行，絕不擋住對話。這與吸收閘門刻意相反——閘門故障必須擋住，因為寫進錯的知識比沒寫更糟。

### 從另一台機器連

服務只綁 loopback，沒有 token 時拒絕在對外介面啟動。要從筆電連進來，用通道而不是開 port：

```bash
ssh -N -L 18972:127.0.0.1:18972 your-server
```

身分由 bearer token 決定，token 綁定 namespace 與權限；宣告別的 namespace 的請求會被**拒絕**，而不是默默改用別的。沒設定 token 時服務維持匿名，那是單人使用的預設。

---

## 召回怎麼決定

```text
訊息
   │
   ├─ 招呼或確認 ─────────────────────────► 完全跳過，連 embedding 都不呼叫
   │
   ▼
語意搜尋  ──►  實測相似度  ──►  低於門檻？  ──► 丟棄
   │                                            │
   ▼                                            ▼
wiki 主題 · 證據卡 · 對話軌跡          回傳空的，並說明原因
   │
   ▼
namespace ACL（故障時關閉）  ──►  帶來源的上下文
```

每個回應都會回報 `retrieval_mode`、ACL 擋掉了哪幾層、有多少筆因不相關被丟掉——所以結果很少時永遠解釋得出來，而不是一團謎。

---

## 來源

同一套卡片契約，多種輸入：

```bash
python3 scripts/local_ingest.py ~/notes --category research    # Markdown / 純文字
python3 scripts/pdf_ingest.py paper.pdf                        # PDF / 論文
python3 scripts/fetch_youtube_playlist.py <playlist-url>       # 影片逐字稿
python3 scripts/fetch_github_repos.py                          # star 與 fork
python3 scripts/media_ingest.py <file.md> --limit 4            # 圖片 OCR + 視覺註記
```

另含 X/Twitter 書籤匯入、PubMed，以及 Minions 佇列式的加值流程，詳見 [`SKILL.md`](./SKILL.md)。

---

## 執行模式

從能解決你問題的最小模式開始。

| 模式 | 檢索方式 | 需要 |
| --- | --- | --- |
| **Lite** | `search_index.json` 關鍵字 | Python + 一個 LLM |
| **Enhanced** | 平面向量索引，語意召回 | 一個 embedding 供應商（Gemini、OpenAI 或本機 Ollama） |
| **Full** | XBrain/GBrain 混合檢索 + RRF | Postgres + pgvector |

召回會依這個順序降級，而且一定會告訴你實際跑的是哪一種。

---

## 這個專案不是什麼

講清楚比事後失望便宜：

- **不是雲端服務。** 它跑在你的機器上，處理你的檔案。
- **不是全自動。** 沒有你點頭，不會有東西被升進 wiki，也不會有東西被退場。
- **不是 Agent 框架。** 它負責存放與回傳知識，思考是你的 Agent 的事。
- **還很早期。** 服務層、跨 Agent hook、退場訊號都是新的，介面還會變動。

使用雲端 embedding 代表查詢會離開你的機器；設定 `EMBEDDING_PROVIDER=ollama` 可以全部留在本機。哪些資料送到哪裡，詳見 [`docs/data-flow.md`](./docs/data-flow.md)。

---

## 文件

| 文件 | 內容 |
| --- | --- |
| [`docs/xkb-memory-service.md`](./docs/xkb-memory-service.md) | 服務 API、身分驗證、Agent hook、相關度門檻 |
| [`docs/data-flow.md`](./docs/data-flow.md) | 哪些資料離開你的機器，以及如何停止 |
| [`docs/RUNTIME_PATHS.md`](./docs/RUNTIME_PATHS.md) | 程式碼與你的資料的界線 |
| [`SKILL.md`](./SKILL.md) | 完整指令清單 |
| [`wiki/WIKI-SCHEMA.md`](./wiki/WIKI-SCHEMA.md) | wiki 主題契約 |

## 授權

[PolyForm Noncommercial 1.0.0](./LICENSE) — 非商業用途免費。

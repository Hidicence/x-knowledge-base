<p align="right">
  <a href="./README.md">English</a> · <strong>繁體中文</strong>
</p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="XKB — 讓你的 Agent 共用同一層知識：Claude Code、OpenClaw、Codex 連進同一個本機優先服務，共享證據卡、wiki 主題與對話軌跡">
</p>

<p align="center">
  <a href="#快速開始"><strong>快速開始</strong></a> ·
  <a href="#運作方式"><strong>運作方式</strong></a> ·
  <a href="#九段式知識卡"><strong>卡片格式</strong></a> ·
  <a href="#四種召回方式"><strong>召回層</strong></a> ·
  <a href="#讓多個-agent-共用"><strong>跨 Agent 共用</strong></a> ·
  <a href="./docs/data-flow.md"><strong>隱私</strong></a>
</p>

<p align="center">
  <a href="./LICENSE"><img alt="授權：PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-E07A3F"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="Local-first" src="https://img.shields.io/badge/data-local--first-59D8C8">
</p>

## 知識不該在存下來之後就消失

書籤、筆記、逐字稿、論文、專案不斷累積。難的從來不是存下來，而是在你真正需要的時候，把對的那一則連同它的證據找回來。

**XKB 是一套本機優先的知識生命週期。** 它把多種來源轉成同一種結構化卡片格式，用語意把它們找回來，把耐久的部分蒸餾成人看得懂的 wiki，並在對話當下把該出現的知識推到你面前。

而因為你的 Agent 總是從零開始，它現在也把這一切透過**同一個共享 API** 供應出去——讓 Claude Code、OpenClaw、Codex 讀的是同一份記憶，而不是各自為政。

---

## 這個閉環

多數知識工具是漏斗：東西倒進去，然後你自己去撈。XKB 把圈子接起來，而**槓桿就在接起來的那一刻**。

```text
        外部來源                            ┌──────────────────────┐
   筆記 · 書籤 · 影片 · 專案 ─────────────►│                      │
                                            │       證據           │
   ┌────────────────────────────────────────┤    九段式知識卡      │
   │                                        │    來源 · 可信度     │
   │  Claude Code · OpenClaw · Codex        └───────────┬──────────┘
   │        │                    ▲                      │
   │        │ 工作發生           │ 召回                 ▼
   │        ▼                    │              ┌───────────────┐
   │   對話 ──────► 候選記憶     │              │   吸收閘門    │
   │                      │      │              └───────┬───────┘
   │                      ▼      │                      ▼
   │                 ┌────────────────┐         ┌──────────────┐
   └────────────────►│    知識服務    │◄────────┤  wiki 主題   │
                     └────────────────┘         └──────────────┘
```

把它當成一個循環來讀：**工作產生證據，證據經過治理變成理解，理解以召回的形式回來，而更好的召回讓下一次工作更好。** 你在 Claude Code 花一個下午弄懂的事，下週 OpenClaw 就能用上——你不必手動搬任何東西。

### 閉環通常會腐化，這裡靠什麼擋住

一個會從自己的產出學習的系統會漂移：它召回自己寫的東西、附和自己，然後慢慢把觀點變成事實。四件事守住這條線：

- **來源有分級。** 從你自己的對話蒸餾出來的知識會被標記，召回時的權重低於外部來源的證據（預設扣 `0.15`）。這個閉環分得出自己的聲音和世界的聲音。
- **重複不等於證據。** 一個候選必須在**不同的 episode** 都有支持，才有資格進入審查。同一場對話裡講兩次不算數。
- **沒有東西能自己升級。** 對話只會變成候選，不會變成知識。吸收閘門站在證據與理解之間，而且故障時關閉。
- **刻意召回反對意見。** 反例層會把你自己記錄過的限制與失敗翻出來，讓「快速收斂」不等於「盲目收斂」。

結果是一個會累積、而不是會回音的閉環——而且每一跳都可以檢查：召回了什麼、過濾掉什麼、擱置了什麼、為什麼。

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
  "judge": { "status": "judged", "floor": 0.1,
             "considered": 15, "kept": 9, "dropped": 6 },
  "records": [
    { "record_type": "knowledge_chunk",
      "score": 0.604,                      // 實測餘弦相似度，不是排名分數
      "rank_score": 0.888,                 // backend 原本用來排序的值
      "judge": 0.88,                       // 有沒有回答這個問題？
      "source_url": "https://…" }
  ],
  "warnings": ["5 semantic results dropped below the relevance floor"]
}
```

問一件你的知識庫根本沒有的事，它會說沒有，而不是把最不糟的十筆丟給你：

```jsonc
{ "count": 0, "retrieval_mode": "keyword_fallback",
  "warnings": ["semantic results found but 10 dropped below the relevance floor"] }
```

從外面看，「沒有資料」和「檢索壞掉」長得一模一樣。分得出這兩者，知識庫才值得信任。

---

## 運作方式

```text
來源
  本機筆記 · X 書籤 · YouTube · GitHub · PDF / PubMed · 對話
       │
       ▼
同一套卡片契約
  來源轉接器 → scripts/_card_prompt.py → scripts/_llm.py
       │
       ▼
證據卡
  九個段落 · 來源連結 · 可信度分級 · 雙語摘要
       │
       ├──────────────►  混合檢索（向量 + 關鍵字 + RRF）
       ├──────────────►  平面向量索引              ── 降級
       ├──────────────►  關鍵字索引                ── 降級
       │
       ▼
吸收閘門
  卡片 + 對話 → staging → 審核 → 耐久的 wiki 主題
       │
       ▼
召回
  四個層次撈出候選，再由一個只做判斷的模型逐一裁決
       │
       ▼
相關性判斷
  「這段有沒有回答問題？」——卡片、wiki、對話軌跡一視同仁
       │
       ▼
知識服務
  一個 HTTP API · token 分權 · 所有接上的 Agent 共用
```

每一段都是可以單獨執行的腳本，沒有黑箱。

### 九段式知識卡

每一種來源都產生同樣的結構，讓檢索有一個穩定的單位，而不是一堆各自為政的摘要：

1. 核心問題與結論
2. **Claim 等級** —— `Attested`、`Scholarship`、`Inference`
3. 關鍵論點
4. False Friends —— 技術含義與日常用法不同的詞
5. 驚訝點
6. 與現有知識的關係
7. 雙語摘要，供搜尋索引使用
8. 對使用者的價值
9. 原始來源與連結

含圖片的來源會多一個第十段 **Media Evidence**，帶 OCR 與視覺註記，由 `scripts/media_ingest.py` 產生。

**Claim 等級是日後回收價值的關鍵**：幾個月後這張卡再出現時，你看得出它當初是被驗證過的、有文獻的，還是推論來的。

### 四種召回方式

召回不是一種搜尋。依照你在做什麼，XKB 會動用不同的層：

| 層 | 查哪裡 | 回答什麼 |
| --- | --- | --- |
| **Continuity** | wiki 主題、每日記憶 | *我們之前決定或確立了什麼？* |
| **Associative** | 證據卡、書籤 | *我蒐集過哪些跟這件事有關的東西？* |
| **Contrarian** | wiki、記憶 | *有什麼反對意見——限制、衝突、失敗過的案例？* |
| **Action** | 腳本、roadmap、TODO 段落 | *我可以跑什麼？下一步原本是什麼？* |

**反例層之所以存在，是因為一個永遠附和你的知識庫是負債。** 當你正在快速收斂成一個方案時，它會把你自己存過的反面證據翻出來。

---

## 為什麼結果不一樣

### 相關度是判斷出來的，不是算出來的

混合檢索回傳的是**排名**分數：它說的是「這筆排第一」，不是「這筆相關」。在真實的知識庫上，第一名永遠落在 `0.88` 附近，不管有沒有相關內容——實測時一個離題問題拿到 `0.863`，一個切題問題只有 `0.862`。

重算真實餘弦解決了一半，XKB 有很長一段時間只做到這裡。用十二個真實問題量過之後，這樣不夠：**餘弦分不出「講同一個主題」和「回答了這個問題」。** 問「agent 記憶應該當成內部服務嗎」，它給一則沒有內容的推文 `0.724`，還排在一段幾乎逐字回答了問題的文字前面。

所以最後一關交給一個只做判斷的模型。每一個候選——卡片、wiki 段落、對話軌跡一視同仁——都被問同一個問題：*這段有沒有回答使用者問的事？* 在同一批候選上，真正的答案落在 `0.17–0.92`，雜訊落在 `0.01–0.07`。門檻放行但沒人用得上的那些，在抵達你的 context 之前就被擋下來了。

判斷不出來時全部保留，召回退回原本的樣子。一個安靜地回傳空白的知識庫，比一個回傳太多的更糟。

### 蒸餾兩個方向都有閘門

卡片是證據，wiki 主題是理解。這條線不會自己被跨越。吸收閘門會評估主題契合度與重複度，而且**故障時關閉**——閘門跑不動就什麼都不吸收。從 Agent 捕捉的對話只會變成**候選**，通過審查前不算知識。

### 知識可以退場

召回會記錄每筆知識被撈出來後，有沒有任何一次夠格被使用。被反覆撈出卻從來沒有貢獻的會被**降權，不是刪除**：它還在索引裡、還搜得到、還繼續被量測。哪天它真的相關一次，降權就自動解除——不需要任何人做決定。

時間從來不是理由。你存下來打算兩年後才用的東西根本沒被撈出來過，所以這條規則在定義上碰不到它；它抓的是一直佔著名額卻從來沒有貢獻的知識。可追溯性才是產品本身，不為了整潔丟掉證據。

### 不是一輪對話的東西，不會變成知識

Agent 會產生看起來像對話、其實不是的文字：任務完成通知、執行期脈絡、系統提醒。在超過一千輪的實測裡，這些佔了**召回被問到的全部內容的 13.5%**——而且它們輕鬆越過餘弦門檻，因為用字跟筆記重疊。現在它們不再被記成一輪對話，也不再被搜尋。你貼進來的東西仍然是你的：規則只比對訊息開頭的已知機器標記，不做更寬的推測。

### 新分類要累積到門檻才開

分類器遇到現有分類都不適合的內容時，不會當場開一個新分類。那個名字會被記成提案，同一個名字被提議五次之後才真的開——這是 wiki 主題層本來就在用的規則。還在等待的卡片身上留著提議的名字，門檻到了就會被撈過去。

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

相關性判斷是第二個模型，在 `config/llm.json` 裡以 `judge_model` 另外指定，而它沿用上面的 URL 與金鑰——不需要多管一份憑證。這個前提是同一個供應商同時提供 chat 端點與判斷端點；如果你的供應商沒有，召回會保留全部候選而不是丟掉，方向是安全的，但就沒有效果。

**3 · 攝取、建索引、提問**

```bash
python3 scripts/local_ingest.py demo/sample-notes --category learning --limit 3
bash    scripts/build_search_index.sh
python3 scripts/xkb_ask.py "這些筆記之間有什麼共通模式？"
```

卡片與索引寫進你的工作區，不會寫進這個版本庫。

---

## 加入來源

同一套卡片契約，多種輸入：

```bash
python3 scripts/local_ingest.py ~/notes --category research    # Markdown / 純文字
python3 scripts/pdf_ingest.py paper.pdf                        # PDF / 論文
python3 scripts/fetch_youtube_playlist.py <playlist-url>       # 影片逐字稿
python3 scripts/fetch_github_repos.py                          # star 與 fork
python3 scripts/media_ingest.py <card.md> --limit 4            # 圖片 OCR + 視覺註記
```

另含 X/Twitter 書籤匯入、PubMed，以及佇列式的加值 worker，詳見 [`SKILL.md`](./SKILL.md)。

## 蒸餾成耐久知識

```bash
python3 scripts/absorb_gate_semantic.py --review               # 會吸收什麼
python3 scripts/sync_cards_to_wiki.py --apply                  # 卡片 → wiki 主題
python3 scripts/distill_memory_to_wiki.py --stage              # 對話 → staging
python3 scripts/xkb_review.py --list                           # 審核佇列
```

沒有通過閘門的東西不會進到 wiki 主題，而且你永遠看得到它判斷了什麼、為什麼。

---

## 讓多個 Agent 共用

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

身分由 bearer token 決定，token 綁定 namespace 與權限；宣告別的 namespace 的請求會被**拒絕**，而不是默默改用別的。沒設定 token 時服務維持匿名，那是單人使用的預設。完整 API 見 [`docs/xkb-memory-service.md`](./docs/xkb-memory-service.md)。

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
- **不是全自動。** 沒有你點頭，不會有東西被升進 wiki。降權是自動的，但它只改變你看到的順序——不刪任何東西，而且只要相關一次就會自己解除。
- **不是 Agent 框架。** 它負責存放與回傳知識，思考是你的 Agent 的事。
- **有些部分還很早期。** 知識服務、跨 Agent hook、降權訊號與相關性判斷都是新的——判斷層又是其中最新的，它的門檻來自一次實測樣本。這些介面還會變動；卡片與 wiki 這兩層比較久、也比較穩。

使用雲端 embedding 代表查詢會離開你的機器；設定 `EMBEDDING_PROVIDER=ollama` 可以全部留在本機。哪些資料送到哪裡，詳見 [`docs/data-flow.md`](./docs/data-flow.md)。

---

## 文件

| 文件 | 內容 |
| --- | --- |
| [`SKILL.md`](./SKILL.md) | 完整指令清單 |
| [`docs/xkb-memory-service.md`](./docs/xkb-memory-service.md) | 服務 API、身分驗證、Agent hook、相關度門檻與判斷層 |
| [`docs/data-flow.md`](./docs/data-flow.md) | 哪些資料離開你的機器，以及如何停止 |
| [`docs/RUNTIME_PATHS.md`](./docs/RUNTIME_PATHS.md) | 程式碼與你的資料的界線 |
| [`wiki/WIKI-SCHEMA.md`](./wiki/WIKI-SCHEMA.md) | wiki 主題契約 |
| [`docs/xkb-vnext-roadmap-draft.md`](./docs/xkb-vnext-roadmap-draft.md) | 接下來的方向 |

## 授權

[PolyForm Noncommercial 1.0.0](./LICENSE) — 非商業用途免費。

<p align="right">
  <a href="./README.md">English</a> · <strong>繁體中文</strong>
</p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="XKB：有人在對話中說出計畫、沒有發問；XKB 搜尋卡片、wiki 與過去的對話，最多三條附來源的知識送到回答的 agent 面前。">
</p>

# XKB · 讓 Agent 找回你累積的知識

**在正常討論中辨識需求，主動帶回有用的既有知識；不用等使用者提問或要求搜尋。**

XKB 將帶有來源的 Markdown 卡片、wiki 主題與已擷取的對話軌跡保存在你控制的環境。MCP、HTTP 與預設 CLI 共用同一個召回核心，讓接入的 agent 以相同的權限規則與相關性檢查，搜尋同一份知識。

目標是在討論計畫、困難、限制或決策時，適時帶回能推進當前工作的證據。
召回可以連同近期對話理解接續發言。Claude hook 會在提交訊息時執行；
使用 MCP 的 agent 需要把主動召回納入對話流程，僅連上工具不代表一定會使用。

[直接試用](#不用-api-key先試用) · [匯入自己的資料](#建立自己的知識庫) · [接入-agent](#讓-agent-接進來) · [召回如何運作](#召回如何運作)

## 把證據找回來，也保留不同看法

在附帶的合成資料庫中，詢問 **「Aurora deployment」**，會找到兩種已記錄的做法：

| 證據 | 內容 |
| --- | --- |
| `aurora-blue` | 採用藍綠部署。 |
| `aurora-canary` | 改用金絲雀部署，而不是藍綠部署。 |

這是測試資料，不是正式環境的效能基準。它展示召回的用途：找出相關來源、保留分歧，再由回答問題的 agent 根據證據判斷。每筆結果仍保有來源、文件段落與 namespace 等資訊。

[原有 24 個離線案例](./evals/recall-cases.json)在未改標籤的情況下，[目前通過 24/24](./evals/recall-keyword-report.json)。涵蓋關鍵字召回、中文查詢、相反證據、權限邊界、無答案問題與真實 MCP 傳輸；**不代表語意模型或生成答案的品質評測**。

## 不用 API key，先試用

需要 **Python 3.10+** 與 Git。離線範例也能在 Windows 執行，`python3` 可依環境改成 `python`。後面的資料匯入流程另外需要 Bash，請使用 Linux、macOS 或 WSL。

```bash
git clone https://github.com/Hidicence/x-knowledge-base.git
cd x-knowledge-base
python3 scripts/xkb_eval.py
```

這會建立暫存的測試資料庫，啟動真正的 MCP 子程序、執行有標籤的查詢，最後輸出 JSON 報告。不使用供應商憑證，也不讀取你的個人知識庫。

## 建立自己的知識庫

先在程式庫外建立資料目錄。`xkb_init.py` 會記住位置，但不會替你建立目錄。

```bash
export XKB_DATA_DIR="$HOME/xkb-data"
mkdir -p "$XKB_DATA_DIR/cards" "$XKB_DATA_DIR/bookmarks" \
         "$XKB_DATA_DIR/x-knowledge-base/wiki/topics"
python3 scripts/xkb_init.py --data-dir "$XKB_DATA_DIR"
```

設定支援 chat-completions 的供應商來產生卡片。以下操作會把選定的筆記送給該供應商，可能產生費用；請填入自己的網址、金鑰與模型名稱。

```bash
export LLM_API_URL="https://your-provider.example/v1"
export LLM_API_KEY="your-key"
export LLM_MODEL="your-model"

python3 scripts/local_ingest.py demo/sample-notes --category learning --limit 3
python3 scripts/recall_router.py "agent memory" --json
```

想讓「產生卡片」與「相關性判斷」走不同供應商時，改設 `XKB_GENERATION_API_URL`／`_API_KEY`／`_MODEL` 與 `XKB_JUDGE_API_URL`／`_API_KEY`／`_MODEL`；設了專用網址或金鑰的用途，不會再借用通用的 `LLM_*` 金鑰。範例見 [`.env.example`](./.env.example)。

本地匯入會同時寫入卡片與搜尋索引，這裡不需要另外重建索引。匯入新增卡片時的結束碼是 `2`，沒有新增時是 `0`；這是「資料有變動」的訊號，不代表失敗。

關鍵字召回不需要 embedding。需要語意搜尋時，再依照[向量設定指南](./docs/embedding-configuration.md)設定；安裝 NumPy 可以大幅加快 wiki 相似度搜尋，沒有安裝時結果相同，只是比較慢。Jev 相關性判斷是選用能力，供應商須另外支援 `/systemone`，只有 chat-completions 端點並不足夠。判斷服務不可用時會保留候選並回報狀態；也可設定 `XKB_JEV_DECIDE=0` 主動關閉。

若要根據找回的證據生成回答：

```bash
python3 scripts/xkb_ask.py "RAG 與 agent memory 有什麼差別？"
```

生成回答需要已設定的對話模型。加上 `--json` 可取得完整召回結果與可用證據參照，但不代表模型實際使用了每一筆。現行回答提示詞以繁體中文為主。

## 讓 agent 接進來

**MCP 提供召回；對話擷取需要另外安裝 hook 或串接 HTTP。** 加入 MCP 工具不會自動記錄所有對話。

在 Codex、Claude Code 等 MCP 用戶端註冊以下 stdio server，路徑請使用絕對路徑：

```json
{
  "mcpServers": {
    "xkb": {
      "command": "python3",
      "args": ["/absolute/path/x-knowledge-base/scripts/xkb_recall_server.py"],
      "env": {"XKB_ENV_FILE": "/absolute/path/xkb.env"}
    }
  }
}
```

這是常見的 JSON 設定形式，請依用戶端轉成它接受的格式。工具名稱是 `xkb_recall`，參數為 `message` 與選填的 `limit`。修改後需重新連線或重啟用戶端。

若使用本地知識庫，私有環境設定檔可填入：

```dotenv
XKB_DATA_DIR=/absolute/path/xkb-data
XKB_SERVICE_DB=/absolute/path/xkb-runtime/knowledge.sqlite
```

若要跨機器共用一份資料，在保存知識庫的主機執行 `python3 scripts/xkb_knowledge_service.py`，用戶端改連這個服務：

```dotenv
XKB_MEMORY_SERVICE_URL=http://127.0.0.1:18972
XKB_SERVICE_TOKEN=your-service-token
XKB_NAMESPACE=private
```

金鑰放在程式庫外。伺服器端 token 設定見[服務指南](./docs/xkb-memory-service.md)，namespace 必須與 token 一致。跨機器連線請用 HTTPS 或 SSH tunnel，例如 `ssh -N -L 18972:127.0.0.1:18972 your-server`。

| 接入方式 | 提供的能力 |
| --- | --- |
| MCP 用戶端 | 透過 `xkb_recall` 按需召回。 |
| HTTP 用戶端／OpenClaw 串接 | 召回，以及明確呼叫服務 API 的 session、turn 擷取。 |
| Claude Code hook 安裝器 | 安裝後，在 `UserPromptSubmit` 召回、`Stop` 擷取對話。排程執行的 `claude -p`（Claude Code 會標記為無人值守）會跳過。 |

`python3 scripts/xkb_install_agent_hook.py --install` 修改的是 **Claude Code 設定**，不會安裝 Codex／Orca hook。Hook 的服務連線需要另外設定，並不沿用 MCP 設定。

用自己知識庫中的主題檢查實際連線：

```bash
python3 scripts/xkb_doctor.py --query "agent memory" --json
```

Doctor 會驗證 MCP 初始化、工具清單與一次真正的呼叫，區分 `ready`、`degraded`、`failed`。加上 `--require-semantic --require-judge` 可要求語意搜尋與判斷模型必須可用。完整設定與診斷方式見[召回驗證指南](./docs/recall-validation.md)。

## 召回如何運作

1. **讀取有權限使用的來源。** 卡片、wiki／每日筆記段落與已擷取的對話，都保留來源資訊；回傳前套用 namespace 與可見性規則。
2. **搜尋候選。** 卡片和 wiki 使用已設定的語意後端，並保留關鍵字退路；對話軌跡使用關鍵字搜尋。
3. **判斷相關性。** 設定完成後，Jev 會判斷每筆候選是否回答了問題。失敗或缺少判斷，不會被記成「不相關」。
4. **合併與排序。** 證據身分包含 namespace，文件也區分段落。同文件的不同段落會保留，同一證據經不同路徑命中不會重複累計。
5. **回傳可查驗的結果。** 證據、來源、搜尋模式、各來源狀態、judge 狀態與警告，透過 MCP、HTTP 與預設 CLI 一起回傳。
6. **挑出要主動提起的。** 回傳結果中的 `delivery` 最多放三條判定相關的知識。這一步不呼叫生成模型：正在回答這一輪的 agent 看得到整段對話，由它判斷建議是否適用。在已擷取的對話裡，Claude Code 上次壓縮之後已經給過的建議，以及這段對話自己最近的紀錄，不會再提供；內容有更新的 wiki 段落或筆記則可以再給。

反覆收到明確負面判斷的證據可以降權，但不刪除來源；一次正面判斷就能解除降權。「被回傳」「被判定相關」「被最終答案使用」是不同事件。

舊的 continuity／associative／contrarian／action 呈現方式仍可透過 `recall_router.py --legacy-router` 使用，但已不是預設共用召回路徑。

## 從來源累積成長期知識

XKB 也包含本地筆記、X 書籤、YouTube 逐字稿、GitHub 專案與論文的匯入工具。來源會整理成共用的九段式知識卡，保留連結、主張等級與摘要；含圖片的來源可補上 OCR 與視覺證據。

卡片可透過吸收流程整合成 wiki 主題，已擷取的對話也能先整理成候選、再審閱。**召回本身不會把對話升格成 wiki 知識。** 這些寫入流程需要另外執行與檢查，見[指令指南](./SKILL.md)及 [wiki 格式](./wiki/WIKI-SCHEMA.md)。

## 使用前要知道的邊界

- **本機優先指的是資料保存與控制。** 若使用雲端供應商，卡片生成、embedding、相關性判斷和回答生成，會分別送出各自需要的輸入。只把 embedding 換成本地，不代表整套系統離線。
- **降級會明確回報。** Judge 不可用時保留候選；若指定了遠端服務而連線失敗，會回報錯誤，不會偷偷改查另一份本地資料。
- **品質仍需要你自己的標籤。** 合成案例用來防止功能退步；語意召回與 judge 門檻，需要用自己知識庫中獨立審閱的案例評估。
- **這是自行部署的工具組。** 各來源匯入器有自己的依賴；XBrain／GBrain 混合搜尋需要對應後端，但離線範例與關鍵字召回不需要。

## 文件

| 指南 | 用途 |
| --- | --- |
| [召回與驗證](./docs/recall-validation.md) | MCP、共用選項、證據身分、診斷與評測。 |
| [Knowledge Service](./docs/xkb-memory-service.md) | HTTP API、認證、session 與 hook。 |
| [向量設定](./docs/embedding-configuration.md) | 供應商設定與語意索引。 |
| [完整指令](./SKILL.md) | 匯入、卡片生成、wiki 流程與維護。 |
| [資料流向](./docs/data-flow.md) | 各供應商會收到哪些資料。 |
| [執行期路徑](./docs/RUNTIME_PATHS.md) | 分開保存私有資料與可重用程式。 |

## 授權

[PolyForm Noncommercial 1.0.0](./LICENSE)。可依授權條款用於非商業用途；商業使用需另外取得許可。

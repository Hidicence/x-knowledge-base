"""測試環境的外部逾時。整套測試因此從 6 分 35 秒降到約 1 分半。

2026-09-24 量到的：502 支測試裡有 10 支各花 30~61 秒，而它們全部卡在同一件事
——測試機器上沒有 gbrain 語意後端，也沒有 embedding 憑證，所以每一次呼叫都等滿
預設的 30 秒逾時才退回 fallback。整套 6 分 35 秒裡有 5 分鐘是在等一個必定失敗
的呼叫。

這不是新發現的技巧：test_xkb_memory_service_http.py 早就在自己的 setUp 裡把兩個
逾時設成 1，所以它只花 3.8 秒。問題是那是一份**只有那個檔記得**的做法，其他檔
沒跟上——這個專案的教訓是「教訓要變成結構」，所以改放在 conftest，任何測試都不
可能忘記。

**不要在這裡設得太短。** 逾時是 fallback 路徑的觸發條件，設成 0 會讓那條路徑在
測試裡永遠不成立。1 秒足夠讓真的能連上的後端回應，也足夠讓連不上的立刻放棄。

正式環境不受影響：這個檔只在 pytest 底下載入，VPS 上的排程與服務走的是
XKB_ENV_FILE 與各自的預設值。
"""
from __future__ import annotations

import os

# setdefault 而不是直接指派：某支測試如果刻意要別的值（例如要驗證逾時本身的
# 行為），它在自己的 setUp 裡設的值不該被這裡蓋掉。
for _name in ("XKB_XBRAIN_TIMEOUT", "XKB_EMBEDDING_TIMEOUT"):
    os.environ.setdefault(_name, "1")

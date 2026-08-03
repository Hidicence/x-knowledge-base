#!/usr/bin/env python3
"""哪些內容是系統自己講的話,不是知識——全專案唯一的一份清單。

Agent 的軌跡裡有大量不是對話的東西:心跳、排程回報、dream diary 指令、
背景任務完成通知。它們長得像對話,但沒有人在思考,所以不該進候選記憶。

這份清單原本有兩份,而且各自漏掉對方有的項目:

    xkb_l1_to_candidate.NOISE_MARKERS       13 條
    xkb_candidate_pool_analyzer.SYSTEM_MARKERS  12 條
    只在前者:cron jobs executed、heartbeat_ok、system echo…
    只在後者:write a dream diary entry、do not run the command again…

結果是同一批雜訊,一邊擋得住、另一邊擋不住。2026-08-03 匯入 VPS 軌跡時
發現的:蒸餾器沒有 "write a dream diary entry",所以 dream 指令一路通關。

清單只有一份,改這裡就好。
"""
from __future__ import annotations

# 出現任何一個就算系統雜訊。全部小寫比對。
NOISE_MARKERS: tuple[str, ...] = (
    # 心跳與存活訊號
    "heartbeat",
    "heartbeat_ok",
    "assistant: heartbeat_ok",
    # 排程與自動回報
    "cron:",
    "cron jobs executed",
    "cron_jobs_executed",
    "self_review_sent",
    "hn_digest_sent",
    "delivery",
    # 管線指令:是給模型的工作指示,不是使用者在說話
    "write a dream diary entry",
    "do not run the command again",
    "async command did not run",
    "continue the openclaw runtime event",
    "a background task completed",
    "queued user message from a previous",
    # 空回應與失敗
    "assistant: no_reply",
    "assistant turn failed before producing content",
    "image2_skill_autogrow_failed",
    # 系統自述
    "system echo",
    "system (",
)


def is_noise(*parts: str) -> bool:
    """任何一段文字命中任何一個標記,就算系統雜訊。"""
    blob = "\n".join(str(p or "") for p in parts).lower()
    return any(marker in blob for marker in NOISE_MARKERS)


if __name__ == "__main__":
    for sample in ("[OpenClaw heartbeat poll]", "Write a dream diary entry from these memories",
                   "Continue the OpenClaw runtime event.", "召回應該回報實際用了哪種檢索"):
        print(f"  {'雜訊' if is_noise(sample) else '真對話'}  {sample[:50]}")

#!/usr/bin/env python3
"""jev：只做判斷的模型。XKB 用它回答「這筆知識有沒有回答這個問題」。

jev 不是聊天模型，所以 `_llm.call` 打不到它——它沒有 `/v1/chat/completions`：

    POST {LLM_API_URL}/systemone
    {"model": "jev-1.13", "state": "<要判斷的內容>",
     "questions": {"<名稱>": {"type": "noul|choice|score", "instructions": ...}}}

回傳的是有型別的答案，不是文字。`noul` 回 0~1 的機率，`choice` 回選項，
`score` 回等級。計費只算輸入。

**為什麼值得接。** 2026-09-24 在正式資料上量過：一次呼叫裡塞四個判斷，總共
1.85 秒——跟單一判斷一樣，因為 questions 是平行評估的。所以 N 個候選只要一次
往返，不是 N 次。而它的分離度遠勝餘弦：

    同一批候選     jev          餘弦
    正面回答       0.31 / 0.54  0.480
    完全不相關     0.02 / 0.01  0.446 / 0.435

餘弦把相關與不相關壓在 0.435~0.480 這 7% 的區間裡，而固定門檻 0.55 就壓在
那一段的上方——正式資料上 980 次召回丟掉了 3,886 筆候選，而抽樣檢查發現
「食品展的客戶通常怎麼找」這種問題的正確答案就落在 0.480，被丟掉了。餘弦分不
出「講同一個主題」跟「回答了這個問題」，jev 的 noul 正好就是後者。

**這支不做決定。** 它只回答。要不要照它的答案丟掉候選，由呼叫端決定，而目前
呼叫端跑的是影子模式：餘弦照常決定，jev 的答案存起來比對。門檻要從真實分布
校準，不是我挑一個數字——這個專案在手調門檻上犯過的錯有紀錄在案。

**失敗一律開放通過。** 判斷不出來時回 None，而不是 0。這兩件事差很多：0 是
「jev 說不相關」，None 是「jev 沒跑」。把它們寫成同一件事，就會變成 jev 一掛掉
知識庫就靜默關閉——那正是這個專案吃過最大虧的那種失敗。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import xkb_failures
from runtime_config import runtime_env

_SKILL_DIR = Path(__file__).resolve().parent.parent
_CONFIG_FILE = _SKILL_DIR / "config" / "llm.json"

# 判斷模型跟生成模型是兩回事，所以是另一個設定鍵。共用一把金鑰與 base URL
# （兔子 API 兩個端點都在 LLM_API_URL 底下），但模型名不共用——把 llm.json 的
# model 改成 jev 會讓所有生成腳本一起壞掉，因為那個端點根本不存在。
DEFAULT_JUDGE_MODEL = "jev-1.13"
JUDGE_PATH = "/systemone"

# 判斷要嘛很快要嘛沒用。實測 1.85 秒，留三倍餘裕；超過就當它沒跑。
# 召回路徑上多等十秒比少一筆弱候選糟得多。
TIMEOUT_SECONDS = 6


def judge_model() -> str:
    env_model = runtime_env().get("XKB_JUDGE_MODEL", "")
    if env_model:
        return env_model
    try:
        cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        return cfg.get("judge_model") or DEFAULT_JUDGE_MODEL
    except Exception as err:  # noqa: BLE001
        if _CONFIG_FILE.exists():
            xkb_failures.note("jev config", err, detail=str(_CONFIG_FILE))
        return DEFAULT_JUDGE_MODEL


def available() -> bool:
    """有沒有可用的憑證。沒有的話呼叫端要當作「沒跑」，不是「判斷為否」。"""
    settings = runtime_env()
    return bool(settings.get("LLM_API_URL") and settings.get("LLM_API_KEY"))


def judge(state: str, questions: dict[str, dict],
          *, timeout: int = TIMEOUT_SECONDS) -> dict | None:
    """問 jev 一組問題。回 answers；判斷不出來回 None。

    None 與「答案是 0」是兩件不同的事，呼叫端必須分開處理——見模組說明。
    """
    if not state or not questions or not available():
        return None
    settings = runtime_env()
    url = settings["LLM_API_URL"].rstrip("/") + JUDGE_PATH
    body = json.dumps({"model": judge_model(), "state": state,
                       "questions": questions}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {settings['LLM_API_KEY']}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError,
            ValueError) as err:
        xkb_failures.note("jev judge", err, detail=url)
        return None
    answers = payload.get("answers")
    return answers if isinstance(answers, dict) else None


def relevance(query: str, candidates: list[tuple[str, str]],
              *, timeout: int = TIMEOUT_SECONDS) -> dict[str, float] | None:
    """每個候選有沒有回答這個問題。回 {key: 0~1}；判斷不出來回 None。

    一次呼叫評估全部候選——questions 是平行評估的，所以 N 個候選跟 1 個一樣快。
    逐一呼叫的話 10 個候選要 18 秒，比召回本身還慢，這條路就不可行了。

    候選文字會被截斷：instructions 是判斷準則，不是整篇文件，而卡片可以很長。
    標題加摘要足以判斷「有沒有回答這個問題」——真要整篇才判斷得出來的情況，
    那筆本來就不該直接注入。
    """
    if not query or not candidates:
        return None
    questions = {}
    slots: dict[str, str] = {}
    for index, (key, text) in enumerate(candidates):
        if not key or not text:
            continue
        # 問題名稱用序號，不用候選的 key：key 是檔案路徑，裡面有斜線與中文，
        # 而它會變成回應 JSON 的欄位名。序號對回去就好。
        slot = f"q{index}"
        slots[slot] = key
        questions[slot] = {
            "type": "noul",
            "instructions": ("這段知識有沒有回答使用者的問題。"
                             f"知識內容：{_trim(text)}"),
        }
    if not questions:
        return None
    answers = judge(f"使用者的問題：{_trim(query, 600)}", questions,
                    timeout=timeout)
    if answers is None:
        return None
    out: dict[str, float] = {}
    for slot, key in slots.items():
        answer = answers.get(slot)
        if not isinstance(answer, dict):
            continue
        value = answer.get("noul")
        if isinstance(value, (int, float)):
            out[key] = float(value)
    # 一個都對不上就是回應的形狀跟預期不同——那是「沒跑成」，不是「全部不相關」。
    return out or None


def needs_recall(query: str, *, timeout: int = TIMEOUT_SECONDS) -> float | None:
    """這句話需不需要去查知識庫。回 0~1；判斷不出來回 None。

    問的是「需不需要查」，不是「這是不是問題」。「推上去吧」「好 繼續吧」是完整
    的指令，只是答案不在知識庫裡；而「碳盤查的計算方式」只有八個字，卻正是這個
    知識庫存在的理由。長度、句型、有沒有問號都分不出這兩者——現在那十來條正則就是
    在用這些特徵，而它的補丁裡有一條是把一整句話寫死。

    **這一題判錯的代價不對稱。** 該查卻沒查，整個召回不會跑，而回應看起來完全正常
    ——這個專案為這種靜默失敗付過 12 週。該省卻查了，只是多花幾秒。所以呼叫端的
    門檻要偏向放行，而不是取中間值。
    """
    if not query:
        return None
    answers = judge(
        f"使用者對 AI 助理說：{_trim(query, 1200)}",
        {"needs": {
            "type": "noul",
            "instructions": ("回答這句話需不需要查使用者的個人知識庫（裡面有他的"
                             "工作筆記、報價、拍片流程、AI 工具心得、客戶資料）。"
                             "純粹的指令、確認、閒聊不需要；問到事實、做法、"
                             "過去怎麼處理的就需要。"),
        }},
        timeout=timeout)
    if answers is None:
        return None
    answer = answers.get("needs")
    if not isinstance(answer, dict):
        return None
    value = answer.get("noul")
    return float(value) if isinstance(value, (int, float)) else None


def _trim(text: str, limit: int = 900) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    demo_query = "食品展的客戶通常怎麼找"
    demo = [
        ("cards/a.md", "公開企業名錄通常不直接提供完整聯絡資訊；食品展官方"
                       "資料可能只有公司名稱、攤位、官網、產品與簡介，沒有電話。"),
        ("cards/b.md", "AI 影片生成的鏡頭運動提示詞寫法，包含推軌、環繞與特寫。"),
    ]
    scores = relevance(demo_query, demo)
    if scores is None:
        print("jev 沒跑成——憑證沒設，或端點打不通。這不代表候選不相關。")
    else:
        print(f"問題：{demo_query}")
        for key, value in sorted(scores.items(), key=lambda kv: -kv[1]):
            print(f"  {value:.2f}  {key}")

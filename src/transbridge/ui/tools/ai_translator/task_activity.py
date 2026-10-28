"""Normalize old text callbacks at the boundary and preserve typed activity batches."""

from __future__ import annotations

import re

from transbridge.ai_translator.activity import ActivityKind, ActivityMessage

_TECHNICAL = re.compile(
    r"(?:[A-Za-z]:[\\/]|https?://|(?:^|\s)/(?:[^\s/]+/)+|sha256:|source:plugin[.:]|"
    r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b|Traceback|\{\s*[\"']|\b0x[0-9a-fA-F]+\b)"
)
_BATCH = re.compile(r"^\[(-?\d+)\]\s*")
_DETAIL = re.compile(r"^(?:术语匹配时长|LLM调用时长|解析时长|总时长|翻译词条数|新增术语数|任务\d+[:：])")


def concise_event(value: str) -> ActivityMessage | None:
    """Support untyped extension callbacks without a Chinese-prefix allowlist.

    Known old failure messages become safe summaries before diagnostic filtering.
    Native producers should use ActivityMessage so severity does not depend on prose.
    """
    text = str(value).strip()
    batch = getattr(value, "batch", None)
    if match := _BATCH.match(text):
        index = int(match[1])
        batch = index if index >= 0 else None
        text = text[match.end() :].strip()
    text = text.strip("─ \t\r\n")
    icon = text[:1]
    text = re.sub(r"^[⏸▶⏹⚠✅❌↩]+\s*", "", text)
    kind = getattr(value, "kind", ActivityKind.STAGE)
    if kind == ActivityKind.DETAIL:
        return None
    if "API 调用失败" in text:
        count = re.search(r"（(\d+) 条）", text)
        text = (f"{count[1]} 条" if count else "") + "模型请求失败；本次未获得有效结果，处理结束后可重试失败条目。"
        kind = ActivityKind.ERROR
    elif any(
        text.startswith(prefix)
        for prefix in ("QualityGate 批次异常", "Refine 批次异常", "Polish 批次异常", "Arbitrate 批次异常")
    ):
        text = "本批校对未完成；请在任务结果中查看需复核条目。"
        kind = ActivityKind.WARNING
    elif "未能持久化" in text:
        text = "部分翻译结果未能保存；请检查磁盘空间和写入权限，处理结束后重试失败条目。"
        kind = ActivityKind.ERROR
    elif text.startswith("恢复记录保存失败"):
        text = "恢复记录保存失败；请检查磁盘空间和写入权限，暂时不要退出，当前结果仍可按正常流程保存。"
        kind = ActivityKind.ERROR
    elif kind == ActivityKind.STAGE:
        if "重试" in text and "失败" not in text:
            kind = ActivityKind.RETRY
        elif icon == "❌" or "失败" in text or "任务未启动" in text:
            kind = ActivityKind.ERROR
        elif icon == "⚠" or "未获得译文" in text or "需复核" in text:
            kind = ActivityKind.WARNING
    unsafe = len(text) > 180 or "\n" in text or _TECHNICAL.search(text)
    if unsafe and kind in {ActivityKind.WARNING, ActivityKind.ERROR, ActivityKind.RETRY}:
        text = {
            ActivityKind.ERROR: "处理遇到错误；请在任务结果中查看未完成条目及处理建议。",
            ActivityKind.WARNING: "处理存在异常；请在任务结果中查看需复核条目。",
            ActivityKind.RETRY: "正在自动重试未完成的处理，暂时无需操作。",
        }[kind]
        unsafe = False
    if (
        not text
        or text in {"开始翻译：", "已完成：", "LLM 响应中..."}
        or _DETAIL.match(text)
        or " -> " in text
        or unsafe
        or not re.search(r"[\u4e00-\u9fff]", text)
    ):
        return None
    return ActivityMessage(text, kind, batch=batch, occurred_at=getattr(value, "occurred_at", None))


def event_text(event: ActivityMessage) -> str:
    return (f"第 {event.batch + 1} 批 · " if event.batch is not None else "") + str(event)


class ActivityBatch(str):
    """A text-compatible signal payload retaining source, time and event kind."""

    def __new__(cls, events):
        events = tuple(events)
        instance = super().__new__(cls, "\n".join(f"{source} · {event_text(event)}" for source, event in events))
        instance.events = events
        return instance

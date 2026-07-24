from __future__ import annotations

import re

from personal_news_agent.core.categories import CATEGORIES
from personal_news_agent.core.models import TimeRange
from personal_news_agent.core.tag_classifier import classify_category_tags


ORDINALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
}

def extract_ordinal(message: str) -> int | None:
    match = re.search(r"第\s*([一二三四五1-5])\s*(?:条|篇|则|个|项|篇新闻|条新闻|个新闻)", message)
    if not match:
        return None
    return ORDINALS.get(match.group(1))


def infer_categories(message: str) -> list[str] | None:
    return classify_category_tags(message) or None


def query_from_message(message: str, topic: str | None = None) -> str:
    original = message
    message = message.replace("别的", "其他")
    for key in CATEGORIES.keys():
        message = message.replace(key, " ")
    cleanup = [
        "早上好",
        "告诉我",
        "给我一些",
        "给我",
        "在帮我",
        "帮我看看",
        "帮我",
        "看看",
        "了解一下",
        "请你",
        "请",
        "我想知道",
        "我也想知道",
        "还有别的",
        "还有什么",
        "关于这方面",
        "这方面",
        "他们的",
        "它们的",
        "今天",
        "近一个月",
        "过去一个月",
        "一个月",
        "近30天",
        "30天",
        "有什么新闻",
        "有什么新变化",
        "有哪些值得关注的新变化",
        "最新进展",
        "新进展",
        "最新",
        "最近",
        "说说",
        "如何",
        "一下",
        "的",
        "？",
        "?",
    ]
    for token in cleanup:
        message = message.replace(token, " ")
    message = message.replace("圈", " ")
    cleaned = " ".join(message.split())
    if topic and topic.strip() and _is_generic_chat_query(cleaned):
        return topic.strip()
    if topic and topic.strip() and is_contextual_followup(original):
        suffix = cleaned.replace("他们", " ").replace("它们", " ")
        suffix = " ".join(suffix.split())
        if topic.strip() in suffix:
            return suffix
        return f"{topic.strip()} {suffix}".strip()
    return cleaned if len(cleaned) > 1 else "热点 新闻"


def is_contextual_followup(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    markers = (
        "还有", "继续", "再说", "进一步", "展开", "深挖", "他们", "它们", "这些公司", "该公司", "该赛事", "这方面",
        "上面", "刚才", "刚刚", "上次", "之前", "前面", "刚问",
    )
    return any(marker in compact for marker in markers)


def categories_for_message(
    message: str,
    topic: str | None = None,
    category_scope: list[str] | None = None,
) -> list[str] | None:
    inferred = infer_categories(message)
    if inferred:
        return inferred
    if topic and (topic.strip() in message or is_contextual_followup(message)):
        return category_scope or None
    return None


def time_range_from_message(message: str) -> TimeRange | None:
    if any(token in message for token in ("今天", "今日")):
        return TimeRange(days=1)
    if any(token in message for token in ("近一周", "一周", "7天", "七天")):
        return TimeRange(days=7)
    if any(token in message for token in ("近一个月", "过去一个月", "一个月", "30天", "三十天")):
        return TimeRange(days=31)
    if any(token in message for token in ("近半年", "半年", "6个月", "六个月")):
        return TimeRange(days=180)
    if any(token in message for token in ("最近", "最新", "新进展", "新变化")):
        return TimeRange(days=14)
    return None


def _is_generic_chat_query(cleaned: str) -> bool:
    compact = cleaned.replace(" ", "")
    if not compact:
        return True
    return compact in {"热点新闻", "新闻", "变化", "进展", "更新", "继续", "展开", "深挖"}

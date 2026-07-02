from __future__ import annotations

import re

from personal_news_agent.core.categories import CATEGORIES
from personal_news_agent.core.models import TimeRange


ORDINALS = {
    "第一": 1,
    "第二": 2,
    "第三": 3,
    "第四": 4,
    "第五": 5,
    "第1": 1,
    "第2": 2,
    "第3": 3,
    "第4": 4,
    "第5": 5,
}


def extract_ordinal(message: str) -> int | None:
    for token, value in ORDINALS.items():
        if token in message:
            return value
    match = re.search(r"第\s*(\d+)\s*条", message)
    return int(match.group(1)) if match else None


def infer_categories(message: str) -> list[str] | None:
    hints = {
        "时政": "politics",
        "政治": "politics",
        "国际": "politics",
        "乌克兰": "politics",
        "俄罗斯": "politics",
        "俄乌": "politics",
        "战争": "politics",
        "冲突": "politics",
        "经济": "economy",
        "财经": "economy",
        "粮食": "economy",
        "农作物": "economy",
        "能源": "economy",
        "制裁": "economy",
        "科技": "tech",
        "AI": "tech",
        "汽车": "auto",
        "车企": "auto",
        "车型": "auto",
        "新能源车": "auto",
        "智能驾驶": "auto",
        "游戏": "game",
        "电竞": "game",
        "动漫": "anime",
        "番剧": "anime",
        "娱乐": "entertainment",
        "明星": "entertainment",
        "体育": "sports",
        "NBA": "sports",
        "球队": "sports",
        "WSBK": "sports",
        "机车赛事": "sports",
    }
    categories = [category for word, category in hints.items() if word.lower() in message.lower()]
    return sorted(set(categories)) or None


def query_from_message(message: str, topic: str | None = None) -> str:
    original = message
    for zh, key in CATEGORIES.items():
        message = message.replace(zh, " ")
        message = message.replace(key, " ")
    cleanup = [
        "帮我看看",
        "帮我",
        "看看",
        "了解一下",
        "请你",
        "请",
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
    if topic and topic.strip() and (_is_generic_chat_query(cleaned) or topic.strip() in original):
        return topic.strip()
    return cleaned if len(cleaned) > 1 else "热点 新闻"


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

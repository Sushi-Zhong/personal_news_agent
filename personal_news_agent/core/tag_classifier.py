from __future__ import annotations

from dataclasses import dataclass
import re

from personal_news_agent.core.categories import CATEGORIES


@dataclass(frozen=True)
class TagMatch:
    category: str
    score: int
    matched_terms: tuple[str, ...]


CATEGORY_TAG_RULES: dict[str, tuple[str, ...]] = {
    "politics": (
        "时政",
        "政治",
        "国际关系",
        "外交",
        "选举",
        "政府",
        "战争",
        "冲突",
        "制裁",
        "地缘",
    ),
    "economy": (
        "经济",
        "财经",
        "金融",
        "股市",
        "上市",
        "ipo",
        "gdp",
        "国内生产总值",
        "宏观",
        "通胀",
        "cpi",
        "pmi",
        "利率",
        "汇率",
        "财政",
        "货币政策",
        "贸易",
        "出口",
        "进口",
        "产业链",
        "价格",
        "利润",
        "收入",
        "能源",
        "粮食",
        "农作物",
    ),
    "tech": (
        "科技",
        "ai",
        "人工智能",
        "芯片",
        "半导体",
        "大模型",
        "算力",
        "机器人",
        "云计算",
        "软件",
        "硬件",
        "开源",
    ),
    "auto": (
        "汽车",
        "车企",
        "车型",
        "新能源车",
        "新能源汽车",
        "智能驾驶",
        "自动驾驶",
        "电动车",
        "燃油车",
        "机车公司",
        "机车品牌",
        "机车厂商",
        "摩托车",
    ),
    "game": (
        "游戏",
        "电竞",
        "手游",
        "端游",
        "主机",
        "steam",
        "任天堂",
        "索尼",
        "xbox",
    ),
    "anime": (
        "动漫",
        "动画",
        "漫画",
        "番剧",
        "二次元",
        "acg",
    ),
    "entertainment": (
        "娱乐",
        "明星",
        "电影",
        "电视剧",
        "综艺",
        "音乐",
        "票房",
        "演唱会",
    ),
    "sports": (
        "体育",
        "nba",
        "足球",
        "篮球",
        "世界杯",
        "fifa",
        "球队",
        "比赛",
        "机车比赛",
        "机车赛事",
        "联赛",
        "赛事",
        "奥运",
    ),
}


EXPLICIT_CATEGORY_ALIASES: dict[str, str] = {
    key: key for key in CATEGORIES
} | {
    label.lower(): key for key, label in CATEGORIES.items()
}


def classify_category_tags(
    text: str,
    *,
    min_score: int = 1,
    max_tags: int | None = None,
) -> list[str]:
    """Return category tags matched by general topic wording.

    Empty result means no confident category was found, which callers can
    display as all or pass to a broader semantic classifier.
    """
    matches = explain_category_tags(text, min_score=min_score)
    tags = [match.category for match in matches]
    return tags[:max_tags] if max_tags else tags


def explain_category_tags(text: str, *, min_score: int = 1) -> list[TagMatch]:
    normalized = _normalize(text)
    if not normalized:
        return []

    matches: list[TagMatch] = []
    for category, terms in CATEGORY_TAG_RULES.items():
        matched_terms = tuple(term for term in terms if _contains_term(normalized, term))
        explicit = _explicit_category_score(normalized, category)
        score = len(matched_terms) + explicit
        if score >= min_score:
            matches.append(TagMatch(category=category, score=score, matched_terms=matched_terms))

    return sorted(matches, key=lambda item: (-item.score, item.category))


def _normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _contains_term(normalized_text: str, term: str) -> bool:
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    if re.fullmatch(r"[a-z0-9_]+", normalized_term):
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized_term)}(?![a-z0-9_])", normalized_text) is not None
    return normalized_term in normalized_text


def _explicit_category_score(normalized_text: str, category: str) -> int:
    for alias, target in EXPLICIT_CATEGORY_ALIASES.items():
        if target == category and _contains_term(normalized_text, alias):
            return 2
    return 0

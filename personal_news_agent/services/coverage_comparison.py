from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import json
import re
from typing import Any, Literal
from urllib.parse import urlparse

from personal_news_agent.core.models import SearchResult, TimeRange
from personal_news_agent.services.analysis_evidence import filter_recent_evidence, filter_topic_evidence
from personal_news_agent.services.article_fetch import canonicalize_url
from personal_news_agent.services.evidence import EvidenceLedger
from personal_news_agent.services.structured_output import decode_json_object
from personal_news_agent.skills.result_models import CoverageCompareData


NEWS_COVERAGE_COMPARE_SKILL_NAME = "news-coverage-compare"
_GENERIC_COMPARISON_ENTITIES = {"ai", "api", "app", "ceo", "cfo", "coo", "cto", "eu", "ipo", "uk", "us"}


@dataclass(frozen=True)
class CoverageComparisonOutcome:
    data: CoverageCompareData
    status: Literal["success", "degraded"]
    fallback_reason: str | None = None


@dataclass(frozen=True)
class _GroupRef:
    index: int


class CoverageComparisonService:
    def __init__(
        self,
        store: Any,
        search_service: Any,
        *,
        cc_runtime: Any | None = None,
    ) -> None:
        self.store = store
        self.search_service = search_service
        self.cc_runtime = cc_runtime

    async def run(
        self,
        *,
        topic: str,
        category_scope: list[str] | None = None,
        source_scope: list[str] | None = None,
        include_remote: bool = False,
        now: datetime | None = None,
    ) -> CoverageComparisonOutcome:
        time_range = TimeRange(days=7)
        items = await self.search_service.search(
            topic,
            category_scope or None,
            source_scope or None,
            time_range,
            max_results=30,
            include_remote=include_remote,
        )
        comparison_subject = _comparison_subject(topic)
        items = _prepare_retrieved_evidence(
            items,
            time_range=time_range,
            comparison_subject=comparison_subject,
            now=now,
        )
        if category_scope and _independent_group_count(items) < 2:
            expanded = await self.search_service.search(
                topic,
                None,
                source_scope or None,
                time_range,
                max_results=30,
                include_remote=include_remote,
            )
            items = _prepare_retrieved_evidence(
                [*items, *expanded],
                time_range=time_range,
                comparison_subject=comparison_subject,
                now=now,
            )
        return await self.analyze(topic=topic, items=items)

    async def analyze(
        self,
        *,
        topic: str,
        items: list[dict[str, Any] | SearchResult],
    ) -> CoverageComparisonOutcome:
        ledger = EvidenceLedger()
        indexed: list[tuple[dict[str, Any], Any]] = []
        seen_urls: set[str] = set()
        for item in items:
            payload = _payload(item)
            url = canonicalize_url(str(payload.get("url") or ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            indexed.append((payload, ledger.add(item, claim_role="context")))

        groups, group_by_index = _source_groups(indexed)
        comparison_status = "sufficient" if len(groups) >= 2 else "insufficient"
        if comparison_status == "insufficient":
            return CoverageComparisonOutcome(
                data=CoverageCompareData(
                    topic=topic,
                    comparison_status="insufficient",
                    source_groups=groups,
                    missing_questions=["至少需要两个独立来源组才能比较共同事实与差异。"],
                    evidence=list(ledger.references),
                ),
                status="success",
            )

        fallback_framing = _fallback_framing(groups, indexed)
        if not self.cc_runtime or not getattr(self.cc_runtime, "configured", False):
            return CoverageComparisonOutcome(
                data=CoverageCompareData(
                    topic=topic,
                    comparison_status="insufficient",
                    framing_differences=fallback_framing,
                    missing_questions=["Agent 暂不可用，仅展示来源分组与基础叙事差异。"],
                    source_groups=groups,
                    evidence=list(ledger.references),
                ),
                status="degraded",
                fallback_reason="agent_unavailable",
            )

        evidence_payload = [
            {
                "index": ref.index,
                "group_id": group_by_index[ref.index],
                "title": ref.title,
                "url": ref.url,
                "source_id": ref.source_id,
                "summary": _summary_for_ref(ref.index, indexed),
            }
            for ref in ledger.references
        ]
        try:
            runtime_result = await self.cc_runtime.run(
                message=(
                    "只基于给定 evidence/source_groups 比较报道，返回严格 JSON。\n"
                    + json.dumps(
                        {"topic": topic, "source_groups": groups, "evidence": evidence_payload},
                        ensure_ascii=False,
                    )
                ),
                query=topic,
                topic=topic,
                category_scope=[],
                time_range=None,
                history="",
                allow_web_search=False,
                allow_local_search=False,
                skill_names=[NEWS_COVERAGE_COMPARE_SKILL_NAME],
                strict_json_output=True,
                max_turns=6,
            )
        except Exception:
            return CoverageComparisonOutcome(
                data=CoverageCompareData(
                    topic=topic,
                    comparison_status="insufficient",
                    framing_differences=fallback_framing,
                    missing_questions=["智能分析暂不可用，当前仅展示来源分组。"],
                    source_groups=groups,
                    evidence=list(ledger.references),
                ),
                status="degraded",
                fallback_reason="agent_unavailable",
            )

        try:
            parsed = decode_json_object(runtime_result.answer)
            declared_status = parsed.get("comparison_status")
            if declared_status not in {"sufficient", "insufficient"}:
                raise ValueError("invalid comparison_status")
            common = _validated_items(parsed.get("common_facts"), ledger, group_by_index, min_groups=2)
            unique = _validated_items(parsed.get("unique_claims"), ledger, group_by_index, min_groups=1)
            conflicts = _validated_items(parsed.get("conflicts"), ledger, group_by_index, min_groups=2)
            conflicts = [item for item in conflicts if _has_deterministic_conflict(item, indexed)]
            framing = _validated_items(parsed.get("framing_differences"), ledger, group_by_index, min_groups=1)
            data = CoverageCompareData(
                topic=topic,
                comparison_status=declared_status,
                common_facts=common,
                unique_claims=unique,
                conflicts=conflicts,
                framing_differences=framing,
                missing_questions=_string_list(parsed.get("missing_questions")),
                source_groups=groups,
                evidence=list(ledger.references),
            )
            return CoverageComparisonOutcome(data=data, status="success")
        except Exception:
            return CoverageComparisonOutcome(
                data=CoverageCompareData(
                    topic=topic,
                    comparison_status="insufficient",
                    framing_differences=fallback_framing,
                    missing_questions=["当前无法形成可靠比较结论，仅展示来源分组。"],
                    source_groups=groups,
                    evidence=list(ledger.references),
                ),
                status="degraded",
                fallback_reason="agent_output_invalid",
            )


def _source_groups(indexed: list[tuple[dict[str, Any], Any]]) -> tuple[list[dict[str, Any]], dict[int, str]]:
    parents = list(range(len(indexed)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(indexed)):
        for right in range(left + 1, len(indexed)):
            left_row = indexed[left][0]
            right_row = indexed[right][0]
            if _publisher_identity(left_row) == _publisher_identity(right_row) or _same_distribution(left_row, right_row):
                union(left, right)

    grouped: dict[int, list[tuple[dict[str, Any], Any]]] = {}
    for index, item in enumerate(indexed):
        grouped.setdefault(find(index), []).append(item)
    buckets = list(grouped.values())

    groups: list[dict[str, Any]] = []
    group_by_index: dict[int, str] = {}
    for position, bucket in enumerate(buckets, start=1):
        group_id = f"group_{position}"
        indices = [ref.index for _, ref in bucket]
        for index in indices:
            group_by_index[index] = group_id
        cross_publisher_distribution = any(
            _publisher_identity(left[0]) != _publisher_identity(right[0])
            and _same_distribution(left[0], right[0])
            for index, left in enumerate(bucket)
            for right in bucket[index + 1 :]
        )
        groups.append(
            {
                "group_id": group_id,
                "label": bucket[0][0].get("source_id") or _domain(bucket[0][0].get("url")),
                "source_ids": list(dict.fromkeys(str(row.get("source_id") or _domain(row.get("url"))) for row, _ in bucket)),
                "evidence_indices": indices,
                "reason": (
                    "syndicated_same_copy"
                    if cross_publisher_distribution
                    else "same_publisher"
                    if len(bucket) > 1
                    else "independent_report"
                ),
            }
        )
    return groups, group_by_index


def _prepare_retrieved_evidence(
    items: list[dict[str, Any] | SearchResult],
    *,
    time_range: TimeRange,
    comparison_subject: str,
    now: datetime | None,
) -> list[dict[str, Any] | SearchResult]:
    recent = filter_recent_evidence(items, time_range, now=now)
    relevant = filter_topic_evidence(recent, comparison_subject)
    deduplicated: list[dict[str, Any] | SearchResult] = []
    seen_urls: set[str] = set()
    for item in relevant:
        url = canonicalize_url(str(_payload(item).get("url") or ""))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduplicated.append(item)
    return deduplicated


def _independent_group_count(items: list[dict[str, Any] | SearchResult]) -> int:
    indexed = [(_payload(item), _GroupRef(index)) for index, item in enumerate(items, start=1)]
    groups, _ = _source_groups(indexed)
    return len(groups)


def _comparison_subject(topic: str) -> str:
    """Use the stable named subject, not every decorative Latin headline token."""
    entities = re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9+#._-]{1,}(?![A-Za-z0-9])", topic or "")
    if not entities:
        return topic

    def priority(item: tuple[int, str]) -> tuple[bool, bool, bool, int]:
        index, entity = item
        return (
            entity.casefold() not in _GENERIC_COMPARISON_ENTITIES,
            any(character.isdigit() or character in ".-_" for character in entity),
            any(character.isupper() for character in entity) and not entity.isupper(),
            -index,
        )

    return max(enumerate(entities), key=priority)[1]


def _publisher_identity(item: dict[str, Any]) -> str:
    source_id = str(item.get("source_id") or "").strip().lower()
    if source_id:
        return f"source:{source_id}"
    domain = _domain(item.get("url")).lower().removeprefix("www.")
    return f"domain:{domain}"


def _same_distribution(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_hash = str(left.get("content_hash") or "")
    right_hash = str(right.get("content_hash") or "")
    if left_hash and left_hash == right_hash:
        return True
    left_text = _normalized_body(left)
    right_text = _normalized_body(right)
    if min(len(left_text), len(right_text)) < 24:
        return False
    return left_text == right_text or SequenceMatcher(None, left_text, right_text).ratio() >= 0.94


def _normalized_body(item: dict[str, Any]) -> str:
    text = str(item.get("content") or item.get("content_excerpt") or item.get("summary") or "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())


def _fallback_framing(
    groups: list[dict[str, Any]],
    indexed: list[tuple[dict[str, Any], Any]],
) -> list[dict[str, Any]]:
    representatives = [group["evidence_indices"][0] for group in groups if group.get("evidence_indices")]
    titles = [indexed[index - 1][0].get("title") for index in representatives if index <= len(indexed)]
    if len(set(titles)) < 2:
        return []
    return [
        {
            "text": "各独立来源的标题或叙事侧重不同；该差异本身不构成事实冲突。",
            "evidence_indices": representatives,
        }
    ]


def _validated_items(
    value: Any,
    ledger: EvidenceLedger,
    group_by_index: dict[int, str],
    *,
    min_groups: int,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("comparison items must be lists")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        references = ledger.validate_model_references(item.get("evidence_indices") or [])
        indices = [ref.index for ref in references]
        groups = {group_by_index.get(index) for index in indices}
        groups.discard(None)
        if len(groups) < min_groups:
            continue
        result.append({**item, "evidence_indices": indices})
    return result


def _has_deterministic_conflict(
    item: dict[str, Any],
    indexed: list[tuple[dict[str, Any], Any]],
) -> bool:
    rows_by_index = {ref.index: row for row, ref in indexed}
    rows = [rows_by_index[index] for index in item.get("evidence_indices") or [] if index in rows_by_index]
    signatures = [_fact_signature(row) for row in rows]
    numeric = [signature[0] for signature in signatures if signature[0]]
    if len(numeric) >= 2 and any(value != numeric[0] for value in numeric[1:]):
        return True
    statuses = [signature[1] for signature in signatures if signature[1]]
    return len(statuses) >= 2 and any(value != statuses[0] for value in statuses[1:])


def _fact_signature(item: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    text = f"{item.get('summary') or ''} {item.get('content_excerpt') or ''}".lower()
    numbers = frozenset(
        re.sub(r"\s+", "", match)
        for match in re.findall(r"\d+(?:\.\d+)?\s*(?:年|月|日|名|人|位|%|％|万|亿|元|美元|项|家|次)", text)
    )
    status_terms = frozenset(
        term
        for term in ("提议", "宣布", "批准", "通过", "启动", "开始", "完成", "取消", "撤回", "否认", "暂停")
        if term in text
    )
    return numbers, status_terms


def _payload(item: dict[str, Any] | SearchResult) -> dict[str, Any]:
    return item.model_dump(mode="python") if hasattr(item, "model_dump") else dict(item)


def _summary_for_ref(index: int, indexed: list[tuple[dict[str, Any], Any]]) -> str:
    for row, ref in indexed:
        if ref.index == index:
            return str(row.get("summary") or row.get("content_excerpt") or "")[:800]
    return ""


def _domain(value: Any) -> str:
    return urlparse(str(value or "")).netloc or "unknown"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:10]

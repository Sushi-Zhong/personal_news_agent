from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import hashlib
import json
import math
import re
from typing import Any, Literal
from zoneinfo import ZoneInfo

from personal_news_agent.core.models import SearchResult, TimeRange
from personal_news_agent.services.analysis_evidence import filter_recent_evidence, filter_topic_evidence
from personal_news_agent.services.article_fetch import canonicalize_url
from personal_news_agent.services.evidence import EvidenceLedger
from personal_news_agent.services.structured_output import decode_json_object
from personal_news_agent.services.time_context import DEFAULT_APP_TIMEZONE, application_timezone
from personal_news_agent.skills.result_models import ChangeDigestData


NEWS_CHANGE_DIGEST_SKILL_NAME = "news-change-digest"


@dataclass(frozen=True)
class ChangeDetectionOutcome:
    data: ChangeDigestData
    status: Literal["success", "degraded"]
    fallback_reason: str | None = None


class ChangeDetectionService:
    def __init__(
        self,
        store: Any,
        search_service: Any,
        *,
        cc_runtime: Any | None = None,
        app_timezone: ZoneInfo | None = None,
    ) -> None:
        self.store = store
        self.search_service = search_service
        self.cc_runtime = cc_runtime
        self.app_timezone = app_timezone or application_timezone(DEFAULT_APP_TIMEZONE)

    async def run(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        topic: str,
        baseline_expression: str | None = None,
        category_scope: list[str] | None = None,
        include_remote: bool = False,
        now: datetime | None = None,
    ) -> ChangeDetectionOutcome:
        current_now = (now or datetime.now(self.app_timezone)).astimezone(self.app_timezone)
        baseline_items: list[dict[str, Any] | SearchResult] = []
        if baseline_expression:
            baseline_label, baseline_at = resolve_change_baseline(
                baseline_expression,
                now=current_now,
                app_timezone=self.app_timezone,
            )
        else:
            conversation_baseline = self._conversation_baseline(conversation_id, user_id, topic)
            if conversation_baseline:
                baseline_items, baseline_at = conversation_baseline
                baseline_label = f"当前对话：{baseline_at.astimezone(self.app_timezone).strftime('%Y-%m-%d %H:%M')} 的研究结果"
            else:
                baseline_label, baseline_at = resolve_change_baseline(
                    None,
                    now=current_now,
                    app_timezone=self.app_timezone,
                )

        baseline_distance_days = max(1, math.ceil((current_now - baseline_at).total_seconds() / 86400))
        retrieval_days = min(365, max(7, baseline_distance_days))
        results = await self.search_service.search(
            topic,
            category_scope or None,
            None,
            TimeRange(days=retrieval_days),
            max_results=24,
            include_remote=include_remote,
        )
        results = filter_recent_evidence(results, TimeRange(days=retrieval_days), now=current_now)
        results = filter_topic_evidence(results, topic)
        if not baseline_items:
            baseline_items = [item for item in results if _published_at(item) and _published_at(item) <= baseline_at]
            current_items = [item for item in results if not _published_at(item) or _published_at(item) > baseline_at]
        else:
            current_items = list(results)
        return await self.analyze(
            topic=topic,
            baseline_items=baseline_items,
            current_items=current_items,
            baseline_label=baseline_label,
            baseline_at=baseline_at,
        )

    async def analyze(
        self,
        *,
        topic: str,
        baseline_items: list[dict[str, Any] | SearchResult],
        current_items: list[dict[str, Any] | SearchResult],
        baseline_label: str,
        baseline_at: datetime,
    ) -> ChangeDetectionOutcome:
        ledger = EvidenceLedger()
        baseline_rows = _dedupe_items(baseline_items)
        current_rows = _dedupe_items(current_items)
        indexed_baseline = [
            (
                _payload(item),
                ledger.add(
                    item,
                    origin="conversation" if isinstance(item, dict) else None,
                    claim_role="baseline",
                    dedupe_key=_observation_key(_payload(item)),
                ),
            )
            for item in baseline_rows
        ]
        indexed_current = [
            (
                _payload(item),
                ledger.add(
                    item,
                    claim_role="current",
                    dedupe_key=_observation_key(_payload(item)),
                ),
            )
            for item in current_rows
        ]

        repeated: list[dict[str, Any]] = []
        unmatched_current: list[tuple[dict[str, Any], Any]] = []
        for current, current_ref in indexed_current:
            matched = next(
                (
                    baseline_ref
                    for baseline, baseline_ref in indexed_baseline
                    if _same_report_copy(baseline, current)
                ),
                None,
            )
            if matched:
                repeated.append(
                    {
                        "text": current.get("title") or "重复报道",
                        "reason": "正文或摘要与基线报道高度一致，仅标题、链接或分发来源变化。",
                        "evidence_indices": list(dict.fromkeys([matched.index, current_ref.index])),
                    }
                )
            else:
                unmatched_current.append((current, current_ref))

        if indexed_baseline and indexed_current and not unmatched_current:
            return self._outcome(
                topic,
                baseline_label,
                baseline_at,
                "no_material_change",
                repeated_reports=repeated,
                evidence=ledger.references,
            )

        if not indexed_baseline or not indexed_current:
            return self._degraded(
                topic,
                baseline_label,
                baseline_at,
                repeated,
                unmatched_current,
                ledger,
                "insufficient_evidence",
            )

        if not self.cc_runtime or not getattr(self.cc_runtime, "configured", False):
            return self._degraded(
                topic,
                baseline_label,
                baseline_at,
                repeated,
                unmatched_current,
                ledger,
                "agent_unavailable",
            )

        evidence_payload = [
            {
                "index": ref.index,
                "side": "baseline" if ref.claim_role == "baseline" else "current",
                "title": ref.title,
                "url": ref.url,
                "published_at": ref.published_at.isoformat() if ref.published_at else None,
                "summary": _summary_for_ref(ref.index, [*indexed_baseline, *indexed_current]),
            }
            for ref in ledger.references
        ]
        try:
            runtime_result = await self.cc_runtime.run(
                message=(
                    "只基于给定 baseline/current evidence 判断实质变化，返回严格 JSON。\n"
                    + json.dumps({"topic": topic, "evidence": evidence_payload}, ensure_ascii=False)
                ),
                query=topic,
                topic=topic,
                category_scope=[],
                time_range=None,
                history="",
                allow_web_search=False,
                allow_local_search=False,
                skill_names=[NEWS_CHANGE_DIGEST_SKILL_NAME],
                strict_json_output=True,
                max_turns=6,
            )
        except Exception:
            return self._degraded(
                topic,
                baseline_label,
                baseline_at,
                repeated,
                unmatched_current,
                ledger,
                "agent_unavailable",
            )

        try:
            parsed = decode_json_object(runtime_result.answer)
        except (TypeError, ValueError):
            return self._degraded(
                topic,
                baseline_label,
                baseline_at,
                repeated,
                unmatched_current,
                ledger,
                "agent_output_invalid",
            )

        allowed_current = {ref.index for _, ref in indexed_current}
        categories: dict[str, list[dict[str, Any]]] = {}
        try:
            for key in ("new_facts", "status_changes", "number_changes", "corrections"):
                categories[key] = _validated_change_items(parsed.get(key), ledger, allowed_current)
            declared_status = parsed.get("change_status")
            if declared_status not in {"changed", "no_material_change", "insufficient"}:
                raise ValueError("invalid change_status")
            if declared_status == "changed" and not any(categories.values()):
                declared_status = "insufficient"
            return self._outcome(
                topic,
                baseline_label,
                baseline_at,
                declared_status,
                repeated_reports=repeated,
                watch_next=_string_list(parsed.get("watch_next")),
                evidence=ledger.references,
                **categories,
            )
        except (TypeError, ValueError):
            return self._degraded(
                topic,
                baseline_label,
                baseline_at,
                repeated,
                unmatched_current,
                ledger,
                "agent_output_invalid",
            )

    def _conversation_baseline(
        self,
        conversation_id: str | None,
        user_id: str,
        topic: str,
    ) -> tuple[list[dict[str, Any]], datetime] | None:
        if not conversation_id:
            return None
        turns = self.store.list_turns(conversation_id, user_id, limit=40)
        for turn in reversed(turns):
            response = turn.get("response") or {}
            output_kind = response.get("output_kind") or (response.get("skill_result") or {}).get("output_kind")
            if output_kind in {"change_digest", "coverage_compare", "schedule_confirmation"}:
                continue
            evidence = _response_evidence(response)
            if not evidence or not _topic_matches_response(topic, response, evidence):
                continue
            created_at = _as_datetime(turn.get("created_at"))
            if created_at:
                return evidence, created_at
        return None

    def _outcome(
        self,
        topic: str,
        baseline_label: str,
        baseline_at: datetime,
        change_status: str,
        *,
        new_facts: list[dict[str, Any]] | None = None,
        status_changes: list[dict[str, Any]] | None = None,
        number_changes: list[dict[str, Any]] | None = None,
        corrections: list[dict[str, Any]] | None = None,
        repeated_reports: list[dict[str, Any]] | None = None,
        watch_next: list[str] | None = None,
        evidence: Any = (),
    ) -> ChangeDetectionOutcome:
        data = ChangeDigestData(
            topic=topic,
            baseline_label=baseline_label,
            baseline_at=baseline_at,
            change_status=change_status,
            new_facts=new_facts or [],
            status_changes=status_changes or [],
            number_changes=number_changes or [],
            corrections=corrections or [],
            repeated_reports=repeated_reports or [],
            watch_next=watch_next or [],
            evidence=list(evidence),
        )
        return ChangeDetectionOutcome(data=data, status="success")

    def _degraded(
        self,
        topic: str,
        baseline_label: str,
        baseline_at: datetime,
        repeated: list[dict[str, Any]],
        unmatched_current: list[tuple[dict[str, Any], Any]],
        ledger: EvidenceLedger,
        reason: str,
    ) -> ChangeDetectionOutcome:
        safe_reports = list(repeated)
        safe_reports.extend(
            {
                "text": row.get("title") or "基线后新增报道",
                "classification": "unclassified_new_report",
                "evidence_indices": [ref.index],
            }
            for row, ref in unmatched_current
        )
        data = ChangeDigestData(
            topic=topic,
            baseline_label=baseline_label,
            baseline_at=baseline_at,
            change_status="insufficient",
            repeated_reports=safe_reports,
            watch_next=["只能确认出现了新报道，尚不能确认事实发生实质变化。"],
            evidence=list(ledger.references),
        )
        return ChangeDetectionOutcome(data=data, status="degraded", fallback_reason=reason)


def resolve_change_baseline(
    expression: str | None,
    *,
    now: datetime,
    app_timezone: ZoneInfo,
) -> tuple[str, datetime]:
    local_now = now.astimezone(app_timezone)
    value = " ".join(str(expression or "").split()).strip()
    timezone_name = getattr(app_timezone, "key", DEFAULT_APP_TIMEZONE)
    if not value:
        return "默认：过去24小时", local_now - timedelta(hours=24)
    if "昨天" in value:
        baseline = (local_now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return f"用户指定：昨天 00:00（{timezone_name}）", baseline
    if "一周" in value or "7天" in value:
        baseline = local_now - timedelta(days=7)
        return f"用户指定：过去一周（{timezone_name}）", baseline
    date_match = re.search(
        r"(?:自|从)?\s*(20\d{2})\s*(?:年|[-/])\s*(\d{1,2})\s*(?:月|[-/])\s*(\d{1,2})\s*日?",
        value,
    )
    if date_match:
        try:
            baseline = datetime(
                int(date_match.group(1)),
                int(date_match.group(2)),
                int(date_match.group(3)),
                tzinfo=app_timezone,
            )
        except ValueError as exc:
            raise ValueError(f"无法解析 changed 基线：{value}") from exc
        if baseline > local_now:
            raise ValueError(f"无法解析 changed 基线：日期位于未来（{value}）")
        return f"用户指定：{baseline.strftime('%Y-%m-%d 00:00')}（{timezone_name}）", baseline
    month_day_match = re.search(r"(?:自|从)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", value)
    if month_day_match:
        try:
            baseline = datetime(
                local_now.year,
                int(month_day_match.group(1)),
                int(month_day_match.group(2)),
                tzinfo=app_timezone,
            )
            if baseline > local_now:
                baseline = baseline.replace(year=baseline.year - 1)
        except ValueError as exc:
            raise ValueError(f"无法解析 changed 基线：{value}") from exc
        return f"用户指定：{baseline.strftime('%Y-%m-%d 00:00')}（{timezone_name}）", baseline
    raise ValueError(f"无法解析 changed 基线：{value}")


def _payload(item: dict[str, Any] | SearchResult) -> dict[str, Any]:
    return item.model_dump(mode="python") if hasattr(item, "model_dump") else dict(item)


def _dedupe_items(items: list[dict[str, Any] | SearchResult]) -> list[dict[str, Any] | SearchResult]:
    seen: set[str] = set()
    result: list[dict[str, Any] | SearchResult] = []
    for item in items:
        payload = _payload(item)
        key = str(payload.get("article_id") or payload.get("url") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _same_report_copy(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_hash = str(left.get("content_hash") or "")
    right_hash = str(right.get("content_hash") or "")
    if left_hash and left_hash == right_hash:
        return True
    left_text = _normalized_copy_text(left)
    right_text = _normalized_copy_text(right)
    if not left_text or not right_text:
        return False
    return left_text == right_text or SequenceMatcher(None, left_text, right_text).ratio() >= 0.92


def _observation_key(item: dict[str, Any]) -> str:
    url = canonicalize_url(str(item.get("url") or ""))
    content_fingerprint = str(item.get("content_hash") or "").strip()
    if not content_fingerprint:
        normalized = _normalized_copy_text(item)
        content_fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else "empty"
    return f"{url}\x1f{content_fingerprint}"


def _normalized_copy_text(item: dict[str, Any]) -> str:
    text = str(item.get("content") or item.get("content_excerpt") or item.get("summary") or "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())


def _published_at(item: dict[str, Any] | SearchResult) -> datetime | None:
    return _as_datetime(_payload(item).get("published_at"))


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _summary_for_ref(index: int, indexed: list[tuple[dict[str, Any], Any]]) -> str:
    for row, ref in indexed:
        if ref.index == index:
            return str(row.get("summary") or row.get("content_excerpt") or "")[:800]
    return ""


def _validated_change_items(value: Any, ledger: EvidenceLedger, current_indices: set[int]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("change items must be lists")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        references = ledger.validate_model_references(item.get("evidence_indices") or [])
        indices = [ref.index for ref in references]
        if not any(index in current_indices for index in indices):
            continue
        result.append({**item, "evidence_indices": indices})
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:8]


def _response_evidence(response: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [*(response.get("evidence") or []), *(response.get("recommendations") or [])]
    skill_data = (response.get("skill_result") or {}).get("data") or {}
    candidates.extend(skill_data.get("evidence") or [])
    candidates.extend(skill_data.get("sources") or [])
    return [dict(item) for item in candidates if isinstance(item, dict) and item.get("url")]


def _topic_matches_response(topic: str, response: dict[str, Any], evidence: list[dict[str, Any]]) -> bool:
    normalized_topic = re.sub(r"\s+", "", topic).lower()
    haystack = " ".join(
        [
            str(response.get("topic") or ""),
            *(str(item.get("title") or "") for item in evidence),
            *(str(item.get("summary") or "") for item in evidence),
        ]
    )
    normalized_haystack = re.sub(r"\s+", "", haystack).lower()
    if normalized_topic and normalized_topic in normalized_haystack:
        return True
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", topic)]
    return bool(terms) and sum(term in normalized_haystack for term in terms) >= min(2, len(terms))

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from claude_code_backend.models import ChatRequest as LocalAgentChatRequest

from personal_news_agent.core.models import FactCheckResponse, SearchResult
from personal_news_agent.core.text import stable_id, summarize
from personal_news_agent.services.search import UnifiedSearchService
from personal_news_agent.services.store import NewsStore


VERDICTS = {"supported", "contradicted", "mixed", "insufficient", "not_checkable"}


class FactCheckService:
    def __init__(self, store: NewsStore, search_service: UnifiedSearchService, local_agent: Any | None = None):
        self.store = store
        self.search_service = search_service
        self.local_agent = local_agent

    async def run(
        self,
        user_id: str,
        claim: str,
        category_scope: list[str] | None = None,
        max_results: int = 12,
        check_query: str | None = None,
        include_remote: bool = False,
    ) -> FactCheckResponse:
        cleaned_claim = " ".join(str(claim or "").split()).strip()
        if not cleaned_claim:
            raise ValueError("缺少待核查说法")
        cleaned_check_query = " ".join(str(check_query or "").split()).strip()
        categories = category_scope or []
        search_query = f"{cleaned_claim} {cleaned_check_query}".strip()
        results = await self.search_service.search(
            search_query,
            categories or None,
            None,
            None,
            max_results=max_results,
            include_remote=include_remote,
        )
        evidence = self._evidence(results)
        fallback = _fallback_payload(cleaned_claim, categories, evidence, include_remote=include_remote, check_query=cleaned_check_query)
        payload, agent_source = await self._agent_payload(cleaned_claim, categories, evidence, fallback, user_id, cleaned_check_query)
        factcheck_id = stable_id("fact", f"{cleaned_claim}:{datetime.now(timezone.utc).isoformat()}")
        return FactCheckResponse(
            factcheck_id=factcheck_id,
            claim=cleaned_claim,
            category_scope=categories,
            agent_source=agent_source,
            evidence=evidence,
            **payload,
        )

    def _evidence(self, results: list[SearchResult]) -> list[dict[str, Any]]:
        evidence = []
        for index, item in enumerate(results[:12], start=1):
            row = self.store.get_article(item.article_id) if item.article_id else None
            content = (row or {}).get("content") or item.summary or ""
            published_at = item.published_at.isoformat() if item.published_at else (row or {}).get("published_at")
            evidence.append(
                {
                    "index": index,
                    "article_id": item.article_id,
                    "source_id": item.source_id,
                    "title": item.title,
                    "url": item.url,
                    "category": item.category,
                    "published_at": published_at,
                    "summary": item.summary or summarize(content, 180),
                    "content_excerpt": content[:700],
                    "origin": item.origin,
                }
            )
        return evidence

    async def _agent_payload(
        self,
        claim: str,
        categories: list[str],
        evidence: list[dict[str, Any]],
        fallback: dict[str, Any],
        user_id: str,
        check_query: str = "",
    ) -> tuple[dict[str, Any], str]:
        if not self.local_agent or not evidence:
            return fallback, "fallback"
        prompt = (
            "你是事实核查裁判，不是新闻评论员。"
            "只能基于 evidence 判断 claim。"
            "如果 evidence 不能直接支持或反驳 claim，返回 insufficient。"
            "标题和正文冲突时优先正文；多源冲突返回 mixed。"
            "不能用常识补事实，不能添加 evidence 中没有的事实。"
            "只返回严格 JSON，不要 markdown，不要解释。"
            "JSON 字段：verdict, confidence, summary, supporting_evidence, contradicting_evidence, missing_evidence, source_notes, next_checks。"
            "verdict 只能是 supported, contradicted, mixed, insufficient, not_checkable。"
            "supporting_evidence 和 contradicting_evidence 是证据 index 数组。"
        )
        try:
            response = await self.local_agent.chat(
                LocalAgentChatRequest(
                    user_id=user_id,
                    message=prompt,
                    project_context={
                        "feature": "personal_news_agent_factcheck",
                        "claim": claim,
                        "check_query": check_query,
                        "category_scope": categories,
                        "evidence": evidence,
                        "rules": [
                            "只能依据 evidence 判断",
                            "没有直接证据就是 insufficient",
                            "标题和正文冲突时优先正文",
                            "多源冲突就是 mixed",
                            "不能用常识补事实",
                        ],
                    },
                    metadata={"purpose": "factcheck_verdict"},
                )
            )
        except Exception:
            return fallback, "fallback"
        if response.status != "ok":
            return fallback, "fallback"
        try:
            parsed = _decode_json_object(response.message.content)
            return _normalize_payload(parsed, fallback, evidence), "local_agent"
        except Exception:
            return fallback, "fallback"


def _fallback_payload(
    claim: str,
    categories: list[str],
    evidence: list[dict[str, Any]],
    *,
    include_remote: bool = False,
    check_query: str = "",
) -> dict[str, Any]:
    if not evidence:
        summary = "未找到可直接支持或反驳该说法的本地证据。"
    else:
        summary = "已找到相关材料，但未经过事实核查裁判确认，暂按证据不足处理。"
    return {
        "verdict": "insufficient",
        "confidence": 0.25 if evidence else 0.1,
        "summary": summary,
        "supporting_evidence": [],
        "contradicting_evidence": [],
        "missing_evidence": ["原始权威来源", "明确发布时间", "可直接验证该说法的正文证据"],
        "source_notes": [_source_note(len(evidence), include_remote, check_query)],
        "next_checks": ["查找原始发布方或权威媒体全文", "对比标题与正文是否一致", "确认时间、地点、主体是否明确"],
    }


def _source_note(evidence_count: int, include_remote: bool, check_query: str) -> str:
    scope = "本地及联网" if include_remote else "本地"
    suffix = f"；本轮核查点：{check_query}" if check_query else ""
    remote_note = "已启用外部实时搜索" if include_remote else "未启用外部实时搜索"
    return f"本次检索到 {evidence_count} 条{scope}证据；{remote_note}{suffix}。"


def _normalize_payload(parsed: dict[str, Any], fallback: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    verdict = parsed.get("verdict") if parsed.get("verdict") in VERDICTS else fallback["verdict"]
    confidence = parsed.get("confidence")
    if not isinstance(confidence, int | float):
        confidence = fallback["confidence"]
    confidence = max(0.0, min(1.0, float(confidence)))
    return {
        "verdict": verdict,
        "confidence": confidence,
        "summary": _clean_text(parsed.get("summary"), 600) or fallback["summary"],
        "supporting_evidence": _evidence_refs(parsed.get("supporting_evidence"), evidence),
        "contradicting_evidence": _evidence_refs(parsed.get("contradicting_evidence"), evidence),
        "missing_evidence": _clean_string_list(parsed.get("missing_evidence"), 6, 160) or fallback["missing_evidence"],
        "source_notes": _clean_string_list(parsed.get("source_notes"), 6, 200) or fallback["source_notes"],
        "next_checks": _clean_string_list(parsed.get("next_checks"), 6, 160) or fallback["next_checks"],
    }


def _evidence_refs(value: Any, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    by_index = {item.get("index"): item for item in evidence}
    refs = []
    for raw in value:
        index = raw.get("index") if isinstance(raw, dict) else raw
        try:
            index = int(index)
        except (TypeError, ValueError):
            continue
        item = by_index.get(index)
        if item and item not in refs:
            refs.append(
                {
                    "index": item.get("index"),
                    "title": item.get("title"),
                    "source_id": item.get("source_id"),
                    "url": item.get("url"),
                    "summary": item.get("summary"),
                }
            )
    return refs


def _decode_json_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw or ""):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("factcheck agent did not return a JSON object")


def _clean_string_list(value: Any, limit: int, max_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = _clean_text(item, max_length)
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _clean_text(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:max_length]

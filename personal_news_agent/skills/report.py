from __future__ import annotations

import json
from typing import Any

from personal_news_agent.core.models import TimeRange
from personal_news_agent.services.cc_runtime import NEWS_TOPIC_REPORT_SKILL_NAME
from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec


class ReportSkill:
    spec = SkillSpec(
        command="/report",
        name="专题报告",
        description="把当前对话里已经出现的内容整理成带时间线和来源的专题报告。",
        usage="/report [主题] [--category tech,economy] [--time-range 30d]",
        examples=("/report", "/report 国际局势与大宗商品 --category politics,economy"),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        topic, options = _parse_args(args)
        explicit_topic = bool(topic.strip())
        topic = _clean_report_topic(topic if explicit_topic else (context.topic or "").strip())
        reports = context.services["reports"]
        if context.conversation_id and hasattr(reports, "generate_from_conversation"):
            report = await reports.generate_from_conversation(
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                topic=topic,
                category_scope=_categories(options.get("category")) or (context.category_scope or []),
                strict_topic_filter=bool(topic),
            )
            payload = report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)
            payload = await _cc_enrich_report_payload(
                payload,
                topic=payload.get("topic") or topic or "当前对话",
                categories=payload.get("category_scope") or context.category_scope or [],
                context=context,
                skill_name=NEWS_TOPIC_REPORT_SKILL_NAME,
                request="请把当前对话与已收集证据整理成一份可追踪的专题报告。",
                time_range=TimeRange(days=30),
            )
            return SkillResult(
                command=self.spec.command,
                title=f"专题报告：{payload.get('topic') or topic or '当前对话'}",
                message=f"报告已生成，共整理 {len(payload.get('sources') or [])} 条对话内证据。",
                data=payload,
            )
        if not topic:
            raise ValueError(f"缺少报告主题。用法：{self.spec.usage}")
        categories = _categories(options.get("category")) or (context.category_scope or [])
        report = await reports.generate(
            user_id=context.user_id,
            topic=topic,
            category_scope=categories,
            time_range=options.get("time-range", "30d"),
            report_type="timeline_analysis",
        )
        payload = report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)
        payload = await _cc_enrich_report_payload(
            payload,
            topic=topic,
            categories=categories,
            context=context,
            skill_name=NEWS_TOPIC_REPORT_SKILL_NAME,
            request=f"请围绕【{topic}】生成一份证据化专题报告。",
            time_range=TimeRange(days=_time_range_days(options.get("time-range", "30d"), 30)),
        )
        return SkillResult(
            command=self.spec.command,
            title=f"专题报告：{topic}",
            message=f"报告已生成，共整理 {len(payload.get('sources') or [])} 个来源。",
            data=payload,
        )


def _parse_args(args: list[str]) -> tuple[str, dict[str, str]]:
    topic_parts: list[str] = []
    options: dict[str, str] = {}
    index = 0
    while index < len(args):
        item = args[index]
        if item.startswith("--"):
            key = item[2:]
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError(f"参数 {item} 缺少值")
            options[key] = args[index + 1]
            index += 2
            continue
        topic_parts.append(item)
        index += 1
    return " ".join(topic_parts).strip(), options


def _categories(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _clean_report_topic(value: str) -> str:
    topic = " ".join(str(value or "").split()).strip()
    prefixes = ("专题报告：", "专题报告:", "事实核查：", "事实核查:", "继续核查：", "继续核查:")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if topic.startswith(prefix):
                topic = topic[len(prefix):].strip()
                changed = True
    return topic


async def _cc_enrich_report_payload(
    payload: dict[str, Any],
    *,
    topic: str,
    categories: list[str],
    context: SkillContext,
    skill_name: str,
    request: str,
    time_range: TimeRange,
) -> dict[str, Any]:
    runtime = context.services.get("cc_runtime")
    if not runtime or not getattr(runtime, "configured", False):
        payload["agent_source"] = "fallback"
        payload.setdefault("research_trace", []).append(
            {"stage": "场景 Skill", "status": "fallback", "message": "Agent 主控暂不可用，保留本地报告结果。"}
        )
        return payload
    try:
        result = await runtime.run(
            message=request,
            query=topic,
            topic=topic,
            category_scope=categories,
            time_range=time_range,
            history=json.dumps(payload, ensure_ascii=False, default=str)[:8_000],
            allow_web_search=context.allow_web_search,
            skill_names=[skill_name],
            max_turns=10,
            builtin_web_search_limit=3,
            on_trace=context.on_trace,
        )
    except Exception as exc:
        payload["agent_source"] = "fallback"
        payload.setdefault("research_trace", []).append(
            {
                "stage": "场景 Skill",
                "status": "fallback",
                "message": f"Agent 主控生成失败，保留本地报告：{type(exc).__name__}。",
            }
        )
        return payload
    payload["markdown"] = result.answer
    payload["agent_source"] = "cc_runtime"
    payload["research_trace"] = result.trace
    payload["evidence"] = _runtime_evidence(result.results)
    payload["expanded_queries"] = result.queries[:8]
    payload["recommendations"] = [item.model_dump(mode="json") for item in result.results[:8]]
    return payload


def _time_range_days(value: str, default: int) -> int:
    raw = str(value or "").strip().lower()
    if raw.endswith("d"):
        raw = raw[:-1]
    try:
        days = int(raw)
    except ValueError:
        days = default
    return max(1, min(365, days))


def _runtime_evidence(results: list[Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in results[:12]:
        url = str(getattr(item, "url", "") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        published_at = getattr(item, "published_at", None)
        evidence.append(
            {
                "index": len(evidence) + 1,
                "article_id": getattr(item, "article_id", None),
                "source_id": getattr(item, "source_id", None),
                "title": getattr(item, "title", ""),
                "url": url,
                "summary": getattr(item, "summary", ""),
                "published_at": published_at.isoformat() if hasattr(published_at, "isoformat") else published_at,
                "origin": getattr(item, "origin", "local"),
            }
        )
    return evidence

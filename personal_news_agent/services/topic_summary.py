from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from personal_news_agent.core.models import SearchResult
from personal_news_agent.core.text import extract_entities, extract_keywords, stable_id, summarize
from personal_news_agent.services.llm import LLMClient
from personal_news_agent.services.search import UnifiedSearchService
from personal_news_agent.services.store import NewsStore


class TopicSummarySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body: str
    bullets: list[str]
    evidence_indices: list[int]


class TopicSummaryTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    title: str
    summary: str
    stage: str
    actors: list[str]
    evidence_indices: list[int]


class TopicSummaryGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    type: str
    description: str
    evidence_indices: list[int]


class TopicSummaryGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    label: str
    description: str
    evidence_indices: list[int]


class TopicSummaryGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[TopicSummaryGraphNode]
    edges: list[TopicSummaryGraphEdge]


class TopicSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    lead: str
    sections: list[TopicSummarySection]
    timeline: list[TopicSummaryTimelineItem]
    graph: TopicSummaryGraph
    analysis: list[str]
    uncertainty: list[str]
    markdown: str
    confidence: float = Field(ge=0, le=1)


class TopicSummaryService:
    def __init__(
        self,
        store: NewsStore,
        search_service: UnifiedSearchService,
        llm: LLMClient | None = None,
        prompt_path: Path | None = None,
    ):
        self.store = store
        self.search_service = search_service
        self.llm = llm or LLMClient()
        self.prompt_path = prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "default_topic_summary.md"

    async def generate(
        self,
        user_id: str,
        topic: str,
        category_scope: list[str] | None = None,
        source_scope: list[str] | None = None,
        max_articles: int = 12,
        use_llm: bool = True,
        output_style: str = "结构化专题摘要",
        save_report: bool = True,
    ) -> dict[str, Any]:
        results = await self.search_service.search(topic, category_scope or None, source_scope or None, None, max_results=max_articles, include_remote=False)
        evidence = _evidence_payload(self.store, results)
        generation_method = "fallback"
        if use_llm and self.llm.configured and evidence:
            summary, generation_method = await self._llm_summary(topic, category_scope or [], output_style, evidence)
        else:
            summary = _fallback_summary(topic, category_scope or [], evidence)
            if not evidence:
                generation_method = "fallback_no_evidence"
            elif not use_llm:
                generation_method = "fallback_llm_disabled"
            elif not self.llm.configured:
                generation_method = "fallback_llm_unconfigured"
        if not summary.markdown:
            summary.markdown = _summary_markdown(summary, evidence)
        report = {
            "topic": topic,
            "category_scope": category_scope or [],
            "source_scope": source_scope or [],
            "report_type": "topic_summary_skill",
            "output_style": output_style,
            "generation_method": generation_method,
            "summary": summary.model_dump(mode="json"),
            "evidence": evidence,
            "sources": [{"article_id": item.article_id, "source_id": item.source_id, "title": item.title, "url": item.url} for item in results],
        }
        report_id = self.store.save_report(user_id, topic, category_scope or [], report) if save_report else None
        self.store.log(
            "topic_summary",
            "ok",
            topic,
            {
                "report_id": report_id,
                "evidence_count": len(evidence),
                "generation_method": generation_method,
                "llm_requested": bool(use_llm),
                "llm_configured": bool(self.llm.configured),
            },
        )
        return {
            "report_id": report_id,
            "topic": topic,
            "category_scope": category_scope or [],
            "source_scope": source_scope or [],
            "generation_method": generation_method,
            "summary": summary.model_dump(mode="json"),
            "markdown": summary.markdown,
            "evidence": evidence,
            "source_count": len(evidence),
        }

    async def _llm_summary(self, topic: str, category_scope: list[str], output_style: str, evidence: list[dict[str, Any]]) -> tuple[TopicSummaryOutput, str]:
        messages = [
            {"role": "system", "content": self.prompt_path.read_text(encoding="utf-8")},
            {
                "role": "user",
                "content": (
                    f"专题：{topic}\n"
                    f"板块：{', '.join(category_scope) or '不限'}\n"
                    f"输出风格：{output_style}\n\n"
                    "证据：\n"
                    + "\n\n".join(_evidence_block(item) for item in evidence[:12])
                ),
            },
        ]
        try:
            raw = await self.llm.structured(messages, "topic_summary", TopicSummaryOutput.model_json_schema())
            coerced = _coerce_summary_payload(raw, topic, evidence)
            return _normalize_summary(TopicSummaryOutput.model_validate(coerced), evidence), "llm_structured"
        except Exception as exc:
            self.store.log("topic_summary_llm", "error", topic, {"error": str(exc)})
            return _fallback_summary(topic, category_scope, evidence), "fallback_after_llm_error"


def _evidence_payload(store: NewsStore, results: list[SearchResult]) -> list[dict[str, Any]]:
    evidence = []
    for index, item in enumerate(results, start=1):
        row = store.get_article(item.article_id) if item.article_id else None
        content = (row or {}).get("content") or item.summary or ""
        evidence.append(
            {
                "index": index,
                "article_id": item.article_id,
                "source_id": item.source_id,
                "title": item.title,
                "url": item.url,
                "category": item.category,
                "published_at": item.published_at.isoformat() if hasattr(item.published_at, "isoformat") else item.published_at,
                "summary": item.summary or summarize(content, 180),
                "content_excerpt": content[:900],
                "origin": item.origin,
                "score": item.score,
            }
        )
    return evidence


def _evidence_block(item: dict[str, Any]) -> str:
    return (
        f"[{item['index']}] {item.get('title')}\n"
        f"来源：{item.get('source_id')}｜板块：{item.get('category')}｜时间：{item.get('published_at') or 'unknown'}\n"
        f"摘要：{item.get('summary') or ''}\n"
        f"正文片段：{item.get('content_excerpt') or ''}\n"
        f"链接：{item.get('url') or ''}"
    )


def _fallback_summary(topic: str, category_scope: list[str], evidence: list[dict[str, Any]]) -> TopicSummaryOutput:
    if not evidence:
        summary = TopicSummaryOutput(
            title=f"{topic}专题摘要",
            lead=f"当前本地库没有找到「{topic}」的可用证据。",
            sections=[
                TopicSummarySection(
                    title="证据状态",
                    body="暂无足够材料形成专题摘要，需要先执行抓取或扩大检索范围。",
                    bullets=["建议先运行源搜索入库或等待下一轮自动抓取。"],
                    evidence_indices=[],
                )
            ],
            timeline=[],
            graph=TopicSummaryGraph(nodes=[TopicSummaryGraphNode(id="topic", label=topic, type="topic", description="专题中心", evidence_indices=[])], edges=[]),
            analysis=["证据不足时不应生成强结论。"],
            uncertainty=["没有可引用来源。"],
            markdown="",
            confidence=0.2,
        )
        summary.markdown = _summary_markdown(summary, evidence)
        return summary

    combined = "\n".join(f"{item['title']}。{item.get('summary') or item.get('content_excerpt') or ''}" for item in evidence)
    entities = extract_entities(combined, limit=10)
    keywords = extract_keywords(combined, limit=10)
    timeline = [
        TopicSummaryTimelineItem(
            date=(item.get("published_at") or "unknown")[:10],
            title=item["title"],
            summary=item.get("summary") or item.get("content_excerpt", "")[:160],
            stage=_stage(index, len(evidence)),
            actors=extract_entities(f"{item['title']} {item.get('summary') or ''}", limit=4),
            evidence_indices=[item["index"]],
        )
        for index, item in enumerate(sorted(evidence, key=lambda row: row.get("published_at") or ""), start=0)
    ][:8]
    graph = _fallback_graph(topic, evidence, entities, keywords)
    sections = [
        TopicSummarySection(
            title="一句话导语",
            body=summarize(combined, 180),
            bullets=[evidence[0]["title"]],
            evidence_indices=[evidence[0]["index"]],
        ),
        TopicSummarySection(
            title="核心事件",
            body=f"围绕「{topic}」共召回 {len(evidence)} 条证据，主要集中在 {', '.join(category_scope) or '不限板块'}。",
            bullets=[item["title"] for item in evidence[:4]],
            evidence_indices=[item["index"] for item in evidence[:4]],
        ),
        TopicSummarySection(
            title="人物/主体与关系",
            body="当前可识别主体包括：" + ("、".join(entities[:8]) if entities else "暂无稳定主体。"),
            bullets=keywords[:6],
            evidence_indices=[item["index"] for item in evidence[:6]],
        ),
        TopicSummarySection(
            title="来源与不确定性",
            body="默认摘要来自本地已入库新闻，仍需要关注多源交叉验证和发布时间准确性。",
            bullets=[f"{item['source_id']}：{item['title']}" for item in evidence[:5]],
            evidence_indices=[item["index"] for item in evidence[:5]],
        ),
    ]
    summary = TopicSummaryOutput(
        title=f"{topic}专题摘要",
        lead=summarize(combined, 220),
        sections=sections,
        timeline=timeline,
        graph=graph,
        analysis=[
            "当前结论主要来自标题、摘要和正文片段，适合做快速专题理解。",
            "若同一事件只有单一来源，应视为待验证线索。",
            "后续观察重点是新主体是否出现、事件是否获得多源确认、影响链是否扩展。",
        ],
        uncertainty=["部分文章可能缺少准确发布时间。", "本地库覆盖不足时，热点排序可能偏向已抓取来源。"],
        markdown="",
        confidence=0.58 if len(evidence) < 4 else 0.68,
    )
    summary.markdown = _summary_markdown(summary, evidence)
    return summary


def _fallback_graph(topic: str, evidence: list[dict[str, Any]], entities: list[str], keywords: list[str]) -> TopicSummaryGraph:
    nodes = [TopicSummaryGraphNode(id="topic", label=topic, type="topic", description="专题中心", evidence_indices=[item["index"] for item in evidence[:8]])]
    term_counter = Counter([term for term in [*entities, *keywords] if term and term != topic])
    for term, _ in term_counter.most_common(10):
        nodes.append(
            TopicSummaryGraphNode(
                id=stable_id("node", term),
                label=term,
                type=_node_type(term),
                description="证据中抽取的相关主体或概念。",
                evidence_indices=_term_evidence(term, evidence),
            )
        )
    edges = [
        TopicSummaryGraphEdge(
            source="topic",
            target=node.id,
            label="相关",
            description=f"{node.label} 与专题在证据中共同出现。",
            evidence_indices=node.evidence_indices[:4],
        )
        for node in nodes[1:9]
    ]
    return TopicSummaryGraph(nodes=nodes, edges=edges)


def _term_evidence(term: str, evidence: list[dict[str, Any]]) -> list[int]:
    indices = []
    for item in evidence:
        text = f"{item.get('title') or ''} {item.get('summary') or ''} {item.get('content_excerpt') or ''}"
        if term in text:
            indices.append(item["index"])
    return indices[:6]


def _node_type(term: str) -> str:
    if any(token in term for token in ["公司", "集团", "车队", "官方", "委员会", "政府"]):
        return "organization"
    if any(token in term for token in ["中国", "美国", "俄罗斯", "乌克兰", "北京", "上海", "欧洲"]):
        return "place"
    if any(token in term for token in ["事件", "发布", "比赛", "冲突", "争议"]):
        return "event"
    if len(term) <= 4 and not any(char.isdigit() for char in term):
        return "person"
    return "concept"


def _stage(index: int, total: int) -> str:
    if total <= 2:
        return "update"
    if index == 0:
        return "origin"
    if index >= total - 2:
        return "latest"
    return "development"


def _normalize_summary(summary: TopicSummaryOutput, evidence: list[dict[str, Any]]) -> TopicSummaryOutput:
    allowed = {item["index"] for item in evidence}
    for section in summary.sections:
        section.evidence_indices = [index for index in section.evidence_indices if index in allowed][:8]
    for item in summary.timeline:
        item.evidence_indices = [index for index in item.evidence_indices if index in allowed][:8]
    for node in summary.graph.nodes:
        node.evidence_indices = [index for index in node.evidence_indices if index in allowed][:8]
    for edge in summary.graph.edges:
        edge.evidence_indices = [index for index in edge.evidence_indices if index in allowed][:8]
    summary.graph.nodes = summary.graph.nodes[:12]
    summary.graph.edges = summary.graph.edges[:16]
    summary.timeline = summary.timeline[:8]
    summary.sections = summary.sections[:8]
    if not summary.markdown:
        summary.markdown = _summary_markdown(summary, evidence)
    return summary


def _coerce_summary_payload(raw: dict[str, Any], topic: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Tolerate providers that ignore strict response_format but still return useful JSON."""
    payload = dict(raw or {})
    payload.setdefault("title", f"{topic}专题摘要")
    payload.setdefault("lead", summarize("。".join(str(item.get("title") or "") for item in evidence), 160) or f"{topic}专题摘要。")
    payload.setdefault("sections", [])
    payload.setdefault("timeline", [])
    payload.setdefault("graph", {})
    payload.setdefault("analysis", [])
    payload.setdefault("uncertainty", [])
    payload.setdefault("markdown", "")
    payload.setdefault("confidence", 0.6)

    if isinstance(payload.get("analysis"), str):
        payload["analysis"] = [payload["analysis"]]
    if isinstance(payload.get("uncertainty"), str):
        payload["uncertainty"] = [payload["uncertainty"]]

    allowed_indices = {int(item["index"]) for item in evidence if "index" in item}

    sections = []
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue
        item = dict(section)
        item.setdefault("body", item.pop("content", ""))
        item.setdefault("bullets", item.pop("points", []))
        item["bullets"] = _list_of_str(item.get("bullets"))
        item["evidence_indices"] = _coerce_evidence_indices(item, allowed_indices)
        item.setdefault("title", "专题章节")
        sections.append(item)
    payload["sections"] = sections

    timeline = []
    for entry in payload.get("timeline") or []:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        item.setdefault("title", item.pop("event", "事件进展"))
        item.setdefault("summary", item.get("description") or item.get("body") or item.get("title") or "")
        item.setdefault("stage", "update")
        item.setdefault("actors", [])
        item["actors"] = _list_of_str(item.get("actors"))
        item["evidence_indices"] = _coerce_evidence_indices(item, allowed_indices)
        item.setdefault("date", "unknown")
        timeline.append(item)
    payload["timeline"] = timeline

    graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else {}
    nodes = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        item = dict(node)
        item.setdefault("id", stable_id("node", item.get("label") or item.get("name") or "node"))
        item.setdefault("label", item.get("name") or item["id"])
        item.setdefault("type", "concept")
        item.setdefault("description", item.get("summary") or "")
        item["evidence_indices"] = _coerce_evidence_indices(item, allowed_indices)
        nodes.append(item)
    edges = []
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        item = dict(edge)
        item.setdefault("source", "")
        item.setdefault("target", "")
        item.setdefault("label", item.pop("relation", "相关"))
        item.setdefault("description", item.get("label") or "相关")
        item["evidence_indices"] = _coerce_evidence_indices(item, allowed_indices)
        edges.append(item)
    payload["graph"] = {"nodes": nodes, "edges": edges}
    return payload


def _coerce_evidence_indices(item: dict[str, Any], allowed_indices: set[int]) -> list[int]:
    value = item.get("evidence_indices")
    if value is None and "source_index" in item:
        value = [item.get("source_index")]
    if value is None and "evidence_index" in item:
        value = [item.get("evidence_index")]
    if isinstance(value, int):
        value = [value]
    indices = []
    for raw_index in value or []:
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if index in allowed_indices:
            indices.append(index)
    return indices


def _list_of_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _summary_markdown(summary: TopicSummaryOutput, evidence: list[dict[str, Any]]) -> str:
    lines = [f"# {summary.title}", "", summary.lead, ""]
    for section in summary.sections:
        lines.extend([f"## {section.title}", "", section.body])
        for bullet in section.bullets[:6]:
            lines.append(f"- {bullet}")
        if section.evidence_indices:
            lines.append(f"证据：{', '.join(str(index) for index in section.evidence_indices)}")
        lines.append("")
    if summary.timeline:
        lines.extend(["## 时间线", ""])
        for item in summary.timeline:
            lines.append(f"- {item.date}：{item.title}｜{item.summary}")
        lines.append("")
    if summary.graph.nodes:
        lines.extend(["## 人物/事件图谱", ""])
        node_labels = "、".join(node.label for node in summary.graph.nodes[:10])
        lines.append(f"核心节点：{node_labels}")
        for edge in summary.graph.edges[:8]:
            lines.append(f"- {edge.source} --{edge.label}--> {edge.target}：{edge.description}")
        lines.append("")
    if summary.analysis:
        lines.extend(["## 我的分析", ""])
        lines.extend(f"- {item}" for item in summary.analysis[:6])
        lines.append("")
    if summary.uncertainty:
        lines.extend(["## 不确定性", ""])
        lines.extend(f"- {item}" for item in summary.uncertainty[:6])
        lines.append("")
    if evidence:
        lines.extend(["## 来源", ""])
        for item in evidence[:8]:
            lines.append(f"- [{item['index']}] {item['title']}（{item['source_id']}）{item.get('url') or ''}")
    return "\n".join(lines).strip()

from __future__ import annotations

import re
from typing import Any

from personal_news_agent.services.cc_runtime import HOT_EVENT_MAP_SKILL_NAME
from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.report import _categories, _parse_args


class HotEventMapSkill:
    spec = SkillSpec(
        command="/map",
        name="热点事件图谱",
        description="检索热点事件的主体、时间线与影响关系，并生成可视化 Mermaid 图谱。",
        usage="/map <热点事件> [--category tech,economy]",
        examples=("/map 人工智能终端新品发布", "/map 某地极端天气应急响应 --category politics"),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        topic, options = _parse_args(args)
        topic = topic or context.topic or ""
        if not topic:
            raise ValueError(f"缺少热点事件。用法：{self.spec.usage}")
        categories = _categories(options.get("category")) or context.category_scope or []
        runtime = context.services.get("cc_runtime")
        if runtime and getattr(runtime, "configured", False):
            result = await runtime.run(
                message=f"请研究热点事件【{topic}】，生成证据化事件图谱。",
                query=topic,
                topic=topic,
                category_scope=categories,
                time_range=None,
                history="",
                allow_web_search=context.allow_web_search,
                skill_names=[HOT_EVENT_MAP_SKILL_NAME],
                max_turns=10,
                builtin_web_search_limit=3,
                on_trace=context.on_trace,
            )
            evidence = _runtime_evidence(result.results)
            return SkillResult(
                command=self.spec.command,
                title=f"热点事件图谱：{topic}",
                message=f"已整理 {len(evidence)} 条证据并生成事件图谱。",
                data={
                    "topic": topic,
                    "category_scope": categories,
                    "markdown": _sanitize_event_map_markdown(result.answer),
                    "evidence": evidence,
                    "expanded_queries": result.queries[:8],
                    "research_trace": result.trace,
                    "agent_source": "cc_runtime",
                },
            )

        search = context.services.get("search")
        results = await search.search(topic, categories or None, None, None, max_results=8, include_remote=False)
        evidence = _runtime_evidence(results)
        markdown = _fallback_map(topic, evidence)
        return SkillResult(
            command=self.spec.command,
            title=f"热点事件图谱：{topic}",
            message="Agent 主控暂不可用，已用本地新闻引擎生成基础图谱。",
            data={
                "topic": topic,
                "category_scope": categories,
                "markdown": markdown,
                "evidence": evidence,
                "research_trace": [
                    {
                        "stage": "热点事件图谱",
                        "status": "fallback",
                        "message": "Agent 主控暂不可用，已生成基础事件图谱。",
                    }
                ],
                "agent_source": "fallback",
            },
        )


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


def _fallback_map(topic: str, evidence: list[dict[str, Any]]) -> str:
    safe_topic = _mermaid_label(topic)
    lines = ["flowchart LR", f'  event["{safe_topic}"]']
    for index, item in enumerate(evidence[:6], start=1):
        title = _mermaid_label(item.get("title") or f"线索 {index}")
        lines.extend([f'  evidence{index}["{title}"]', f'  evidence{index} -->|报道| event'])
    if not evidence:
        lines.extend(['  pending["等待可引用证据"]', '  pending -.->|待核实| event', '  class pending uncertain'])
    lines.append("  classDef uncertain stroke:#f59e0b,stroke-dasharray:5 5,color:#fbbf24")
    source_lines = [f"- [{item['title']}]({item['url']})" for item in evidence[:8]] or ["- 暂无可引用来源"]
    return (
        "## 事件图谱\n\n```mermaid\n"
        + "\n".join(lines)
        + "\n```\n\n## 关键解读\n\n- 当前图谱仅展示本地新闻引擎召回的直接报道关系。"
        + "\n\n## 证据来源\n\n"
        + "\n".join(source_lines)
        + "\n\n## 不确定性\n\n- Agent 主控暂不可用，主体关系、因果链和时间线仍需进一步核验。"
    )


def _mermaid_label(value: Any) -> str:
    return " ".join(str(value or "").replace('"', "”").replace("`", "").split())[:72]


def _sanitize_event_map_markdown(markdown: str) -> str:
    text = str(markdown or "")[:24_000]
    section_start = text.find("## 事件图谱")
    if section_start >= 0:
        text = text[section_start:]
    match = re.search(r"```mermaid\s*\n(?P<code>[\s\S]*?)```", text, flags=re.IGNORECASE)
    if not match:
        return text
    code_lines: list[str] = []
    for line in match.group("code").splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("click ") or stripped.startswith("%%{"):
            continue
        clean = re.sub(r"<br\s*/?>", " · ", line, flags=re.IGNORECASE)
        clean = re.sub(r"<[^>]{1,200}>", "", clean)
        code_lines.append(clean)
    code = _repair_mermaid_source("\n".join(code_lines).strip())[:8_000]
    if not code.lower().startswith("flowchart lr"):
        code = "flowchart LR\n" + code
    return text[: match.start()] + f"```mermaid\n{code}\n```" + text[match.end() :]


def _repair_mermaid_source(source: str) -> str:
    """Repair a narrow, common CC edge-label transposition without inventing graph facts."""
    text = str(source or "")
    # Mermaid expects A -->|"label"| B. Models occasionally emit
    # A -->|"label|" B, which is visually plausible but does not parse.
    text = re.sub(
        r'\|"([^"\n|]{1,120})\|"(?=\s+[A-Za-z_][A-Za-z0-9_-]*(?:\s|$))',
        r'|"\1"|',
        text,
    )
    return text

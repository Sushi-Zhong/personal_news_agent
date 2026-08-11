from __future__ import annotations

import json

from personal_news_agent.services.cc_runtime import NEWS_SOURCE_AUDIT_SKILL_NAME
from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec


class SourcesSkill:
    spec = SkillSpec(
        command="/sources",
        name="来源审计",
        description="查看资讯源数量、分类、可信度以及抓取和搜索状态。",
        usage="/sources [category]",
        examples=("/sources", "/sources tech"),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        if len(args) > 1:
            raise ValueError(f"参数过多。用法：{self.spec.usage}")
        registry = context.services["registry"]
        category = args[0] if args else None
        sources = (
            registry.get_sources_by_category(category)
            if category
            else registry.all_sources()
        )
        items = [
            {
                "source_id": source.source_id,
                "name": source.name,
                "domain": source.root_domain,
                "categories": list(source.categories),
                "tags": list(source.tags),
                "credibility": source.credibility,
                "crawl_enabled": source.crawl_enabled,
                "search_enabled": source.search_enabled,
            }
            for source in sources
        ]
        summary = registry.source_summary()
        data = {"category": category, "summary": summary, "items": items}
        runtime = context.services.get("cc_runtime")
        if runtime and getattr(runtime, "configured", False):
            try:
                result = await runtime.run(
                    message=(
                        f"请审计系统配置的{'全部' if not category else category}新闻来源，"
                        "说明覆盖、可用性抽查边界、风险和改进建议。"
                    ),
                    query=f"{category or '主要门户'} 新闻来源 可用性",
                    topic=f"{category or '全部'}资讯源审计",
                    category_scope=[category] if category else [],
                    time_range=None,
                    history=json.dumps(data, ensure_ascii=False)[:8_000],
                    allow_web_search=context.allow_web_search,
                    allow_local_search=False,
                    skill_names=[NEWS_SOURCE_AUDIT_SKILL_NAME],
                    max_turns=8,
                    builtin_web_search_limit=2,
                    on_trace=context.on_trace,
                )
                data.update(
                    {
                        "markdown": result.answer,
                        "research_trace": result.trace,
                        "expanded_queries": result.queries[:8],
                        "recommendations": [item.model_dump(mode="json") for item in result.results[:8]],
                        "agent_source": "cc_runtime",
                    }
                )
            except Exception as exc:
                data.update(
                    {
                        "agent_source": "fallback",
                        "research_trace": [
                            {
                                "stage": "来源审计 Skill",
                                "status": "fallback",
                                "message": f"Agent 主控审计失败，保留配置清单：{type(exc).__name__}。",
                            }
                        ],
                    }
                )
        return SkillResult(
            command=self.spec.command,
            title="资讯源" if not category else f"{category} 资讯源",
            message=f"找到 {len(items)} 个来源，其中 {sum(item['crawl_enabled'] for item in items)} 个可抓取。",
            data=data,
        )

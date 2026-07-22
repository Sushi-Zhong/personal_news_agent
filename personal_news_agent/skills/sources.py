from __future__ import annotations

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
        return SkillResult(
            command=self.spec.command,
            title="资讯源" if not category else f"{category} 资讯源",
            message=f"找到 {len(items)} 个来源，其中 {sum(item['crawl_enabled'] for item in items)} 个可抓取。",
            data={"category": category, "summary": summary, "items": items},
        )

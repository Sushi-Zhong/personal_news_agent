from __future__ import annotations

from personal_news_agent.skills.arguments import CompareArguments
from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec


class CompareSkill:
    spec = SkillSpec(
        command="/compare",
        name="报道对比",
        description="合并同稿分发后，比较独立来源的共同事实、独有说法、冲突和叙事侧重。",
        usage="/compare <主题> [--category tech,economy] [--source source-a,source-b]",
        examples=("/compare OpenAI Agent 产品 --source official,analysis",),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        return await self.run_structured(_parse_compare_args(args), context)

    async def run_structured(self, arguments: CompareArguments, context: SkillContext) -> SkillResult:
        topic = (arguments.topic or context.topic or "").strip()
        if not topic:
            raise ValueError(f"缺少新闻主题。用法：{self.spec.usage}")
        outcome = await context.services["coverage_comparison"].run(
            topic=topic,
            category_scope=arguments.category_scope or context.category_scope or [],
            source_scope=arguments.source_scope,
            include_remote=context.allow_web_search,
        )
        payload = outcome.data.model_dump(mode="json")
        message = (
            "已完成独立来源报道对比。"
            if payload["comparison_status"] == "sufficient"
            else "独立来源不足，当前只能展示有限分组。"
        )
        return SkillResult(
            command=self.spec.command,
            skill_id="compare",
            title=f"报道对比：{topic}",
            message=message,
            data=payload,
            status=outcome.status,
            output_kind="coverage_compare",
            evidence=tuple(outcome.data.evidence),
            fallback_reason=outcome.fallback_reason,
        )


def _parse_compare_args(args: list[str]) -> CompareArguments:
    topic_parts: list[str] = []
    categories: list[str] = []
    sources: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"--category", "--categories", "--scope"} and index + 1 < len(args):
            categories = [item.strip() for item in args[index + 1].split(",") if item.strip()]
            index += 2
            continue
        if token in {"--source", "--sources"} and index + 1 < len(args):
            sources = [item.strip() for item in args[index + 1].split(",") if item.strip()]
            index += 2
            continue
        topic_parts.append(token)
        index += 1
    return CompareArguments(
        topic=" ".join(topic_parts).strip() or None,
        category_scope=categories,
        source_scope=sources,
    )

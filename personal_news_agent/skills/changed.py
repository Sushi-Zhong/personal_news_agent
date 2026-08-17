from __future__ import annotations

from personal_news_agent.skills.arguments import ChangedArguments
from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec


class ChangedSkill:
    spec = SkillSpec(
        command="/changed",
        name="变化摘要",
        description="只报告主题相对基线出现的实质变化，并区分重复报道。",
        usage="/changed <主题> [--baseline 昨天|过去一周] [--category tech,economy]",
        examples=("/changed OpenAI Agent SDK --baseline 昨天",),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        parsed = _parse_changed_args(args)
        return await self.run_structured(parsed, context)

    async def run_structured(self, arguments: ChangedArguments, context: SkillContext) -> SkillResult:
        topic = (arguments.topic or context.topic or "").strip()
        if not topic:
            raise ValueError(f"缺少新闻主题。用法：{self.spec.usage}")
        outcome = await context.services["change_detection"].run(
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            topic=topic,
            baseline_expression=arguments.baseline_expression,
            category_scope=arguments.category_scope or context.category_scope or [],
            include_remote=context.allow_web_search,
        )
        payload = outcome.data.model_dump(mode="json")
        business_status = payload["change_status"]
        message = {
            "changed": "检测到实质变化。",
            "no_material_change": "未检测到实质变化。",
            "insufficient": "当前证据不足以判断是否发生实质变化。",
        }[business_status]
        return SkillResult(
            command=self.spec.command,
            skill_id="changed",
            title=f"变化摘要：{topic}",
            message=message,
            data=payload,
            status=outcome.status,
            output_kind="change_digest",
            evidence=tuple(outcome.data.evidence),
            fallback_reason=outcome.fallback_reason,
        )


def _parse_changed_args(args: list[str]) -> ChangedArguments:
    topic_parts: list[str] = []
    baseline: str | None = None
    categories: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--baseline" and index + 1 < len(args):
            baseline = args[index + 1]
            index += 2
            continue
        if token in {"--category", "--categories", "--scope"} and index + 1 < len(args):
            categories = [item.strip() for item in args[index + 1].split(",") if item.strip()]
            index += 2
            continue
        topic_parts.append(token)
        index += 1
    return ChangedArguments(
        topic=" ".join(topic_parts).strip() or None,
        baseline_expression=baseline,
        category_scope=categories,
    )

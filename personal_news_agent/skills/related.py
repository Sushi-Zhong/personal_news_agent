from __future__ import annotations

from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.report import _categories, _parse_args


class RelatedNewsSkill:
    spec = SkillSpec(
        command="/related",
        name="相关新闻延展",
        description="结合当前对话消歧人物或事件，再检索其与当前主题的关系、近况和可靠来源。",
        usage="/related <人物、队伍、机构、产品或事件> [--category game,sports]",
        examples=("/related 这支队伍", "/related 赛事主办方", "/related 公司创始人 --category tech"),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        focus, options = _parse_args(args)
        focus = focus or context.topic or ""
        if not focus:
            raise ValueError(f"缺少要延展的对象。用法：{self.spec.usage}")
        response = await context.services["chat"].related_search(
            context.conversation_id,
            focus,
            topic=context.topic,
            category_scope=_categories(options.get("category")) or context.category_scope,
            user_id=context.user_id,
            allow_web_search=context.allow_web_search,
            on_trace=context.on_trace,
            save_turn=False,
        )
        payload = response.model_dump(mode="json")
        payload["markdown"] = response.markdown or response.answer
        return SkillResult(
            command=self.spec.command,
            title=f"相关新闻延展：{focus}",
            message="已结合当前对话完成消歧、检索与关系核验。",
            data=payload,
        )

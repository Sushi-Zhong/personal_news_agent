from __future__ import annotations

from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.report import _categories, _parse_args


class BriefSkill:
    spec = SkillSpec(
        command="/brief",
        name="今日简报",
        description="按指定主题或板块生成一次个性化资讯简报。",
        usage="/brief [主题] [--category tech,sports]",
        examples=("/brief", "/brief AI 产品 --category tech"),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        topic, options = _parse_args(args)
        topic = topic or (context.topic or "").strip() or "今日资讯"
        categories = _categories(options.get("category")) or (context.category_scope or [])
        reports = context.services["reports"]
        if context.conversation_id:
            report = await reports.generate_brief_from_conversation(
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                topic=topic,
                category_scope=categories,
                time_range="1d",
            )
        else:
            report = await reports.generate(
                user_id=context.user_id,
                topic=topic,
                category_scope=categories,
                time_range="1d",
                report_type="daily_digest",
            )
        payload = report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)
        return SkillResult(
            command=self.spec.command,
            title=f"今日简报：{topic}",
            message=f"今日简报已生成，共整理 {len(payload.get('sources') or [])} 个来源。",
            data=payload,
        )

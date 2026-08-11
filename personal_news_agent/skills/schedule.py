from __future__ import annotations

from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec


class ScheduleSkill:
    spec = SkillSpec(
        command="/schedule",
        name="定时资讯任务",
        description="由 Agent 解析自然语言周期、主题、抓取范围和报告样式，再创建用户级推送任务。",
        usage="/schedule <周期、时间、主题和报告要求>",
        examples=(
            "/schedule 每天早上9点汇总 AI Agent 新闻并生成简报",
            "/schedule 每周一上午8点给我周末关注热点专题",
        ),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        if not args:
            raise ValueError(f"缺少定时任务描述。用法：{self.spec.usage}")
        message = "/schedule " + " ".join(args)
        result = await context.services["tasks"].create_from_schedule_message(
            user_id=context.user_id,
            message=message,
            on_trace=context.on_trace,
        )
        task = result["task"]
        topic = (task.get("topics") or ["定时资讯"])[0]
        return SkillResult(
            command=self.spec.command,
            title=f"定时资讯任务：{topic}",
            message="定时任务已创建。",
            data={
                "markdown": result["answer"],
                "context_relation": "scheduled_push_created",
                "task": task,
                "api_params": result["api_params"],
                "conversation": result["conversation"],
                "research_trace": result.get("research_trace") or [],
                "required_context_items": ["scheduled_task", "scheduled_push_conversation"],
                "focus_object": {"type": "scheduled_task", "target_id": task["id"], "text": topic},
            },
        )

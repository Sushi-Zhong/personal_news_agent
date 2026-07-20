from __future__ import annotations

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
                strict_topic_filter=explicit_topic,
            )
            payload = report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)
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

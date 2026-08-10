from __future__ import annotations

from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.report import _categories, _parse_args


class FactCheckSkill:
    spec = SkillSpec(
        command="/factcheck",
        name="事实核查",
        description="自动联网搜索并结合本地证据，由大模型围绕一条具体说法给出保守判定。",
        usage="/factcheck <待核查说法> [--category tech,economy]",
        examples=("/factcheck 淘宝近期调整过下载落地页策略", "/factcheck 某公司发布绿色家电补贴计划 --category economy"),
    )

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        claim, options = _parse_args(args)
        if not claim:
            raise ValueError(f"缺少待核查说法。用法：{self.spec.usage}")
        result = await context.services["factcheck"].run(
            user_id=context.user_id,
            claim=claim,
            category_scope=_categories(options.get("category")),
            include_remote=True,
        )
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        return SkillResult(
            command=self.spec.command,
            title=f"事实核查：{claim}",
            message=f"核查完成：{payload.get('verdict')}，置信度 {payload.get('confidence')}",
            data=payload,
        )

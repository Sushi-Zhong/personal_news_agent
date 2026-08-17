from __future__ import annotations

from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.report import _categories, _parse_args


class FactCheckSkill:
    spec = SkillSpec(
        command="/factcheck",
        name="事实核查",
        description="按用户联网设置检索证据，并围绕一条具体说法给出保守判定。",
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
            include_remote=context.allow_web_search,
            on_trace=context.on_trace,
        )
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        payload["research_mode"] = "hybrid" if context.allow_web_search else "local_only"
        locally_supported = payload.get("verdict") in {"supported", "contradicted", "mixed"} and bool(
            payload.get("supporting_evidence") or payload.get("contradicting_evidence")
        )
        offline_insufficient = not context.allow_web_search and not locally_supported
        return SkillResult(
            command=self.spec.command,
            title=f"事实核查：{claim}",
            message=f"核查完成：{payload.get('verdict')}，置信度 {payload.get('confidence')}",
            data=payload,
            status="degraded" if offline_insufficient else "success",
            output_kind="fact_check",
            fallback_reason="web_search_disabled" if offline_insufficient else None,
        )

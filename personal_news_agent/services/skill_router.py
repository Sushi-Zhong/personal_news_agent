from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from personal_news_agent.skills.catalog import all_definitions
from personal_news_agent.skills.manifest import SkillDefinition, import_reference


class SkillRoutingContext(BaseModel):
    topic: str | None = None
    category_scope: list[str] = Field(default_factory=list)
    conversation_id: str | None = None
    user_id: str = "default"


class SkillRoute(BaseModel):
    skill_id: str | None
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["slash_command", "rule", "model", "fallback"]
    confirmation_required: bool = False


class _ModelRoute(BaseModel):
    skill_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SkillRouter:
    RULE_CONFIDENCE = 0.95
    MODEL_CONFIDENCE = 0.82

    def __init__(self, llm_client: Any, *, definitions: list[SkillDefinition] | None = None) -> None:
        self.llm_client = llm_client
        self.definitions = [item for item in (definitions or all_definitions()) if item.enabled]
        self._by_id = {item.id: item for item in self.definitions}
        self._by_command = {
            command.lower(): item
            for item in self.definitions
            for command in (*item.commands, *item.aliases)
        }

    async def route(
        self,
        message: str,
        *,
        context: SkillRoutingContext,
        model_key: str,
        use_llm: bool,
    ) -> SkillRoute:
        text = " ".join(str(message or "").split()).strip()
        slash = self._slash_route(text)
        if slash:
            return slash
        if _excluded_non_news_request(text):
            return _fallback()
        deterministic = self._rule_route(text, context)
        if deterministic:
            return deterministic
        if not use_llm or not text:
            return _fallback()
        return await self._model_route(text, context, model_key)

    def _slash_route(self, text: str) -> SkillRoute | None:
        if not text.startswith("/"):
            return None
        command = text.split(maxsplit=1)[0].lower()
        definition = self._by_command.get(command)
        if not definition:
            return SkillRoute(
                skill_id=None,
                confidence=1.0,
                source="slash_command",
                confirmation_required=False,
            )
        return SkillRoute(
            skill_id=definition.id,
            confidence=1.0,
            source="slash_command",
            confirmation_required=definition.confirmation_required,
        )

    def _rule_route(self, text: str, context: SkillRoutingContext) -> SkillRoute | None:
        if _schedule_intent(text):
            return self._route_for("schedule", {"raw_request": text})
        if _factcheck_intent(text):
            return self._route_for("factcheck", {"topic": _topic_from_text(text, context.topic)})
        if _compare_intent(text, context.topic):
            return self._route_for("compare", {"topic": _topic_from_text(text, context.topic)})
        if _changed_intent(text, context.topic):
            return self._route_for(
                "changed",
                {
                    "topic": _topic_from_text(text, context.topic),
                    "baseline_expression": _baseline_expression(text),
                    "category_scope": context.category_scope,
                },
            )
        if _map_intent(text):
            return self._route_for("map", {"topic": _topic_from_text(text, context.topic)})
        if _brief_intent(text):
            return self._route_for("brief", {"topic": _topic_from_text(text, context.topic)})
        if _report_intent(text):
            return self._route_for("report", {"topic": _topic_from_text(text, context.topic)})
        return None

    def _route_for(self, skill_id: str, arguments: dict[str, Any]) -> SkillRoute | None:
        definition = self._by_id.get(skill_id)
        if not definition:
            return None
        validated = _validate_arguments(definition, arguments)
        if validated is None:
            return None
        return SkillRoute(
            skill_id=skill_id,
            arguments=validated,
            confidence=self.RULE_CONFIDENCE,
            source="rule",
            confirmation_required=definition.confirmation_required,
        )

    async def _model_route(self, text: str, context: SkillRoutingContext, model_key: str) -> SkillRoute:
        candidates = [
            item
            for item in self.definitions
            if item.arguments_model
            and item.exposure == "public"
            and (item.side_effect == "read_only" or item.confirmation_required)
        ]
        schema = _ModelRoute.model_json_schema()
        schema["properties"]["skill_id"] = {
            "anyOf": [
                {"type": "string", "enum": [item.id for item in candidates]},
                {"type": "null"},
            ],
            "default": None,
        }
        try:
            raw = await self.llm_client.structured(
                [
                    {
                        "role": "system",
                        "content": (
                            "Classify one news-assistant intent. Return none when uncertain or when the request is not "
                            "about news research. Never chain skills."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Current topic: {context.topic or ''}\nRequest: {text}",
                    },
                ],
                "skill_route",
                schema,
                model_key=model_key,
            )
            parsed = _ModelRoute.model_validate(raw)
        except Exception:
            return _fallback()
        if parsed.confidence < self.MODEL_CONFIDENCE or not parsed.skill_id:
            return _fallback()
        definition = self._by_id.get(parsed.skill_id)
        if not definition or definition not in candidates:
            return _fallback()
        validated = _validate_arguments(definition, parsed.arguments)
        if validated is None:
            return _fallback()
        return SkillRoute(
            skill_id=definition.id,
            arguments=validated,
            confidence=parsed.confidence,
            source="model",
            confirmation_required=definition.confirmation_required,
        )


def _validate_arguments(definition: SkillDefinition, arguments: dict[str, Any]) -> dict[str, Any] | None:
    if not definition.arguments_model:
        return arguments
    try:
        model = import_reference(definition.arguments_model)
        return model.model_validate(arguments).model_dump(mode="json")
    except (ValueError, ValidationError):
        return None


def _fallback() -> SkillRoute:
    return SkillRoute(skill_id=None, confidence=0.0, source="fallback")


def _excluded_non_news_request(text: str) -> bool:
    return bool(
        re.search(r"比较.{0,8}(两句|句子|语法|产品|参数|代码|实现|文件|版本)", text, re.IGNORECASE)
        or re.search(r"(两句|句子|语法|产品参数|Python|代码).{0,8}(区别|差异|比较)", text, re.IGNORECASE)
    )


def _schedule_intent(text: str) -> bool:
    time_anchor = re.search(r"(每天|每周|每月|工作日|定时|周期|早上|上午|下午|晚上|\d{1,2}[点:时])", text)
    action = re.search(r"(推送|汇总|提醒|生成|发送|给我.*新闻)", text)
    return bool(time_anchor and action)


def _factcheck_intent(text: str) -> bool:
    return bool(re.search(r"(核实|核查|查证|事实核查|是否属实|是真是假|真假)", text))


def _compare_intent(text: str, topic: str | None) -> bool:
    comparison = re.search(r"(不同|国内外|共同确认|冲突|区别|差异|怎么报道|如何报道)", text)
    source_anchor = re.search(r"(媒体|来源|报道|新闻机构|报纸|通讯社)", text)
    return bool(comparison and source_anchor and (topic or len(text) >= 10))


def _changed_intent(text: str, topic: str | None) -> bool:
    change = re.search(r"(新进展|新增内容|后来.{0,4}(变化|进展)|有什么变化|从.{0,12}到现在|自上次|只告诉我新增)", text)
    return bool(change and (topic or len(text) >= 10))


def _map_intent(text: str) -> bool:
    return bool(re.search(r"(画|生成|整理).{0,8}(关系图|事件图谱|时间线图|可视化)", text))


def _brief_intent(text: str) -> bool:
    return bool(re.search(r"(简报|早报|晚报|今日新闻汇总|新闻摘要)", text))


def _report_intent(text: str) -> bool:
    return bool(re.search(r"(专题报告|复盘报告|事件报告|专题档案)", text))


def _baseline_expression(text: str) -> str | None:
    match = re.search(r"(昨天|过去\s*24\s*小时|最近一周|过去一周|近一周|自\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    return match.group(1).replace(" ", "") if match else None


def _topic_from_text(text: str, context_topic: str | None) -> str | None:
    if context_topic:
        return context_topic.strip() or None
    cleaned = re.sub(
        r"(请|帮我|给我|只告诉我|从昨天到现在|有什么新进展|有什么变化|后来有变化吗|是否属实|是真是假|"
        r"核实|核查|查证|不同媒体|国内外媒体|怎么报道|如何报道|共同确认|哪里冲突|生成|整理成|做一份|？|\?)",
        " ",
        text,
    )
    cleaned = " ".join(cleaned.split()).strip(" ，。！？:：")
    return cleaned[:240] or None

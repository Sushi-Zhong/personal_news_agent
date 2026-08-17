from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from personal_news_agent.core.categories import CATEGORIES
from personal_news_agent.core.models import FocusObject, SearchResult
from personal_news_agent.core.text import summarize
from personal_news_agent.services.chat_understanding import infer_categories
from personal_news_agent.services.cc_runtime import SCHEDULED_NEWS_TASK_SKILL_NAME
from personal_news_agent.services.llm import LLMClient
from personal_news_agent.services.reports import ReportGenerationService
from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.time_context import DEFAULT_APP_TIMEZONE, application_timezone


CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
SCHEDULED_PUSH_CONVERSATION_KIND = "scheduled_push"
SCHEDULED_PUSH_CONVERSATION_TITLE = "Scheduled Push"


class ScheduledPushEvent(BaseModel):
    title: str
    summary: str
    source_indices: list[int] = Field(default_factory=list)


class ScheduledPushSummary(BaseModel):
    title: str
    lead: str
    key_events: list[ScheduledPushEvent] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ScheduledTaskWorkflow(BaseModel):
    intent_summary: str
    search_queries: list[str] = Field(default_factory=list)
    category_scope: list[str] = Field(default_factory=list)
    source_scope: list[str] = Field(default_factory=list)
    fetch_strategy: dict[str, Any] = Field(default_factory=dict)
    report_style: dict[str, Any] = Field(default_factory=dict)
    delivery: dict[str, Any] = Field(default_factory=dict)


class ScheduledTaskApiParams(BaseModel):
    user_id: str
    task_type: str = "scheduled_push"
    schedule: str
    topics: list[str] = Field(default_factory=list)
    category_scope: list[str] = Field(default_factory=list)
    source_scope: list[str] = Field(default_factory=list)
    output_style: str = "通用专题早报"
    delivery_channel: str = "in_app"
    raw_task_description: str
    parsed_workflow: ScheduledTaskWorkflow


class ScheduledTaskService:
    def __init__(
        self,
        store: NewsStore,
        reports: ReportGenerationService,
        search_service: Any | None = None,
        native_ingestion: Any | None = None,
        llm_client: LLMClient | None = None,
        cc_runtime: Any | None = None,
        prompt_path: Path | None = None,
        task_prompt_path: Path | None = None,
        app_timezone: Any | None = None,
    ):
        self.store = store
        self.reports = reports
        self.search_service = search_service or reports.search_service
        self.native_ingestion = native_ingestion
        self.llm_client = llm_client or LLMClient()
        self.cc_runtime = cc_runtime
        self.prompt_path = prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "scheduled_push_summary.md"
        self.task_prompt_path = task_prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "scheduled_task_parsing.md"
        self.app_timezone = app_timezone or application_timezone(DEFAULT_APP_TIMEZONE)

    def create_task(self, payload: dict) -> dict:
        if "timezone" in payload:
            raise ValueError("task-level timezone is not supported in V1")
        if "task_type" not in payload or "schedule" not in payload:
            raise ValueError("task_type and schedule are required")
        if payload["task_type"] not in {"daily_digest", "weekly_digest", "topic_tracking", "scheduled_push"}:
            raise ValueError("task_type must be daily_digest, weekly_digest, topic_tracking or scheduled_push")
        normalized = {**payload, "schedule": normalize_schedule(payload["schedule"])}
        normalized["delivery_channel"] = normalized.get("delivery_channel") or "in_app"
        normalized["next_run_at"] = next_run_at(normalized["schedule"], app_timezone=self.app_timezone)
        return self.store.create_task(normalized)

    async def prepare_schedule_preview(
        self,
        user_id: str,
        message: str,
        on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        *,
        base: datetime | None = None,
    ) -> dict[str, Any]:
        preview, _ = await self.prepare_schedule_preview_bundle(
            user_id,
            message,
            on_trace=on_trace,
            base=base,
        )
        return preview

    async def prepare_schedule_preview_bundle(
        self,
        user_id: str,
        message: str,
        on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        *,
        base: datetime | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        api_params = await self.extract_schedule_task_api_params(user_id, message, on_trace=on_trace)
        payload = api_params.model_dump(mode="json")
        schedule = normalize_schedule(payload["schedule"])
        payload["schedule"] = schedule
        workflow = payload.get("parsed_workflow") or {}
        report_style = workflow.get("report_style") or {}
        preview = {
            "task_type": payload["task_type"],
            "schedule": schedule,
            "timezone": getattr(self.app_timezone, "key", DEFAULT_APP_TIMEZONE),
            "next_run_at": next_run_at(schedule, base=base, app_timezone=self.app_timezone),
            "topics": payload.get("topics") or [],
            "category_scope": payload.get("category_scope") or [],
            "source_scope": payload.get("source_scope") or [],
            "output_style": payload.get("output_style") or "通用专题早报",
            "delivery_channel": payload.get("delivery_channel") or "in_app",
            "intent_summary": workflow.get("intent_summary") or "",
            "report_sections": report_style.get("sections") or [],
            "raw_task_description": payload.get("raw_task_description") or message,
        }
        return preview, payload

    async def create_from_schedule_message(
        self,
        user_id: str,
        message: str,
        on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []

        async def emit(item: dict[str, Any]) -> None:
            trace.append(item)
            if on_trace:
                await on_trace(item)

        _, payload = await self.prepare_schedule_preview_bundle(user_id, message, on_trace=emit)
        return await self._create_from_schedule_preview(
            payload,
            user_id=user_id,
            original_message=message,
            emit=emit,
            trace=trace,
        )

    async def create_from_schedule_preview(
        self,
        task_payload: dict[str, Any],
        user_id: str,
        original_message: str,
        on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []

        async def emit(item: dict[str, Any]) -> None:
            trace.append(item)
            if on_trace:
                await on_trace(item)

        return await self._create_from_schedule_preview(
            task_payload,
            user_id=user_id,
            original_message=original_message,
            emit=emit,
            trace=trace,
        )

    async def _create_from_schedule_preview(
        self,
        task_payload: dict[str, Any],
        *,
        user_id: str,
        original_message: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        api_params = ScheduledTaskApiParams.model_validate(task_payload)
        if api_params.user_id != user_id:
            raise ValueError("schedule preview user does not match confirmation user")
        payload = api_params.model_dump(mode="json")
        conversation = self.store.get_or_create_conversation(user_id, SCHEDULED_PUSH_CONVERSATION_KIND, SCHEDULED_PUSH_CONVERSATION_TITLE)
        await emit({"stage": "推送对话", "status": "completed", "message": f"将推送到 {conversation['title']}。"})
        task = self.create_task(payload)
        try:
            await emit({"stage": "任务落库", "status": "completed", "message": f"任务 {task['id']} 已保存。"})
        except Exception:
            # Task persistence is the commit point; trace transport must not make a confirmed action retryable.
            pass
        topic = _task_topic(task)
        answer = (
            f"已创建定时推送任务：每天/周期按 `{task['schedule']}` 执行。\n\n"
            f"- 主题：{topic}\n"
            f"- 板块：{', '.join(task.get('category_scope') or []) or '不限'}\n"
            f"- 报告样式：{task.get('output_style') or '通用专题早报'}\n"
            f"- 推送对话：{conversation['title']}\n\n"
            "到点后系统会先抓取/召回相关新闻，再生成专题摘要并追加到这个对话。"
        )
        completion_warnings: list[str] = []
        try:
            turn_id = self.store.save_turn(
                conversation["id"],
                original_message,
                answer,
                [],
                {"type": "scheduled_task", "target_id": task["id"], "text": topic},
                user_id=user_id,
                payload={"type": "scheduled_task_created", "task": task, "api_params": payload},
            )
        except Exception as exc:
            turn_id = None
            completion_warnings.append("history_write_failed")
            try:
                self.store.log(
                    "scheduled_task_history",
                    "degraded",
                    task["id"],
                    {"error_type": type(exc).__name__},
                )
            except Exception:
                pass
            try:
                await emit({"stage": "推送对话", "status": "warning", "message": "任务已创建，但创建记录暂未写入对话历史。"})
            except Exception:
                pass
        return {
            "status": "ok",
            "task": task,
            "conversation": conversation,
            "turn_id": turn_id,
            "answer": answer,
            "api_params": payload,
            "research_trace": trace,
            "completion_warnings": completion_warnings,
        }

    async def extract_schedule_task_api_params(
        self,
        user_id: str,
        message: str,
        on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ScheduledTaskApiParams:
        fallback = _schedule_api_params_from_fallback(user_id, message)
        if self.cc_runtime and getattr(self.cc_runtime, "configured", False):
            started = {
                "stage": "定时任务 Skill",
                "status": "running",
                "message": "正在解析周期、主题、抓取范围和报告样式。",
            }
            if on_trace:
                await on_trace(started)
            try:
                categories = ", ".join(f"{key}={label}" for key, label in CATEGORIES.items())
                result = await self.cc_runtime.run(
                    message=(
                        f"用户ID：{user_id}\n当前日期：{datetime.now(self.app_timezone).date().isoformat()}\n"
                        f"可用板块：{categories}\n原始任务描述：{message}\n"
                        "请输出可直接传给定时任务 API 的 JSON 参数。"
                    ),
                    query="定时资讯任务参数解析",
                    topic=None,
                    category_scope=[],
                    time_range=None,
                    history="",
                    allow_web_search=False,
                    allow_local_search=False,
                    skill_names=[SCHEDULED_NEWS_TASK_SKILL_NAME],
                    strict_json_output=True,
                    max_turns=6,
                    on_trace=on_trace,
                )
                raw = _decode_json_object(result.answer)
                parsed = ScheduledTaskApiParams.model_validate(_coerce_schedule_task_raw(raw))
                normalized = _normalize_task_api_params(parsed, fallback)
                if on_trace:
                    await on_trace(
                        {
                            "stage": "定时任务 Skill",
                            "status": "completed",
                            "message": f"已解析为 cron：{normalized.schedule}",
                        }
                    )
                return normalized
            except Exception as exc:
                self.store.log("scheduled_task_cc_parse", "error", user_id, {"error": str(exc), "message": message})
                if on_trace:
                    await on_trace(
                        {
                            "stage": "定时任务 Skill",
                            "status": "fallback",
                            "message": "Agent 解析失败，已使用本地安全解析规则。",
                        }
                    )
        if not self.llm_client.configured:
            return fallback
        try:
            raw = await self.llm_client.structured(
                _schedule_task_parse_messages(self.task_prompt_path, user_id, message, self.app_timezone),
                "scheduled_task_api_params",
                ScheduledTaskApiParams.model_json_schema(),
            )
            parsed = ScheduledTaskApiParams.model_validate(_coerce_schedule_task_raw(raw))
            return _normalize_task_api_params(parsed, fallback)
        except Exception as exc:
            self.store.log("scheduled_task_parse", "error", user_id, {"error": str(exc), "message": message})
            return fallback

    def list_tasks(self, user_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_tasks(user_id=user_id, limit=limit)

    def set_task_enabled(self, task_id: str, user_id: str = "default", enabled: bool = True) -> dict[str, Any] | None:
        return self.store.set_task_enabled(task_id, user_id, enabled)

    def delete_task(self, task_id: str, user_id: str = "default") -> dict[str, Any] | None:
        return self.store.delete_task(task_id, user_id)

    async def run_task(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError(f"Unknown task_id: {task_id}")
        if task["task_type"] == "scheduled_push":
            return await self._run_scheduled_push(task)
        topic = "、".join(task.get("topics") or task.get("category_scope") or ["每日摘要"])
        report = await self.reports.generate(
            user_id=task["user_id"],
            topic=topic,
            category_scope=task.get("category_scope") or [],
            report_type=task["task_type"],
        )
        next_at = next_run_at(task["schedule_cron"], app_timezone=self.app_timezone)
        self.store.mark_task_run(task_id, next_run_at=next_at)
        notification = self.store.create_notification(
            user_id=task["user_id"],
            title=_notification_title(task),
            body=f"{topic} 已生成新的{_task_type_label(task['task_type'])}。",
            target_type="report",
            target_id=report.report_id,
            delivery_channel=task.get("delivery_channel") or "in_app",
            payload={
                "task_id": task_id,
                "report_id": report.report_id,
                "topic": topic,
                "task_type": task["task_type"],
                "next_run_at": next_at,
            },
        )
        self.store.log("scheduled_task", "ok", task_id, {"report_id": report.report_id, "notification_id": notification["id"]})
        return {"task_id": task_id, "report_id": report.report_id, "notification": notification, "next_run_at": next_at, "status": "ok"}

    async def _run_scheduled_push(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task["id"]
        topic = _task_topic(task)
        category_scope = task.get("category_scope") or []
        source_scope = task.get("source_scope") or []
        ingest_payload: dict[str, Any] | None = None
        ingest_errors: list[dict[str, Any]] = []

        if self.native_ingestion:
            try:
                ingest_payload = await self.native_ingestion.ingest(
                    query=topic,
                    category_scope=category_scope or None,
                    source_scope=source_scope or None,
                    max_results=int((task.get("parsed_workflow") or {}).get("fetch_strategy", {}).get("max_results", 12) or 12),
                    fetch_articles=int((task.get("parsed_workflow") or {}).get("fetch_strategy", {}).get("fetch_articles", 6) or 6),
                    follow_depth=0,
                    follow_limit_per_article=0,
                    max_sources=int((task.get("parsed_workflow") or {}).get("fetch_strategy", {}).get("max_sources", 3) or 3),
                    request_timeout_seconds=6.0,
                )
                ingest_errors = list(ingest_payload.get("errors") or [])
            except Exception as exc:
                ingest_errors = [{"stage": "ingest", "error": str(exc)}]
                self.store.log("scheduled_push_ingest", "error", task_id, {"topic": topic, "error": str(exc)})

        results = await self.search_service.search(
            topic,
            category_scope or None,
            source_scope or None,
            None,
            max_results=12,
            include_remote=False,
        )
        evidence = _scheduled_push_evidence(self.store, results)
        summary = await self._summarize_scheduled_push(task, topic, evidence, ingest_payload, ingest_errors)
        markdown = _scheduled_push_markdown(task, topic, summary, evidence, ingest_payload, ingest_errors)
        report_id = self.store.save_report(
            task["user_id"],
            topic,
            category_scope,
            {
                "topic": topic,
                "report_type": "scheduled_push",
                "task_id": task_id,
                "raw_task_description": task.get("raw_task_description"),
                "parsed_workflow": task.get("parsed_workflow") or {},
                "summary": summary,
                "evidence": evidence,
                "ingest": ingest_payload or {},
                "ingest_errors": ingest_errors,
            },
        )
        conversation = self.store.get_or_create_conversation(task["user_id"], SCHEDULED_PUSH_CONVERSATION_KIND, SCHEDULED_PUSH_CONVERSATION_TITLE)
        turn_id = self.store.save_turn(
            conversation["id"],
            f"[scheduled push] {topic}",
            markdown,
            [item.model_dump(mode="json") for item in results[:8]],
            FocusObject(type="scheduled_push", target_id=task_id, text=topic).model_dump(mode="json"),
            user_id=task["user_id"],
            payload={
                "type": "scheduled_push_result",
                "task_id": task_id,
                "report_id": report_id,
                "topic": topic,
                "raw_task_description": task.get("raw_task_description"),
                "parsed_workflow": task.get("parsed_workflow") or {},
                "summary": summary,
                "evidence": evidence,
                "ingest": ingest_payload or {},
            },
        )
        next_at = next_run_at(task["schedule_cron"], app_timezone=self.app_timezone)
        self.store.mark_task_run(task_id, next_run_at=next_at)
        notification = self.store.create_notification(
            user_id=task["user_id"],
            title=f"{topic} · 定时专题已更新",
            body=summary.get("lead") or f"{topic} 已生成新的定时专题推送。",
            target_type="conversation",
            target_id=conversation["id"],
            delivery_channel=task.get("delivery_channel") or "in_app",
            payload={
                "task_id": task_id,
                "report_id": report_id,
                "conversation_id": conversation["id"],
                "turn_id": turn_id,
                "topic": topic,
                "task_type": task["task_type"],
                "next_run_at": next_at,
            },
        )
        self.store.log(
            "scheduled_push",
            "ok",
            task_id,
            {
                "report_id": report_id,
                "conversation_id": conversation["id"],
                "turn_id": turn_id,
                "evidence_count": len(evidence),
                "ingest_errors": len(ingest_errors),
            },
        )
        return {
            "task_id": task_id,
            "report_id": report_id,
            "conversation_id": conversation["id"],
            "turn_id": turn_id,
            "notification": notification,
            "next_run_at": next_at,
            "status": "ok",
            "summary": summary,
            "evidence_count": len(evidence),
        }

    async def _summarize_scheduled_push(
        self,
        task: dict[str, Any],
        topic: str,
        evidence: list[dict[str, Any]],
        ingest_payload: dict[str, Any] | None,
        ingest_errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.llm_client.configured or not evidence:
            return _fallback_scheduled_push_summary(topic, evidence, bool(self.llm_client.configured))
        messages = [
            {"role": "system", "content": self.prompt_path.read_text(encoding="utf-8")},
            {
                "role": "user",
                "content": (
                    f"用户：{task['user_id']}\n"
                    f"任务ID：{task['id']}\n"
                    f"主题：{topic}\n"
                    f"板块：{', '.join(task.get('category_scope') or []) or '不限'}\n"
                    f"输出风格：{task.get('output_style') or '通用专题早报'}\n"
                    f"解析后的任务流程：{task.get('parsed_workflow') or {}}\n"
                    f"抓取概况：{ingest_payload or {}}\n"
                    f"抓取错误：{ingest_errors[:3]}\n\n"
                    "证据：\n"
                    + "\n\n".join(_evidence_block(item) for item in evidence[:10])
                ),
            },
        ]
        try:
            raw = await self.llm_client.structured(messages, "scheduled_push_summary", ScheduledPushSummary.model_json_schema())
            return ScheduledPushSummary.model_validate(raw).model_dump(mode="json")
        except Exception as exc:
            self.store.log("scheduled_push_summary", "error", task["id"], {"topic": topic, "error": str(exc)})
            return _fallback_scheduled_push_summary(topic, evidence, True)

    async def run_due_tasks(self, user_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        due = []
        for task in self.store.list_tasks(user_id=user_id, enabled_only=True, limit=100):
            due_at = _parse_datetime(task.get("next_run_at"))
            if due_at and due_at <= now:
                due.append(task)
            if len(due) >= limit:
                break
        results = []
        for task in due:
            try:
                results.append(await self.run_task(task["id"]))
            except Exception as exc:
                self.store.log("scheduled_task", "error", task["id"], {"error": str(exc)})
        return {
            "ran_count": len(results),
            "items": results,
            "notifications": [item["notification"] for item in results if item.get("notification")],
        }


def normalize_schedule(value: str) -> str:
    schedule = str(value or "").strip()
    if not schedule:
        raise ValueError("schedule is required")
    if schedule.endswith("m") and schedule[:-1].isdigit():
        minutes = int(schedule[:-1])
        if minutes < 1 or minutes > 1440:
            raise ValueError("schedule minutes must be between 1 and 1440")
        return f"*/{minutes} * * * *"
    if schedule.startswith("daily:"):
        hour, minute = _parse_time(schedule.removeprefix("daily:"))
        return f"{minute} {hour} * * *"
    if schedule.startswith("weekly:"):
        parts = schedule.split(":")
        if len(parts) != 4:
            raise ValueError("weekly schedule must be weekly:<0-6>:HH:MM")
        day = int(parts[1])
        hour = int(parts[2])
        minute = int(parts[3])
        _assert_range(day, 0, 6, "weekday")
        _assert_range(hour, 0, 23, "hour")
        _assert_range(minute, 0, 59, "minute")
        return f"{minute} {hour} * * {day}"
    _parse_cron(schedule)
    return schedule


def next_run_at(schedule: str, base: datetime | None = None, app_timezone: Any | None = None) -> str:
    fields = _parse_cron(schedule)
    app_timezone = app_timezone or application_timezone(DEFAULT_APP_TIMEZONE)
    base_utc = base.astimezone(timezone.utc) if base else datetime.now(timezone.utc)
    candidate = base_utc.astimezone(app_timezone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if _matches_cron(candidate, fields):
            return candidate.astimezone(timezone.utc).isoformat()
        candidate += timedelta(minutes=1)
    raise ValueError("schedule has no matching time within one year")


def _parse_cron(schedule: str) -> list[set[int]]:
    parts = schedule.split()
    if len(parts) != 5:
        raise ValueError("schedule must be a 5-field cron expression")
    return [_parse_cron_field(part, *CRON_RANGES[index]) for index, part in enumerate(parts)]


def _parse_cron_field(value: str, minimum: int, maximum: int) -> set[int]:
    selected: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if part == "*":
            selected.update(range(minimum, maximum + 1))
        elif part.startswith("*/") and part[2:].isdigit():
            step = int(part[2:])
            if step <= 0:
                raise ValueError("schedule step must be positive")
            selected.update(range(minimum, maximum + 1, step))
        elif part.isdigit():
            item = int(part)
            _assert_range(item, minimum, maximum, "schedule field")
            selected.add(item)
        else:
            raise ValueError("unsupported schedule field")
    return selected


def _matches_cron(value: datetime, fields: list[set[int]]) -> bool:
    cron_weekday = (value.weekday() + 1) % 7
    parts = (value.minute, value.hour, value.day, value.month, cron_weekday)
    return all(item in allowed for item, allowed in zip(parts, fields))


def _parse_time(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("time must be HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    _assert_range(hour, 0, 23, "hour")
    _assert_range(minute, 0, 59, "minute")
    return hour, minute


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _assert_range(value: int, minimum: int, maximum: int, name: str) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _task_type_label(value: str) -> str:
    labels = {
        "daily_digest": "每日摘要",
        "weekly_digest": "每周摘要",
        "topic_tracking": "专题跟踪",
        "scheduled_push": "定时专题推送",
    }
    return labels.get(value, value)


def _notification_title(task: dict[str, Any]) -> str:
    topics = task.get("topics") or []
    topic = "、".join(topics) if topics else "资讯任务"
    return f"{topic} · {_task_type_label(task['task_type'])}更新"


def parse_schedule_command(message: str) -> dict[str, Any]:
    text = str(message or "").strip()
    if text.startswith("/schedule"):
        text = text[len("/schedule") :].strip()
    if not text:
        raise ValueError("schedule command requires a topic")
    schedule = _schedule_from_text(text)
    topic = _topic_from_schedule_text(text)
    categories = infer_categories(text) or []
    return {
        "topic": topic,
        "schedule": schedule,
        "category_scope": categories,
        "output_style": "通用专题早报",
        "raw_text": message,
        "parsed_workflow": _default_parsed_workflow(topic, categories),
    }


def _schedule_api_params_from_fallback(user_id: str, message: str) -> ScheduledTaskApiParams:
    parsed = parse_schedule_command(message)
    topic = parsed["topic"]
    workflow = ScheduledTaskWorkflow.model_validate(parsed["parsed_workflow"])
    return ScheduledTaskApiParams(
        user_id=user_id,
        task_type="scheduled_push",
        schedule=parsed["schedule"],
        topics=[topic],
        category_scope=parsed["category_scope"],
        source_scope=[],
        output_style=parsed["output_style"],
        delivery_channel="in_app",
        raw_task_description=message,
        parsed_workflow=workflow,
    )


def _schedule_task_parse_messages(prompt_path: Path, user_id: str, message: str, app_timezone: Any) -> list[dict[str, str]]:
    categories = "\n".join(f"- {key}: {label}" for key, label in CATEGORIES.items())
    user = (
        f"用户ID：{user_id}\n"
        f"当前日期：{datetime.now(app_timezone).date().isoformat()}\n"
        f"可用板块：\n{categories}\n\n"
        f"原始任务描述：{message}\n\n"
        "请输出可直接传给 POST /api/tasks 的标准参数。"
    )
    return [{"role": "system", "content": prompt_path.read_text(encoding="utf-8")}, {"role": "user", "content": user}]


def _normalize_task_api_params(parsed: ScheduledTaskApiParams, fallback: ScheduledTaskApiParams) -> ScheduledTaskApiParams:
    schedule = parsed.schedule
    try:
        schedule = normalize_schedule(schedule)
    except ValueError:
        schedule = fallback.schedule
    categories = _valid_categories(parsed.category_scope) or fallback.category_scope
    source_scope = [str(item).strip() for item in parsed.source_scope if str(item).strip()][:20]
    topics = [str(item).strip() for item in parsed.topics if str(item).strip()][:5] or fallback.topics
    output_style = _explicit_style_from_text(fallback.raw_task_description) or parsed.output_style or fallback.output_style
    workflow = parsed.parsed_workflow.model_dump(mode="json")
    workflow["category_scope"] = _valid_categories(workflow.get("category_scope") or categories) or categories
    workflow["source_scope"] = source_scope
    workflow["search_queries"] = _clean_string_list(workflow.get("search_queries") or topics, limit=8)
    workflow["fetch_strategy"] = _normalized_fetch_strategy(workflow.get("fetch_strategy") or {})
    workflow["report_style"] = _normalized_report_style(workflow.get("report_style") or {}, output_style)
    workflow["report_style"]["name"] = output_style[:80]
    workflow["delivery"] = workflow.get("delivery") or {"target": SCHEDULED_PUSH_CONVERSATION_TITLE, "channel": "in_app"}
    return ScheduledTaskApiParams(
        user_id=fallback.user_id,
        task_type="scheduled_push",
        schedule=schedule,
        topics=topics,
        category_scope=categories,
        source_scope=source_scope,
        output_style=output_style[:80],
        delivery_channel=parsed.delivery_channel if parsed.delivery_channel in {"in_app", "browser"} else "in_app",
        raw_task_description=fallback.raw_task_description,
        parsed_workflow=ScheduledTaskWorkflow.model_validate(workflow),
    )


def _coerce_schedule_task_raw(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw or {})
    for key in ("topics", "category_scope", "source_scope"):
        data[key] = _coerce_list(data.get(key))
    workflow = dict(data.get("parsed_workflow") or {})
    for key in ("search_queries", "category_scope", "source_scope"):
        workflow[key] = _coerce_list(workflow.get(key))
    report_style = dict(workflow.get("report_style") or {})
    report_style["sections"] = _coerce_list(report_style.get("sections"))
    workflow["report_style"] = report_style
    data["parsed_workflow"] = workflow
    return data


def _decode_json_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw or ""):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("scheduled-task agent did not return a JSON object")


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        return [item.strip() for item in re.split(r"[,，、]", stripped) if item.strip()]
    return [value]


def _valid_categories(values: list[str]) -> list[str]:
    seen: set[str] = set()
    categories = []
    for value in values or []:
        key = str(value).strip()
        if key in CATEGORIES and key not in seen:
            categories.append(key)
            seen.add(key)
    return categories


def _clean_string_list(values: list[Any], limit: int) -> list[str]:
    items = []
    seen: set[str] = set()
    for value in values or []:
        item = " ".join(str(value).split())[:120]
        if item and item not in seen:
            items.append(item)
            seen.add(item)
        if len(items) >= limit:
            break
    return items


def _normalized_fetch_strategy(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": str(value.get("mode") or "search_then_fetch"),
        "max_results": _bounded_int(value.get("max_results"), 12, 1, 30),
        "fetch_articles": _bounded_int(value.get("fetch_articles"), 6, 0, 20),
        "max_sources": _bounded_int(value.get("max_sources"), 3, 1, 10),
        "time_range_days": _bounded_int(value.get("time_range_days"), 3, 1, 30),
    }


def _normalized_report_style(value: dict[str, Any], output_style: str) -> dict[str, Any]:
    sections = _clean_string_list(
        value.get("sections")
        or [
            "一句话导语",
            "核心事件",
            "背景脉络",
            "影响与观察",
            "来源与不确定性",
        ],
        limit=8,
    )
    return {
        "name": str(value.get("name") or output_style or "通用专题早报")[:80],
        "tone": str(value.get("tone") or "简洁、事实优先、结构化")[:80],
        "sections": sections,
        "length": str(value.get("length") or "600-900字")[:40],
    }


def _explicit_style_from_text(text: str) -> str | None:
    cleaned = str(text or "").replace("/schedule", " ")
    matches = re.findall(r"([\u4e00-\u9fffA-Za-z0-9]{0,8}(?:早报|简报|专题|专栏|报告))", cleaned)
    if not matches:
        return None
    style = re.sub(r"^(总结成一个|结成一个|成一个|一个|按|生成|总结成|做成|写成)", "", matches[-1].strip())
    if style in {"早报", "简报", "专题", "专栏", "报告"}:
        return None
    return style[:80] if style else None


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _default_parsed_workflow(topic: str, categories: list[str]) -> dict[str, Any]:
    return {
        "intent_summary": f"按计划收集「{topic}」相关新闻，并生成一篇结构化专题早报。",
        "search_queries": [topic],
        "category_scope": categories,
        "source_scope": [],
        "fetch_strategy": _normalized_fetch_strategy({}),
        "report_style": _normalized_report_style({}, "通用专题早报"),
        "delivery": {"target": SCHEDULED_PUSH_CONVERSATION_TITLE, "channel": "in_app"},
    }


def _schedule_from_text(text: str) -> str:
    lowered = text.lower()
    time_match = re.search(r"(上午|早晨|早上|中午|下午|晚上)?\s*(\d{1,2})\s*[点:：]\s*(\d{1,2})?", text)
    hour = 9
    minute = 0
    if time_match:
        period = time_match.group(1) or ""
        hour = int(time_match.group(2))
        minute = int(time_match.group(3) or 0)
        if period in {"下午", "晚上"} and hour < 12:
            hour += 12
        if period == "中午" and hour < 11:
            hour += 12
    if "每周" in text or "周一" in text or "星期一" in text:
        weekday = _weekday_from_text(text)
        return f"{minute} {hour} * * {weekday}"
    if "daily:" in lowered or "weekly:" in lowered:
        return text.split()[0]
    cron_match = re.search(r"(\S+\s+\S+\s+\S+\s+\S+\s+\S+)", text)
    if cron_match:
        candidate = cron_match.group(1)
        try:
            _parse_cron(candidate)
            return candidate
        except ValueError:
            pass
    return f"{minute} {hour} * * *"


def _weekday_from_text(text: str) -> int:
    mapping = {
        "周日": 0,
        "星期日": 0,
        "周天": 0,
        "星期天": 0,
        "周一": 1,
        "星期一": 1,
        "周二": 2,
        "星期二": 2,
        "周三": 3,
        "星期三": 3,
        "周四": 4,
        "星期四": 4,
        "周五": 5,
        "星期五": 5,
        "周六": 6,
        "星期六": 6,
    }
    for key, value in mapping.items():
        if key in text:
            return value
    return 1


def _topic_from_schedule_text(text: str) -> str:
    patterns = [
        r"关于(.+?)的新闻",
        r"关于(.+?)，",
        r"关于(.+?)并",
        r"收集(.+?)的新闻",
        r"跟踪(.+?)的新闻",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _clean_topic(match.group(1))
    cleaned = re.sub(r"^(帮我|请|定时|每天|每日|每周|早晨|早上|上午|下午|晚上|\d{1,2}点|\d{1,2}:\d{1,2})+", "", text)
    cleaned = (
        cleaned.replace("收集", "")
        .replace("推送", "")
        .replace("总结成一个专题发给我", "")
        .replace("并总结成一个专题发给我", "")
    )
    return _clean_topic(cleaned)


def _clean_topic(value: str) -> str:
    topic = re.sub(r"(新闻|资讯|热点|并总结成一个专题发给我|总结成一个专题发给我|发给我|给我)", "", value).strip(" ，,。")
    if not topic:
        raise ValueError("schedule command topic is empty")
    return topic[:80]


def _task_topic(task: dict[str, Any]) -> str:
    topics = task.get("topics") or []
    if topics:
        return "、".join(str(item) for item in topics if str(item).strip())
    categories = task.get("category_scope") or []
    return "、".join(categories) if categories else "通用资讯"


def _scheduled_push_evidence(store: NewsStore, results: list[SearchResult]) -> list[dict[str, Any]]:
    evidence = []
    for index, item in enumerate(results[:12], start=1):
        row = store.get_article(item.article_id) if item.article_id else None
        content = (row or {}).get("content") or item.summary or ""
        evidence.append(
            {
                "index": index,
                "article_id": item.article_id,
                "source_id": item.source_id,
                "title": item.title,
                "url": item.url,
                "category": item.category,
                "published_at": item.published_at.isoformat() if hasattr(item.published_at, "isoformat") else item.published_at,
                "summary": item.summary or summarize(content, 180),
                "content_excerpt": content[:800],
                "origin": item.origin,
                "score": item.score,
            }
        )
    return evidence


def _evidence_block(item: dict[str, Any]) -> str:
    return (
        f"[{item['index']}] {item.get('title')}\n"
        f"来源：{item.get('source_id')}｜板块：{item.get('category')}｜时间：{item.get('published_at') or 'unknown'}\n"
        f"摘要：{item.get('summary') or ''}\n"
        f"正文片段：{item.get('content_excerpt') or ''}\n"
        f"链接：{item.get('url') or ''}"
    )


def _fallback_scheduled_push_summary(topic: str, evidence: list[dict[str, Any]], llm_was_configured: bool) -> dict[str, Any]:
    if not evidence:
        reason = "没有召回到可用证据" if llm_was_configured else "LLM 未配置且没有召回到可用证据"
        return {
            "title": f"{topic} 定时专题",
            "lead": f"本次围绕「{topic}」{reason}。",
            "key_events": [],
            "source_notes": [],
            "open_questions": ["扩大来源范围", "等待下一轮抓取", "补充更明确的关键词"],
            "confidence": 0.2,
        }
    return {
        "title": f"{topic} 定时专题",
        "lead": f"本次围绕「{topic}」召回 {len(evidence)} 条证据，先给出本地摘要。",
        "key_events": [
            {
                "title": item["title"],
                "summary": item.get("summary") or item.get("content_excerpt", "")[:160],
                "source_indices": [item["index"]],
            }
            for item in evidence[:5]
        ],
        "source_notes": [f"{item['index']}. {item['source_id']}：{item['title']}" for item in evidence[:6]],
        "open_questions": ["后续是否有多源确认", "是否出现新的关键主体", "是否需要扩展到相邻主题"],
        "confidence": 0.55,
    }


def _scheduled_push_markdown(
    task: dict[str, Any],
    topic: str,
    summary: dict[str, Any],
    evidence: list[dict[str, Any]],
    ingest_payload: dict[str, Any] | None,
    ingest_errors: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {summary.get('title') or topic}",
        "",
        summary.get("lead") or "",
        "",
        "## 核心事件",
    ]
    events = summary.get("key_events") or []
    if events:
        for item in events:
            sources = item.get("source_indices") or []
            suffix = f"（证据 {', '.join(str(index) for index in sources)}）" if sources else ""
            lines.append(f"- {item.get('title')}: {item.get('summary')}{suffix}")
    else:
        lines.append("- 暂无足够证据形成核心事件。")
    if summary.get("open_questions"):
        lines.extend(["", "## 后续观察", *[f"- {item}" for item in summary["open_questions"][:5]]])
    lines.extend(
        [
            "",
            "## 来源",
            *[f"- [{item['index']}] {item['title']}（{item['source_id']}）{item.get('url') or ''}" for item in evidence[:8]],
            "",
            f"任务：{task['id']}；抓取：发现 {(ingest_payload or {}).get('discovered_count', 0)}，正文 {(ingest_payload or {}).get('fetched_count', 0)}；错误 {len(ingest_errors)}。",
        ]
    )
    return "\n".join(lines)

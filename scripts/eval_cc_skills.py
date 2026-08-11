#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any
from uuid import uuid4

from personal_news_agent.config import Settings
from personal_news_agent.services.factory import build_services


CASES = (
    "general",
    "knowledge",
    "research",
    "related",
    "factcheck",
    "map",
    "brief",
    "report",
    "sources",
    "schedule",
)
OPTIONAL_CASES = (
    "weather",
    "route",
    "entertainment",
    "entertainment_factcheck",
    "entertainment_map",
)


def _copy_database(source: Path, target: Path) -> None:
    with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
        source_db.backup(target_db)


def _checks(name: str, response: dict[str, Any]) -> list[dict[str, Any]]:
    answer = str(response.get("answer") or "")
    trace = response.get("research_trace") or []
    skill = (response.get("skill_result") or {}).get("command")
    checks = [
        {"name": "has_answer", "passed": len(answer.strip()) >= 20},
        {"name": "has_public_trace", "passed": bool(trace)},
        {
            "name": "no_storage_leak",
            "passed": not any(token in json.dumps(trace, ensure_ascii=False).lower() for token in ("elasticsearch", "sqlite", "mysql", "数据库")),
        },
    ]
    if name in {"general", "knowledge", "weather", "route"}:
        checks.append({"name": "no_business_skill", "passed": skill is None and response.get("context_relation") == "general_conversation_cc_runtime"})
    elif name in {"research", "entertainment"}:
        checks.append({"name": "news_research_route", "passed": response.get("context_relation") == "research_pipeline_cc_runtime"})
    else:
        expected_skill = {
            "entertainment_factcheck": "/factcheck",
            "entertainment_map": "/map",
        }.get(name, f"/{name}")
        checks.append({"name": "expected_skill", "passed": skill == expected_skill})
    if name == "related":
        checks.append({"name": "keeps_person_focus", "passed": "许仰天" in answer})
        path = response.get("mind_map") or {}
        steps = path.get("steps") or []
        checks.extend(
            [
                {"name": "uses_research_path_v2", "passed": path.get("type") == "related_research_path_v2"},
                {
                    "name": "shows_real_search_step",
                    "passed": any(step.get("kind") in {"local_search", "web_search"} for step in steps),
                },
                {
                    "name": "avoids_generic_thought_template",
                    "passed": not any(
                        step.get("label") == "联想依据" or step.get("title") in {"政策 规则 监管", "相似案例"}
                        for step in steps
                    ),
                },
            ]
        )
    if name in {"factcheck", "entertainment_factcheck"}:
        verdict = ((response.get("skill_result") or {}).get("data") or {}).get("verdict")
        checks.append({"name": "conservative_verdict", "passed": verdict in {"supported", "contradicted", "mixed", "insufficient"}, "value": verdict})
    if name in {"map", "entertainment_map"}:
        checks.append({"name": "has_mermaid", "passed": "```mermaid" in answer})
        checks.append(
            {
                "name": "no_transposed_edge_label",
                "passed": re.search(r'\|"[^"\n|]+\|"\s+[A-Za-z_]', answer) is None,
            }
        )
    if name in {"brief", "report"}:
        source = ((response.get("skill_result") or {}).get("data") or {}).get("agent_source")
        checks.append({"name": "cc_generated", "passed": source == "cc_runtime", "value": source})
    if name == "schedule":
        task = ((response.get("skill_result") or {}).get("data") or {}).get("task") or {}
        checks.append({"name": "weekly_cron", "passed": task.get("schedule") == "0 8 * * 1", "value": task.get("schedule")})
    return checks


async def _run_case(chat: Any, name: str, conversation_id: str, user_id: str) -> dict[str, Any]:
    requests = {
        "general": "你好，你是谁？",
        "knowledge": "什么是向量数据库，它和普通关键词搜索有什么区别？",
        "research": "希音 IPO 估值最近有什么变化？",
        "related": "/related 许仰天",
        "factcheck": "/factcheck 希音已于2026年8月10日按300亿美元估值完成IPO",
        "map": "/map 希音IPO估值变化 --category economy",
        "brief": "/brief 希音IPO估值变化 --category economy",
        "report": "/report 希音IPO估值变化 --category economy --time-range 30d",
        "sources": "/sources tech",
        "schedule": "/schedule 每周一上午8点汇总周末AI芯片热点并生成专题报告",
        "weather": "今天上海天气怎么样，出门需要带伞吗？",
        "route": "从上海虹桥火车站到杭州西湖景区怎么走？请比较高铁和自驾。",
        "entertainment": "电影《功夫女足》最近的票房和口碑有什么变化？请区分已确认数据与评价。",
        "entertainment_factcheck": "/factcheck 《功夫女足》票房已超过17亿元，并且现在仍占2026暑期档总票房34%",
        "entertainment_map": "/map 电影《功夫女足》票房与口碑变化 --category entertainment",
    }
    message = requests[name]
    response = await chat.chat(
        conversation_id,
        message,
        topic="希音IPO估值变化" if name == "related" else None,
        category_scope=(
            ["economy"]
            if name in {"research", "related", "factcheck", "map", "brief", "report"}
            else ["entertainment"]
            if name.startswith("entertainment")
            else None
        ),
        use_llm=not message.startswith("/"),
        user_id=user_id,
        allow_web_search=True,
    )
    payload = response.model_dump(mode="json")
    checks = _checks(name, payload)
    return {
        "case": name,
        "request": message,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "context_relation": payload.get("context_relation"),
        "trace": payload.get("research_trace") or [],
        "answer": str(payload.get("answer") or "")[:4_000],
        "mind_map": payload.get("mind_map"),
        "skill": (payload.get("skill_result") or {}).get("command"),
        "task_id": ((((payload.get("skill_result") or {}).get("data") or {}).get("task") or {}).get("id")),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run real CC + Skill regression cases against a temporary copy of local news data.")
    parser.add_argument("--case", action="append", choices=(*CASES, *OPTIONAL_CASES), help="Run only selected cases; may be repeated.")
    parser.add_argument("--output", type=Path, help="Optional JSON result path.")
    args = parser.parse_args()
    selected = tuple(args.case or CASES)
    settings = Settings()
    with tempfile.TemporaryDirectory(prefix="pna-cc-eval-") as temp_dir:
        database = Path(temp_dir) / "eval.db"
        _copy_database(Path(settings.sqlite_path), database)
        services = build_services(replace(settings, database_url=f"sqlite:///{database}"))
        runtime = services["cc_runtime"]
        if not getattr(runtime, "configured", False):
            raise SystemExit("CC Runtime is not configured")
        results = []
        eval_id = uuid4().hex[:10]
        conversation_id = f"cc_skill_eval_{eval_id}"
        user_id = f"cc_skill_eval_{eval_id}"
        for name in selected:
            print(json.dumps({"event": "case_started", "case": name}, ensure_ascii=False), flush=True)
            try:
                result = await _run_case(services["chat"], name, conversation_id, user_id)
            except Exception as exc:
                result = {
                    "case": name,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            print(
                json.dumps(
                    {
                        "event": "case_finished",
                        "case": name,
                        "passed": result.get("passed", False),
                        "context_relation": result.get("context_relation"),
                        "checks": result.get("checks", []),
                        "error": result.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        summary = {
            "passed": all(item.get("passed") for item in results),
            "passed_count": sum(bool(item.get("passed")) for item in results),
            "case_count": len(results),
            "results": results,
        }
        if args.output:
            args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"event": "summary", **{key: summary[key] for key in ("passed", "passed_count", "case_count")}}, ensure_ascii=False))
        return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

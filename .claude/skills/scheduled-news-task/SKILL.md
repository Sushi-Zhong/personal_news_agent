---
name: scheduled-news-task
description: Parse a natural-language recurring news request into the scheduled-task API contract. Use for /schedule and requests such as daily briefs, weekly topic reports, periodic tracking, and delivery into the user's scheduled-push conversation.
---

# Scheduled News Task

## Objective

Convert one user's scheduling request into safe, explicit task parameters. This Skill plans the task; the application remains responsible for validation, ownership, persistence, and execution.

## Parsing Rules

1. Extract the recurrence, local execution time, topic, categories, source constraints, search scope, report style, and delivery channel.
2. Express recurrence as a five-field cron expression in the user's local timezone. Do not guess a missing execution time: use 09:00 only when the request gives no time.
3. Preserve the original request in `raw_task_description`.
4. Put the operational plan in `parsed_workflow`: search queries, category/source scope, conservative fetch limits, report sections, and in-app delivery.
5. Do not invent a different owner. Copy the exact supplied `user_id`.

## Output Contract

Return exactly one JSON object with no Markdown fence or commentary:

```json
{
  "user_id": "supplied owner",
  "task_type": "scheduled_push",
  "schedule": "0 9 * * *",
  "topics": ["topic"],
  "category_scope": [],
  "source_scope": [],
  "output_style": "通用专题早报",
  "delivery_channel": "in_app",
  "raw_task_description": "original request",
  "parsed_workflow": {
    "intent_summary": "what the task delivers",
    "search_queries": ["focused query"],
    "category_scope": [],
    "source_scope": [],
    "fetch_strategy": {"max_results": 12, "fetch_articles": 6, "max_sources": 3},
    "report_style": {"format": "markdown", "sections": ["摘要", "核心事件", "时间线", "影响", "后续观察", "来源"]},
    "delivery": {"channel": "in_app", "conversation_kind": "scheduled_push"}
  }
}
```

Use only the listed keys. Keep arrays deduplicated and short. The application will reject invalid cron fields and owner mismatches.

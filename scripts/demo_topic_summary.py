from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_news_agent.config import settings
from personal_news_agent.services.reports import ReportGenerationService
from personal_news_agent.services.search import UnifiedSearchService
from personal_news_agent.services.source_registry import SourceRegistryService
from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.topic_summary import TopicSummaryService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run an end-to-end topic summary example from source registry to Markdown output.")
    parser.add_argument("--topic", default="新能源汽车价格战")
    parser.add_argument("--category", action="append", default=None)
    parser.add_argument("--max-articles", type=int, default=8)
    parser.add_argument("--use-current-db", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    registry = SourceRegistryService(settings.sources_path)
    registry.load()
    db_path = settings.sqlite_path if args.use_current_db else Path(tempfile.mkdtemp(prefix="pna_topic_summary_demo_")) / "news.db"
    store = NewsStore(db_path)
    store.init()
    store.upsert_sources(registry.all_sources())
    if not args.use_current_db:
        store.seed_demo_articles()
    search = UnifiedSearchService(store, registry)
    # Instantiate reports to exercise the same DB/report path used by the API stack.
    ReportGenerationService(store, search)
    service = TopicSummaryService(store, search)
    payload = await service.generate(
        user_id="demo",
        topic=args.topic,
        category_scope=args.category or ["auto", "economy"],
        max_articles=args.max_articles,
        use_llm=not args.no_llm,
        output_style="结构化专题摘要",
        save_report=True,
    )
    payload["demo_db_path"] = str(db_path)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(payload["markdown"])
        print("\n---")
        print(json.dumps({"report_id": payload["report_id"], "source_count": payload["source_count"], "demo_db_path": str(db_path)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

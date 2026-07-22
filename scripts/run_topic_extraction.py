from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_news_agent.config import settings
from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.topic_extraction import TopicExtractionService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and merge topics from unprocessed news articles.")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    store = NewsStore(settings.sqlite_path)
    store.init()
    result = await TopicExtractionService(store).process_pending(limit=max(1, args.limit))
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())

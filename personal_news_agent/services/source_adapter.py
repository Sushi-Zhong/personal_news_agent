from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup

from personal_news_agent.config import settings
from personal_news_agent.core.models import NormalizedArticle, RawArticle, RawArticleLink, RawSearchResult, SectionConfig, SourceConfig
from personal_news_agent.core.text import content_hash, extract_entities, extract_keywords, stable_id, summarize

from personal_news_agent.services.article_fetch import ArticleFetchService, _host_allowed, _parse_published_datetime, canonicalize_url



class ListPageAdapter:
    def __init__(self, source: SourceConfig, fetcher: ArticleFetchService | None = None):
        self.source = source
        self.fetcher = fetcher or ArticleFetchService()
        self.source_id = source.source_id

    def list_sections(self) -> list[SectionConfig]:
        return list(self.source.sections)

    async def crawl_section(self, section_key: str, limit: int = 30) -> list[RawArticleLink]:
        section = self._section(section_key)
        if not section.crawl_enabled:
            return []
        allowed_domains = list(self.source.search.domain_filters or (self.source.root_domain,))
        if section.crawl_strategy == "rss_feed":
            return await self.fetcher.list_rss_links(self.source.source_id, section.key, section.url, limit, allowed_domains)
        return await self.fetcher.list_links(self.source.source_id, section.key, section.url, limit, allowed_domains)

    async def search(self, query: str, limit: int = 10) -> list[RawSearchResult]:
        if not self.source.search.native_search_enabled:
            return []
        results: list[RawSearchResult] = []
        for template in self.source.search.candidate_templates[:2]:
            url = template.format(query=query, query_encoded=quote(query))
            try:
                allowed_domains = list(self.source.search.domain_filters or (self.source.root_domain,))
                links = await self.fetcher.list_links(self.source.source_id, self.source.sections[0].key, url, limit, allowed_domains)
            except Exception:
                continue
            results.extend(RawSearchResult(source_id=link.source_id, title=link.title, url=link.url) for link in links)
            if len(results) >= limit:
                break
        if len(results) < limit:
            for request in self.source.search.api_requests[:2]:
                try:
                    results.extend(await self._search_api(request, query, limit - len(results)))
                except Exception:
                    continue
                if len(results) >= limit:
                    break
        return results[:limit]

    async def fetch_article(self, url: str) -> RawArticle:
        allowed_domains = list(self.source.search.domain_filters or (self.source.root_domain,))
        canonical_url = canonicalize_url(url)
        if isinstance(self.fetcher, ArticleFetchService):
            return await self.fetcher.fetch_article(self.source.source_id, canonical_url, allowed_domains)
        return await self.fetcher.fetch_article(self.source.source_id, canonical_url)

    def normalize_article(self, raw: RawArticle, section_key: str | None = None, category: str | None = None) -> NormalizedArticle:
        canonical_url = canonicalize_url(raw.url)
        text = f"{raw.title}\n{raw.summary}\n{raw.content}"
        category_value = category or (self.source.sections[0].category if self.source.sections else self.source.categories[0])
        return NormalizedArticle(
            id=stable_id("art", canonical_url),
            source_id=self.source.source_id,
            section_key=section_key,
            url=canonical_url,
            title=raw.title,
            summary=raw.summary or summarize(raw.content),
            content=raw.content,
            category=category_value,
            published_at=raw.published_at,
            fetched_at=datetime.now(timezone.utc),
            source_priority=self.source.priority,
            keywords=extract_keywords(text),
            entities=extract_entities(text),
            content_hash=content_hash(raw.content),
        )

    def _section(self, section_key: str) -> SectionConfig:
        for section in self.source.sections:
            if section.key == section_key:
                return section
        raise ValueError(f"Unknown section {self.source.source_id}/{section_key}")

    async def _search_api(self, request: dict[str, Any], query: str, limit: int) -> list[RawSearchResult]:
        method = str(request.get("method", "GET")).upper()
        url = str(request["url"]).format(query=query, query_encoded=quote(query), limit=limit)
        json_payload = _format_value(request.get("json"), query, limit) if request.get("json") is not None else None
        async with httpx.AsyncClient(
            timeout=self.fetcher.timeout,
            follow_redirects=True,
            verify=settings.http_verify_ssl,
            headers={"User-Agent": "Mozilla/5.0 personal-news-agent/0.1", "Accept": "application/json,*/*"},
        ) as client:
            response = await client.request(method, url, json=json_payload)
            response.raise_for_status()
            payload = response.json()
        records = _extract_path(payload, str(request.get("results_path", ""))) if request.get("results_path") else []
        if not isinstance(records, list):
            return []
        title_field = str(request.get("title_field", "title"))
        url_field = str(request.get("url_field", "url"))
        snippet_field = str(request.get("snippet_field", "content"))
        results: list[RawSearchResult] = []
        allowed_domains = list(self.source.search.domain_filters or (self.source.root_domain,))
        for record in records:
            if not isinstance(record, dict):
                continue
            url_value = _record_value(record, url_field)
            if not url_value:
                continue
            url_value = str(url_value)
            parsed = urlparse(url_value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if allowed_domains and not any(_host_allowed(parsed.netloc, domain) for domain in allowed_domains):
                continue
            title_value = _record_value(record, title_field) or url_value
            snippet_value = _record_value(record, snippet_field) or ""
            results.append(
                RawSearchResult(
                    source_id=self.source.source_id,
                    title=_strip_html(str(title_value)),
                    url=url_value,
                    snippet=_strip_html(str(snippet_value))[:240],
                    published_at=_parse_search_record_datetime(record, request),
                )
            )
            if len(results) >= limit:
                break
        return results


def _format_value(value: Any, query: str, limit: int) -> Any:
    if isinstance(value, dict):
        return {key: _format_value(item, query, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_format_value(item, query, limit) for item in value]
    if isinstance(value, str):
        formatted = value.format(query=query, query_encoded=quote(query), limit=limit)
        return limit if formatted == str(limit) and value == "{limit}" else formatted
    return value


def _extract_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if not part:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _record_value(record: dict[str, Any], field: str) -> Any:
    return _extract_path(record, field) if "." in field else record.get(field)


def _parse_search_record_datetime(record: dict[str, Any], request: dict[str, Any]) -> datetime | None:
    fields = [
        str(request.get("date_field") or ""),
        "published_at",
        "publishedAt",
        "published_date",
        "publish_date",
        "pubDate",
        "datePublished",
        "date",
        "time",
        "created_at",
    ]
    for field in fields:
        if not field:
            continue
        value = _record_value(record, field)
        parsed = _parse_published_datetime(str(value or ""))
        if parsed:
            return parsed
    return None


def _strip_html(value: str) -> str:
    return " ".join(BeautifulSoup(value, "html.parser").get_text(" ", strip=True).split())

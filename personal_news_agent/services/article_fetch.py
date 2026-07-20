from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from personal_news_agent.config import settings
from personal_news_agent.core.models import RawArticle, RawArticleLink


class ArticleFetchService:
    def __init__(self, timeout: float = 12.0, verify_ssl: bool | None = None):
        self.timeout = timeout
        self.verify_ssl = settings.http_verify_ssl if verify_ssl is None else verify_ssl

    async def list_links(self, source_id: str, section_key: str, url: str, limit: int = 30, allowed_domains: list[str] | None = None) -> list[RawArticleLink]:
        html = await self._get_text(url)
        soup = BeautifulSoup(html, "html.parser")
        links: list[RawArticleLink] = []
        seen: set[str] = set()
        root_domain = urlparse(url).netloc
        domains = allowed_domains or [root_domain]
        for anchor in soup.find_all("a", href=True):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            href = _unwrap_search_link(urljoin(url, anchor["href"]))
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if domains and not any(_host_allowed(parsed.netloc, domain) for domain in domains):
                continue
            if not _looks_like_article_url(href):
                continue
            if len(title) < 6 or href in seen:
                continue
            seen.add(href)
            links.append(RawArticleLink(source_id=source_id, section_key=section_key, url=href, title=title))
            if len(links) >= limit:
                break
        return links

    async def list_rss_links(self, source_id: str, section_key: str, url: str, limit: int = 30, allowed_domains: list[str] | None = None) -> list[RawArticleLink]:
        xml = await self._get_text(url)
        links: list[RawArticleLink] = []
        seen: set[str] = set()
        for title, href, published_text in _rss_items(xml):
            if not title or not href:
                continue
            href = _unwrap_search_link(urljoin(url, href))
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if allowed_domains and not any(_host_allowed(parsed.netloc, domain) for domain in allowed_domains):
                continue
            if href in seen:
                continue
            seen.add(href)
            published_at = _parse_published_datetime(published_text)
            links.append(RawArticleLink(source_id=source_id, section_key=section_key, url=href, title=title, published_at=published_at))
            if len(links) >= limit:
                break
        return links

    async def fetch_article(self, source_id: str, url: str) -> RawArticle:
        html = await self._get_text(url)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = str(og_title["content"]).strip()
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        content = "\n".join(p for p in paragraphs if len(p) >= 12)
        if not content:
            content = soup.get_text(" ", strip=True)
        summary = ""
        desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        if desc and desc.get("content"):
            summary = str(desc["content"]).strip()
        return RawArticle(source_id=source_id, url=url, title=title or url, content=content, summary=summary, published_at=_extract_published_at(soup))

    async def _get_text(self, url: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 personal-news-agent/0.1",
            "Accept": "text/html,application/xhtml+xml",
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers, verify=self.verify_ssl) as client:
            response = await client.get(url)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            return response.text


def _host_allowed(host: str, domain: str) -> bool:
    normalized = domain.split(":", 1)[0].removeprefix("www.")
    candidate = host.split(":", 1)[0].removeprefix("www.")
    return candidate == normalized or candidate.endswith("." + normalized)


def _looks_like_article_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if not path or path in {"/", "/index.html", "/index.shtml"}:
        return False
    if any(marker in path for marker in ["/search", "/tag", "/tags", "/video", "/photo", "/special", "/zt/", "/column"]):
        return False
    article_markers = [".html", ".shtml", ".htm", "/a/", "/n1/", "/c/", "/article/", "/news/", "/20"]
    return any(marker in path for marker in article_markers)


def _unwrap_search_link(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    for key in ("targetpage", "target", "url"):
        values = params.get(key)
        if values and values[0].startswith(("http://", "https://")):
            return unquote(values[0])
    return url


def _rss_item_url(item) -> str:
    link_tag = item.find("link")
    if link_tag:
        href = link_tag.get("href") or link_tag.get_text(" ", strip=True)
        if not href and isinstance(link_tag.next_sibling, str):
            href = link_tag.next_sibling.strip()
        if href:
            return str(href).strip()
    guid = item.find("guid")
    return guid.get_text(" ", strip=True) if guid else ""


def _rss_items(raw: str) -> list[tuple[str, str, str]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return _rss_items_from_html_parser(raw)
    items: list[tuple[str, str, str]] = []
    for element in root.iter():
        if _xml_tag_name(element.tag) not in {"item", "entry"}:
            continue
        title = " ".join(_xml_child_text(element, "title").split())
        href = _xml_item_url(element)
        published = _xml_child_text(element, "pubDate", "published", "updated", "date")
        items.append((title, href, published))
    return items


def _rss_items_from_html_parser(raw: str) -> list[tuple[str, str, str]]:
    soup = BeautifulSoup(raw, "html.parser")
    items: list[tuple[str, str, str]] = []
    for item in soup.find_all(["item", "entry"]):
        title_tag = item.find("title")
        title = " ".join((title_tag.get_text(" ", strip=True) if title_tag else "").split())
        href = _rss_item_url(item)
        published = _first_tag_text(item, "pubDate", "pubdate", "published", "updated", "dc:date", "date")
        items.append((title, href, published))
    return items


def _xml_tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _xml_child_text(element: ET.Element, *names: str) -> str:
    normalized = {name.lower() for name in names}
    for child in element.iter():
        if child is element:
            continue
        if _xml_tag_name(child.tag) in normalized and child.text:
            return child.text.strip()
    return ""


def _xml_item_url(element: ET.Element) -> str:
    for child in element.iter():
        if child is element or _xml_tag_name(child.tag) != "link":
            continue
        href = child.attrib.get("href") or child.text
        if href:
            return href.strip()
    return _xml_child_text(element, "guid", "id")


def _first_tag_text(item, *names: str) -> str:
    normalized_names = {name.lower() for name in names}
    for name in names:
        tag = item.find(name)
        if tag and tag.get_text(" ", strip=True):
            return tag.get_text(" ", strip=True)
    tag = item.find(lambda candidate: getattr(candidate, "name", "").lower() in normalized_names)
    if tag and tag.get_text(" ", strip=True):
        return tag.get_text(" ", strip=True)
    return ""


def _extract_published_at(soup: BeautifulSoup) -> datetime | None:
    selectors = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"property": "article:modified_time"}),
        ("meta", {"property": "og:published_time"}),
        ("meta", {"property": "og:updated_time"}),
        ("meta", {"name": "pubdate"}),
        ("meta", {"name": "pub_date"}),
        ("meta", {"name": "publishdate"}),
        ("meta", {"name": "publish_date"}),
        ("meta", {"name": "publish_time"}),
        ("meta", {"name": "published_time"}),
        ("meta", {"name": "publication_date"}),
        ("meta", {"name": "datePublished"}),
        ("meta", {"name": "datepublished"}),
        ("meta", {"name": "date"}),
        ("meta", {"name": "datetime"}),
        ("meta", {"name": "dc.date"}),
        ("meta", {"name": "dc.date.issued"}),
        ("meta", {"name": "dcterms.created"}),
        ("meta", {"name": "dcterms.date"}),
        ("meta", {"name": "sailthru.date"}),
        ("meta", {"name": "parsely-pub-date"}),
        ("meta", {"name": "weibo:article:create_at"}),
        ("meta", {"name": "byl:published_time"}),
        ("meta", {"itemprop": "datePublished"}),
        ("meta", {"itemprop": "dateCreated"}),
        ("meta", {"itemprop": "dateModified"}),
    ]
    for tag_name, attrs in selectors:
        tag = soup.find(tag_name, attrs=attrs)
        value = tag.get("content") if tag else None
        parsed = _parse_published_datetime(str(value or ""))
        if parsed:
            return parsed

    time_tag = soup.find("time", attrs={"datetime": True}) or soup.find("time")
    if time_tag:
        parsed = _parse_published_datetime(str(time_tag.get("datetime") or time_tag.get_text(" ", strip=True)))
        if parsed:
            return parsed

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        parsed = _extract_json_ld_date(script.get_text(" ", strip=True))
        if parsed:
            return parsed

    text = soup.get_text(" ", strip=True)
    match = re.search(r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", text)
    return _parse_published_datetime(match.group(0)) if match else None


def _extract_json_ld_date(raw: str) -> datetime | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    stack = payload if isinstance(payload, list) else [payload]
    while stack:
        item = stack.pop(0)
        if isinstance(item, list):
            stack.extend(item)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("datePublished", "dateCreated", "dateModified", "uploadDate"):
            parsed = _parse_published_datetime(str(item.get(key) or ""))
            if parsed:
                return parsed
        stack.extend(value for value in item.values() if isinstance(value, (dict, list)))
    return None


def _parse_published_datetime(value: str, now: datetime | None = None) -> datetime | None:
    text = re.sub(r"[*_`]+", "", value or "").strip()
    if not text:
        return None
    relative = _parse_relative_datetime(text, now)
    if relative:
        return relative
    try:
        parsed = parsedate_to_datetime(text)
        if parsed:
            return _to_utc(parsed)
    except (TypeError, ValueError):
        pass

    normalized = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
        .replace("T", " ")
        .replace("Z", "+00:00")
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    candidates = [normalized, *_embedded_datetime_candidates(normalized)]
    for candidate in candidates:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", candidate):
            candidate = f"{candidate} 00:00:00"
        try:
            return _to_utc(datetime.fromisoformat(candidate))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return _to_utc(datetime.strptime(candidate, fmt))
                except ValueError:
                    continue
    return None


def _embedded_datetime_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r"20\d{2}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[+-]\d{2}:?\d{2})?)?",
        r"20\d{2}\s*-\s*\d{1,2}\s*-\s*\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = re.sub(r"\s*-\s*", "-", match.group(0)).strip()
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _parse_relative_datetime(value: str, now: datetime | None = None) -> datetime | None:
    text = " ".join(value.lower().split())
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    base = base.astimezone(timezone.utc)

    if any(term in text for term in ("刚刚", "刚才", "just now")):
        return base

    english = re.search(r"(\d+)\s*(minute|hour|day|week|month|year)s?\s+ago", text)
    if english:
        amount = int(english.group(1))
        unit = english.group(2)
        days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(unit)
        delta = timedelta(minutes=amount) if unit == "minute" else timedelta(hours=amount) if unit == "hour" else timedelta(days=amount * days)
        return base - delta

    chinese = re.search(r"(\d+)\s*(分钟|小时|天|日|周|星期|个月|月|年)前", text)
    if chinese:
        amount = int(chinese.group(1))
        unit = chinese.group(2)
        if unit == "分钟":
            return base - timedelta(minutes=amount)
        if unit == "小时":
            return base - timedelta(hours=amount)
        days = {"天": 1, "日": 1, "周": 7, "星期": 7, "个月": 30, "月": 30, "年": 365}[unit]
        return base - timedelta(days=amount * days)

    local_base = base.astimezone(timezone(timedelta(hours=8)))
    day_offset = 0
    if "前天" in text:
        day_offset = 2
    elif "昨天" in text or "yesterday" in text:
        day_offset = 1
    elif "今天" not in text and "today" not in text:
        return None
    time_match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    hour = int(time_match.group(1)) if time_match else 0
    minute = int(time_match.group(2)) if time_match else 0
    second = int(time_match.group(3) or 0) if time_match else 0
    local_value = (local_base - timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=second, microsecond=0)
    return local_value.astimezone(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone(timedelta(hours=8)))
    return value.astimezone(timezone.utc)

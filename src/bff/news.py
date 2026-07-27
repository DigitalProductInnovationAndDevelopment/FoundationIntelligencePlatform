"""Foundation news discovery, article enrichment, and sourced summaries.

News discovery is deliberately recent-first, but it keeps older and undated
coverage explicit instead of treating an empty 28-day window as an error.  All
network work is bounded and failures remain isolated from the profile itself.
"""

from __future__ import annotations

import calendar
import ipaddress
import json
import os
import re
import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Iterator, List, Literal, Mapping, Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from bff import config
from bff.auth import get_current_user_token
from bff.schemas import NewsSource, NewsSummary
from bff.utils.logging import logger

try:
    import anthropic
except ImportError:  # pragma: no cover - depends on the deployment image
    anthropic = None

try:
    from googlenewsdecoder import gnewsdecoder
except ImportError:  # pragma: no cover - optional decoder dependency
    gnewsdecoder = None


DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
DEFAULT_MAX_ARTICLES = 8
DEFAULT_WEEKS = 4
DEFAULT_FALLBACK_LOOKBACK = "12m"
REQUEST_TIMEOUT = 10
SUMMARY_TIMEOUT = 45
MIN_ARTICLE_CHARS = 300
MAX_ARTICLE_CHARS = 4000
MAX_ARTICLE_BYTES = 2 * 1024 * 1024
MAX_RSS_BYTES = 2 * 1024 * 1024
MAX_FEED_ENTRIES = 100
MAX_ARTICLE_WORKERS = 4
MAX_REDIRECTS = 3
MAX_SOURCE_ATTEMPTS = 2
MAX_SEARCH_TERMS = 5
MAX_SEARCH_TERM_LENGTH = 160
NEWS_CONTEXT_STOP_WORDS = {
    "and", "benevolent", "charity", "foundation", "fund", "institution",
    "of", "technology", "the",
}
LOOKBACKS = {"4w", "3m", "12m", "all"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "vero_conv",
    "vero_id",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LOCALE_PRESETS = {
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "de": {"hl": "de-DE", "gl": "DE", "ceid": "DE:de"},
}


class NewsSourceUnavailable(RuntimeError):
    """The upstream news source failed, as distinct from a valid empty feed."""


@dataclass
class Article:
    title: str
    link: str
    source: str
    published: str
    text: str = ""
    note: str = ""
    published_at: Optional[datetime] = None
    classification: str = "undated"
    canonical_link: str = ""


@dataclass
class NewsSelection:
    articles: list[Article]
    lookback: str
    date_from: Optional[str]
    date_to: Optional[str]
    fallback_used: bool
    source_status: str = "success"


def slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug or "foundation"


def build_rss_url(
    foundation_name: str,
    locale: Mapping[str, str],
    *,
    exact_phrase: bool = True,
) -> str:
    # Date operators can weaken Google News exact-phrase matching. Fetch the
    # bounded RSS feed once and apply deterministic date semantics locally.
    query = f'"{foundation_name}"' if exact_phrase else foundation_name
    params = {"q": query, **locale}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def normalize_news_search_terms(
    foundation_name: str,
    aliases: Optional[Iterable[str]] = None,
) -> list[str]:
    """Return distinct, bounded organization names for a news lookup.

    A legal registered name and a trading name can both be correct.  Searching
    only one of them silently misses coverage for organizations such as
    Foothold / The Institution of Engineering and Technology Benevolent Fund.
    Terms are treated as separate exact-phrase searches and are later merged
    through the normal canonical-URL de-duplication path.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for value in [foundation_name, *(aliases or [])]:
        candidate = " ".join(str(value or "").split()).strip()
        if not candidate:
            continue
        if len(candidate) > MAX_SEARCH_TERM_LENGTH:
            raise ValueError("Each news search name must be 160 characters or fewer.")
        identity = candidate.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        terms.append(candidate)
        if len(terms) >= MAX_SEARCH_TERMS:
            break
    if not terms:
        raise ValueError("At least one organization name is required for news research.")
    return terms


def _trading_name_context_query(trading_name: str, legal_name: str) -> Optional[str]:
    """Make an alias query specific enough to avoid ordinary-word matches.

    A trading name such as ``Foothold`` is a common noun.  Combining it with
    ``charity`` and one distinctive legal-name term keeps the search focused on
    the linked organisation rather than unrelated articles about a foothold.
    """
    context_terms = [
        token.casefold()
        for token in re.findall(r"[A-Za-z]{4,}", legal_name)
        if token.casefold() not in NEWS_CONTEXT_STOP_WORDS
    ]
    context = next((term for term in context_terms if term), None)
    return f"{trading_name} charity {context}" if context else None


def _news_search_queries(
    foundation_name: str,
    aliases: Optional[Iterable[str]],
) -> list[tuple[str, bool, Optional[str], Optional[str]]]:
    terms = normalize_news_search_terms(foundation_name, aliases)
    queries: list[tuple[str, bool, Optional[str], Optional[str]]] = [(terms[0], True, None, None)]
    for alias in terms[1:]:
        contextual_query = _trading_name_context_query(alias, terms[0])
        if contextual_query:
            context = contextual_query.rsplit(" ", 1)[-1]
            queries.append((contextual_query, False, alias, context))
    return queries


def _is_contextual_alias_result(
    article: Article,
    *,
    alias: str,
    context: str,
) -> bool:
    """Reject ordinary-word alias matches that do not identify the organisation."""
    title_and_source = f"{article.title} {article.source}".casefold()
    alias_match = alias.casefold() in title_and_source
    # ``engineering`` should also match normal title forms such as
    # ``Engineers walking …``.
    context_match = context.casefold()[:7] in title_and_source
    charity_match = any(marker in title_and_source for marker in (
        "charity", "charitable", "grant", "benevolent", "fund", "iet",
    ))
    return (context_match and charity_match) or (alias_match and context_match)


def _parse_published(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_published_at(entry: Mapping[str, object]) -> Optional[datetime]:
    parsed = entry.get("published_parsed")
    if parsed:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
        except (OverflowError, TypeError, ValueError):
            pass
    return _parse_published(entry.get("published") or entry.get("updated"))


def _read_rss_response(response: requests.Response) -> bytes:
    payload = response.content
    if len(payload) > MAX_RSS_BYTES:
        raise NewsSourceUnavailable("Google News RSS response exceeded the size limit")
    return payload


def fetch_news_entries(
    foundation_name: str,
    weeks: int,
    max_articles: int,
    locale: Mapping[str, str],
    *,
    exact_phrase: bool = True,
) -> list[Article]:
    """Fetch a bounded, unfiltered Google News RSS candidate set.

    ``weeks`` and ``max_articles`` remain in the signature for compatibility;
    date selection and final result limits are applied after canonical dedupe.
    A valid empty feed returns ``[]``. Transport or malformed-feed failures
    raise ``NewsSourceUnavailable`` so callers never mislabel them as no news.
    """
    del weeks, max_articles
    url = build_rss_url(foundation_name, locale, exact_phrase=exact_phrase)
    logger.info("Querying Google News RSS for %s", foundation_name)

    response: Optional[requests.Response] = None
    last_error: Optional[BaseException] = None
    for attempt in range(MAX_SOURCE_ATTEMPTS):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = NewsSourceUnavailable("Google News RSS temporarily unavailable")
                if attempt + 1 < MAX_SOURCE_ATTEMPTS:
                    continue
            response.raise_for_status()
            break
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt + 1 < MAX_SOURCE_ATTEMPTS:
                continue
        except requests.RequestException as exc:
            last_error = exc
            break
    else:  # pragma: no cover - loop exits through the explicit failure below
        response = None

    if response is None or response.status_code == 429 or response.status_code >= 500:
        logger.warning(
            "Google News RSS request failed (%s)",
            last_error.__class__.__name__ if last_error else "upstream error",
        )
        raise NewsSourceUnavailable("Google News RSS is temporarily unavailable") from last_error

    try:
        feed = feedparser.parse(_read_rss_response(response))
    except Exception as exc:  # pragma: no cover - feedparser is normally defensive
        raise NewsSourceUnavailable("Google News RSS could not be parsed") from exc
    if getattr(feed, "bozo", 0) and not feed.entries:
        raise NewsSourceUnavailable("Google News RSS returned malformed data")

    articles: list[Article] = []
    for entry in feed.entries[:MAX_FEED_ENTRIES]:
        raw_title = str(entry.get("title") or "").strip()
        if not raw_title:
            continue
        title, publisher = raw_title, ""
        if " - " in raw_title:
            title, publisher = raw_title.rsplit(" - ", 1)
        if not publisher:
            source_value = entry.get("source")
            if isinstance(source_value, Mapping):
                publisher = str(source_value.get("title") or "")
        published = str(entry.get("published") or entry.get("updated") or "")
        articles.append(
            Article(
                title=title.strip(),
                link=str(entry.get("link") or "").strip(),
                source=publisher.strip(),
                published=published,
                published_at=_entry_published_at(entry),
            )
        )
    return articles


def canonicalize_article_url(url: str) -> str:
    """Return a stable URL with fragments and known tracking keys removed."""
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return value
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (
            parsed.scheme.lower() == "https" and port == 443
        )
        netloc = hostname if not port or default_port else f"{hostname}:{port}"
        query = [
            (key, item)
            for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in TRACKING_QUERY_KEYS
        ]
        return urllib.parse.urlunsplit(
            (
                parsed.scheme.lower(),
                netloc,
                parsed.path or "/",
                urllib.parse.urlencode(sorted(query)),
                "",
            )
        )
    except (UnicodeError, ValueError):
        return value


def _months_before(value: datetime, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _selection_start(now: datetime, lookback: str, weeks: int) -> Optional[date]:
    if lookback == "all":
        return None
    if lookback == "3m":
        return _months_before(now, 3)
    if lookback == "12m":
        return _months_before(now, 12)
    if lookback == "4w":
        return (now - timedelta(weeks=4)).date()
    return (now - timedelta(weeks=weeks)).date()


def _article_sort_key(article: Article) -> tuple[object, ...]:
    priority = {"recent": 0, "older": 1, "undated": 2}.get(article.classification, 3)
    timestamp = article.published_at.timestamp() if article.published_at else 0.0
    return (priority, -timestamp, article.title.casefold(), article.link)


def _deduplicate_articles(articles: list[Article], max_articles: int) -> list[Article]:
    unique: list[Article] = []
    seen_urls: set[str] = set()
    seen_titles: set[tuple[str, str]] = set()
    for article in sorted(articles, key=_article_sort_key):
        canonical = canonicalize_article_url(article.link)
        title_key = (
            re.sub(r"\s+", " ", article.title).strip().casefold(),
            re.sub(r"\s+", " ", article.source).strip().casefold(),
        )
        if (canonical and canonical in seen_urls) or (title_key[0] and title_key in seen_titles):
            continue
        if canonical:
            seen_urls.add(canonical)
            article.link = canonical
            article.canonical_link = canonical
        if title_key[0]:
            seen_titles.add(title_key)
        unique.append(article)
        if len(unique) >= max_articles:
            break
    return unique


def select_news_articles(
    articles: list[Article],
    *,
    weeks: int = DEFAULT_WEEKS,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    lookback: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    now: Optional[datetime] = None,
) -> NewsSelection:
    """Classify, range-filter, order, and canonical-dedupe RSS candidates."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end_date = date_to or current.date()
    if date_from and date_from > end_date:
        raise ValueError("date_from must be on or before date_to")

    explicit_range = date_from is not None or date_to is not None
    requested_lookback = lookback or ("4w" if weeks == DEFAULT_WEEKS else f"{weeks}w")
    start_date = date_from if explicit_range else _selection_start(current, requested_lookback, weeks)
    recent_start = (current - timedelta(weeks=weeks)).date()

    candidates: list[Article] = []
    for article in articles:
        published_at = article.published_at or _parse_published(article.published)
        article.published_at = published_at
        if published_at is None:
            article.classification = "undated"
            # A strict user-supplied range cannot safely claim an undated item.
            if not explicit_range:
                candidates.append(article)
            continue
        published_date = published_at.date()
        if published_date > end_date:
            continue
        article.classification = "recent" if published_date >= recent_start else "older"
        if start_date is None or published_date >= start_date:
            candidates.append(article)

    has_recent = any(article.classification == "recent" for article in candidates)
    effective_lookback = "custom" if explicit_range else requested_lookback

    # The default 28-day search automatically expands to a bounded 12-month
    # fallback only when it found no dated recent coverage. Valid empty feeds do
    # not trigger another upstream request.
    auto_fallback = not explicit_range and requested_lookback == "4w" and not has_recent
    if auto_fallback:
        fallback_start = _months_before(current, 12)
        candidates = []
        for article in articles:
            if article.published_at is None:
                candidates.append(article)
            elif fallback_start <= article.published_at.date() <= end_date:
                candidates.append(article)
        effective_lookback = DEFAULT_FALLBACK_LOOKBACK

    fallback_used = (
        not any(article.classification == "recent" for article in candidates)
        and any(article.classification == "older" for article in candidates)
    )
    selected = _deduplicate_articles(candidates, max_articles)
    return NewsSelection(
        articles=selected,
        lookback=effective_lookback,
        date_from=(
            date_from.isoformat()
            if explicit_range and date_from
            else (_selection_start(current, effective_lookback, weeks) or date.min).isoformat()
            if effective_lookback != "all"
            else None
        ),
        date_to=end_date.isoformat(),
        fallback_used=fallback_used,
        source_status="success" if selected else "success_empty",
    )


def discover_news(
    foundation_name: str,
    *,
    lang: str,
    weeks: int,
    max_articles: int,
    lookback: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
    aliases: Optional[Iterable[str]] = None,
) -> NewsSelection:
    search_queries = _news_search_queries(foundation_name, aliases)
    raw_articles: list[Article] = []
    failed_searches = 0
    for search_term, exact_phrase, alias, context in search_queries:
        try:
            found = fetch_news_entries(
                search_term,
                weeks,
                max_articles,
                LOCALE_PRESETS[lang],
                exact_phrase=exact_phrase,
            )
            if alias and context:
                found = [
                    article for article in found
                    if _is_contextual_alias_result(article, alias=alias, context=context)
                ]
            raw_articles.extend(found)
        except NewsSourceUnavailable:
            failed_searches += 1

    if failed_searches == len(search_queries):
        selection = select_news_articles(
            [],
            weeks=weeks,
            max_articles=max_articles,
            lookback=lookback,
            date_from=date_from,
            date_to=date_to,
        )
        selection.source_status = "failed"
        return selection
    return select_news_articles(
        raw_articles,
        weeks=weeks,
        max_articles=max_articles,
        lookback=lookback,
        date_from=date_from,
        date_to=date_to,
    )


def resolve_google_news_url(url: str) -> tuple[str, str]:
    """Resolve a Google News wrapper while preserving a safe local fallback."""
    try:
        hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        hostname = ""
    if gnewsdecoder is None or not (hostname == "news.google.com" or hostname.endswith(".news.google.com")):
        return url, ""
    try:
        result = gnewsdecoder(url, interval=0)
    except Exception as exc:
        return url, f"could not resolve Google News redirect ({exc.__class__.__name__})"
    if result.get("status") and result.get("decoded_url"):
        return str(result["decoded_url"]), ""
    return url, "could not resolve Google News redirect"


def is_safe_public_url(url: str) -> bool:
    """Fail closed unless every resolved target address is globally routable."""
    value = str(url or "").strip()
    if not value or len(value) > 2048:
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        if parsed.username is not None or parsed.password is not None or not parsed.hostname:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return False
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            addresses = [ipaddress.ip_address(hostname)]
        except ValueError:
            records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            addresses = {ipaddress.ip_address(record[4][0].split("%", 1)[0]) for record in records}
        return bool(addresses) and all(address.is_global for address in addresses)
    except (OSError, UnicodeError, ValueError):
        return False


def _join_notes(*notes: str) -> str:
    return "; ".join(note for note in notes if note)


def _bounded_response_text(response: requests.Response) -> tuple[str, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        remaining = MAX_ARTICLE_BYTES - total
        if remaining <= 0:
            truncated = True
            break
        chunks.append(chunk[:remaining])
        total += min(len(chunk), remaining)
        if len(chunk) > remaining:
            truncated = True
            break
    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace"), truncated


def fetch_article_text(url: str) -> tuple[str, str]:
    """Fetch article text with DNS/redirect SSRF protection and byte limits."""
    resolved_url, resolve_note = resolve_google_news_url(url)
    current_url = resolved_url
    response: Optional[requests.Response] = None
    for redirect_count in range(MAX_REDIRECTS + 1):
        if not is_safe_public_url(current_url):
            return "", _join_notes(resolve_note, "article URL was blocked by network safety policy")
        try:
            response = requests.get(
                current_url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            return "", _join_notes(resolve_note, f"could not fetch article ({exc.__class__.__name__})")

        if response.status_code in REDIRECT_STATUSES:
            location = response.headers.get("location")
            response.close()
            if not location:
                return "", _join_notes(resolve_note, "article redirect had no destination")
            if redirect_count >= MAX_REDIRECTS:
                return "", _join_notes(resolve_note, "article redirect limit exceeded")
            current_url = urllib.parse.urljoin(current_url, location)
            continue
        try:
            response.raise_for_status()
            html, truncated = _bounded_response_text(response)
        except requests.RequestException as exc:
            return "", _join_notes(resolve_note, f"could not fetch article ({exc.__class__.__name__})")
        finally:
            response.close()
        break
    else:  # pragma: no cover - redirect bound returns above
        return "", _join_notes(resolve_note, "article redirect limit exceeded")

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # pragma: no cover - defensive parser boundary
        return "", _join_notes(resolve_note, f"could not parse HTML ({exc.__class__.__name__})")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    container = soup.find("article") or soup
    paragraphs = [paragraph.get_text(" ", strip=True) for paragraph in container.find_all("p")]
    text = "\n".join(paragraph for paragraph in paragraphs if len(paragraph) > 40)
    response_note = "article response was truncated" if truncated else ""
    if len(text) < MIN_ARTICLE_CHARS:
        return text, _join_notes(
            resolve_note,
            response_note,
            "page content too short or blocked, falling back to RSS title only",
        )
    return text[:MAX_ARTICLE_CHARS], _join_notes(resolve_note, response_note)


def _enrich_article(article: Article) -> Article:
    try:
        article.text, article.note = fetch_article_text(article.link)
    except Exception as exc:  # isolate one publisher from all other articles
        logger.warning("Article enrichment failed (%s)", exc.__class__.__name__)
        article.text = ""
        article.note = "article content could not be loaded"
    return article


def enrich_articles(articles: list[Article]) -> list[Article]:
    if not articles:
        return []
    workers = min(MAX_ARTICLE_WORKERS, len(articles))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="news-article") as executor:
        return list(executor.map(_enrich_article, articles))


def build_prompt(foundation_name: str, weeks: int, articles: list[Article]) -> str:
    parts = [
        f'Below are news articles about the foundation "{foundation_name}". '
        f"Recent means the last {weeks} week(s); explicitly labelled older or undated sources may also be included.",
        "",
    ]
    for index, article in enumerate(articles, 1):
        parts.extend([f"### Source [{index}]", f"Title: {article.title}"])
        if article.source:
            parts.append(f"Publisher: {article.source}")
        if article.published:
            parts.append(f"Published: {article.published}")
        parts.append(f"Age classification: {article.classification}")
        parts.append(f"URL: {article.link}")
        if article.text:
            parts.extend(["Content:", article.text])
        else:
            parts.append("Content: (not available - " + (article.note or "unknown reason") + ")")
        parts.append("")
    return "\n".join(parts)


SYSTEM_PROMPT = (
    "You are a research assistant summarizing news about a philanthropic foundation. "
    "Base the briefing strictly on the provided sources and cite source numbers in square "
    "brackets. Clearly distinguish recent, older, and undated reporting. Use short markdown "
    "headers for applicable programs, grants, partnerships, personnel changes, and other "
    "notable news. Omit unsupported sections and never guess. Keep the tone factual and concise."
)


def summarize_with_claude(
    foundation_name: str,
    weeks: int,
    articles: list[Article],
    model: str,
) -> str:
    if anthropic is None:
        raise RuntimeError("The anthropic package is unavailable")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    auth_token = config.ANTHROPIC_AUTH_TOKEN
    client_options: dict[str, object] = {
        "timeout": SUMMARY_TIMEOUT,
        "max_retries": 1,
    }
    if config.ANTHROPIC_BASE_URL:
        client_options["base_url"] = config.ANTHROPIC_BASE_URL
    if auth_token:
        client_options["auth_token"] = auth_token
    elif api_key:
        client_options["api_key"] = api_key
    elif config.ANTHROPIC_BASE_URL:
        # Some internal proxies authenticate upstream and only require a
        # syntactically present token. Passing it directly avoids process-global
        # environment mutation across concurrent requests.
        client_options["auth_token"] = "not-required"
    else:
        raise RuntimeError("No summary-provider credentials are configured")

    client = anthropic.Anthropic(**client_options)
    user_prompt = build_prompt(foundation_name, weeks, articles)
    user_prompt += f'\n\nWrite a sourced briefing about "{foundation_name}" based only on these sources.'
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _source_models(articles: list[Article]) -> list[NewsSource]:
    return [
        NewsSource(
            title=article.title,
            link=article.link,
            source=article.source,
            published=article.published,
            note=article.note,
            classification=article.classification,
            published_at=article.published_at.isoformat() if article.published_at else None,
        )
        for article in articles
    ]


def _base_result(
    foundation_name: str,
    weeks: int,
    selection: NewsSelection,
    *,
    summary: str,
    sources: list[NewsSource],
    result_status: str,
    summary_status: str,
    message: Optional[str] = None,
) -> NewsSummary:
    return NewsSummary(
        foundation=foundation_name,
        summary=summary,
        sources=sources,
        searched_weeks=weeks,
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=result_status,
        summary_status=summary_status,
        source_status=selection.source_status,
        lookback=selection.lookback,
        date_from=selection.date_from,
        date_to=selection.date_to,
        fallback_used=selection.fallback_used,
        message=message,
    )


def _empty_or_failed_result(
    foundation_name: str,
    weeks: int,
    selection: NewsSelection,
) -> NewsSummary:
    if selection.source_status == "failed":
        return _base_result(
            foundation_name,
            weeks,
            selection,
            summary="",
            sources=[],
            result_status="source_unavailable",
            summary_status="not_requested",
            message="The news source is temporarily unavailable. The profile remains available.",
        )
    return _base_result(
        foundation_name,
        weeks,
        selection,
        summary="",
        sources=[],
        result_status="success_empty",
        summary_status="not_requested",
        message="No matching news was found in the selected window.",
    )


def _summarize_selection(
    foundation_name: str,
    weeks: int,
    model: str,
    selection: NewsSelection,
    articles: list[Article],
) -> NewsSummary:
    sources = _source_models(articles)
    try:
        summary = summarize_with_claude(foundation_name, weeks, articles, model)
        if not summary:
            raise RuntimeError("summary provider returned no text")
    except Exception as exc:
        logger.warning("News summary unavailable for %s (%s)", foundation_name, exc.__class__.__name__)
        return _base_result(
            foundation_name,
            weeks,
            selection,
            summary="",
            sources=sources,
            result_status="partial_success",
            summary_status="summary_unavailable",
            message="Articles were found, but the generated summary is temporarily unavailable.",
        )
    return _base_result(
        foundation_name,
        weeks,
        selection,
        summary=summary,
        sources=sources,
        result_status="success_older" if selection.fallback_used else "success",
        summary_status="available",
    )


def _validate_options(
    foundation_name: str,
    lang: str,
    lookback: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
) -> None:
    if not foundation_name.strip():
        raise HTTPException(status_code=400, detail="foundation_name must not be empty")
    if lang not in LOCALE_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported lang '{lang}'. Choose one of: {sorted(LOCALE_PRESETS)}",
        )
    if lookback is not None and lookback not in LOOKBACKS:
        raise HTTPException(status_code=400, detail="Unsupported news lookback")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")


def _parse_news_aliases(aliases: Optional[str]) -> list[str]:
    """Decode the bounded pipe-delimited aliases accepted by the HTTP API."""
    if not aliases:
        return []
    return [value for value in aliases.split("|") if value.strip()]


router = APIRouter(
    prefix="/api/news",
    tags=["Foundation News"],
    dependencies=[Depends(get_current_user_token)],
)


@router.get("/{foundation_name}/summary", response_model=NewsSummary)
def get_foundation_news_summary(
    foundation_name: str,
    lang: str = Query(default="en", min_length=2, max_length=2),
    max_articles: int = Query(default=DEFAULT_MAX_ARTICLES, ge=1, le=20),
    weeks: int = Query(default=DEFAULT_WEEKS, ge=1, le=52),
    model: str = Query(default=DEFAULT_MODEL, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$"),
    lookback: Optional[Literal["4w", "3m", "12m", "all"]] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    aliases: Optional[str] = Query(default=None, max_length=800),
):
    """Return a sourced briefing without turning valid empty coverage into an error."""
    _validate_options(foundation_name, lang, lookback, date_from, date_to)
    try:
        selection = discover_news(
            foundation_name,
            lang=lang,
            weeks=weeks,
            max_articles=max_articles,
            lookback=lookback,
            date_from=date_from,
            date_to=date_to,
            aliases=_parse_news_aliases(aliases),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not selection.articles:
        return _empty_or_failed_result(foundation_name, weeks, selection)
    articles = enrich_articles(selection.articles)
    return _summarize_selection(foundation_name, weeks, model, selection, articles)


def _news_sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/{foundation_name}/summary/stream")
def stream_foundation_news_summary(
    foundation_name: str,
    lang: str = Query(default="en", min_length=2, max_length=2),
    max_articles: int = Query(default=DEFAULT_MAX_ARTICLES, ge=1, le=20),
    weeks: int = Query(default=DEFAULT_WEEKS, ge=1, le=52),
    model: str = Query(default=DEFAULT_MODEL, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$"),
    lookback: Optional[Literal["4w", "3m", "12m", "all"]] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    aliases: Optional[str] = Query(default=None, max_length=800),
):
    """Stream discovery stages and always finish with a typed result when possible."""
    _validate_options(foundation_name, lang, lookback, date_from, date_to)
    try:
        alias_values = _parse_news_aliases(aliases)
        normalize_news_search_terms(foundation_name, alias_values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def generate() -> Iterator[str]:
        try:
            yield _news_sse_event(
                "progress",
                {"step": "discovering", "label": "Searching recent Google News coverage"},
            )
            selection = discover_news(
                foundation_name,
                lang=lang,
                weeks=weeks,
                max_articles=max_articles,
                lookback=lookback,
                date_from=date_from,
                date_to=date_to,
                aliases=alias_values,
            )
            if selection.fallback_used:
                yield _news_sse_event(
                    "progress",
                    {
                        "step": "older_fallback",
                        "label": "No recent coverage found; using older matching news",
                    },
                )
            if not selection.articles:
                result = _empty_or_failed_result(foundation_name, weeks, selection)
                yield _news_sse_event("complete", result.model_dump())
                return

            yield _news_sse_event(
                "progress",
                {
                    "step": "reading",
                    "label": f"Reading and resolving {len(selection.articles)} candidate article(s)",
                    "article_count": len(selection.articles),
                },
            )
            articles = enrich_articles(selection.articles)
            yield _news_sse_event(
                "progress",
                {"step": "summarizing", "label": "Synthesizing a sourced AI briefing"},
            )
            result = _summarize_selection(foundation_name, weeks, model, selection, articles)
            yield _news_sse_event("complete", result.model_dump())
        except Exception as exc:  # unexpected programming/runtime boundary only
            logger.exception("Foundation news research failed for %s", foundation_name)
            yield _news_sse_event(
                "error",
                {
                    "detail": "News research could not be completed.",
                    "error_type": exc.__class__.__name__,
                },
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

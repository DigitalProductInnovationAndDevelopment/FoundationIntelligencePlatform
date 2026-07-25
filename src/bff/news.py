"""
news.py

Router that researches recent news about a foundation via Google News RSS,
downloads the article content of the top hits, and asks Claude to write a
summary of what the foundation has recently been up to (new programs,
grants/funding, partnerships, personnel changes, etc.).

Ported from the standalone foundation_news.py / api.py scraper into the BFF's
FastAPI router pattern (see bff/charity.py). Auth credentials for the Claude
API (ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL) are read once in bff/config.py
rather than via os.environ.get() here.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from bff import config
from bff.auth import get_current_user_token
from bff.schemas import NewsSource, NewsSummary
from bff.utils.logging import logger

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from googlenewsdecoder import gnewsdecoder
except ImportError:
    gnewsdecoder = None

DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
DEFAULT_MAX_ARTICLES = 8
DEFAULT_WEEKS = 4
REQUEST_TIMEOUT = 10
MIN_ARTICLE_CHARS = 300
MAX_ARTICLE_CHARS = 4000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Google News RSS locale presets. "hl" (language) should generally match the
# language of the foundation name you search for -- searching an English name
# with a German locale (or vice versa) measurably hurts result relevance.
LOCALE_PRESETS = {
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "de": {"hl": "de-DE", "gl": "DE", "ceid": "DE:de"},
}


@dataclass
class Article:
    title: str
    link: str
    source: str
    published: str
    text: str = ""
    note: str = ""  # e.g. "content could not be loaded, falling back to title"


def slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug or "foundation"


def build_rss_url(foundation_name: str, locale: dict) -> str:
    # IMPORTANT: Google News RSS's `when:`/`after:` date operators silently break
    # exact-phrase (quoted) matching -- a quoted search with no date operator
    # returns dozens of on-topic results, but adding `when:28d` to the *same*
    # quoted query collapses it to a handful of unrelated hits (verified: the
    # backend seems to fall back to a loose OR-match once a date operator is
    # present). So we deliberately do NOT put a date filter in the query and
    # instead filter client-side on each entry's published date.
    query = f'"{foundation_name}"'
    params = {"q": query, **locale}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def fetch_news_entries(foundation_name: str, weeks: int, max_articles: int, locale: dict) -> list[Article]:
    import ssl
    url = build_rss_url(foundation_name, locale)
    logger.info(f"Querying Google News RSS: {url}")
    
    # Temporarily bypass SSL verification on macOS to prevent certificate verification failures in feedparser
    old_context = getattr(ssl, "_create_default_https_context", None)
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
    except AttributeError:
        pass

    try:
        feed = feedparser.parse(url)
    finally:
        if old_context:
            try:
                ssl._create_default_https_context = old_context
            except AttributeError:
                pass

    if getattr(feed, "bozo", 0) and not feed.entries:
        logger.warning(f"Could not parse RSS feed ({feed.bozo_exception})")

    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    articles: list[Article] = []
    for entry in feed.entries:
        parsed = entry.get("published_parsed")
        if parsed:
            published_dt = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            if published_dt < cutoff:
                continue  # older than the requested window -- skip

        raw_title = entry.get("title", "").strip()
        title, source = raw_title, ""
        if " - " in raw_title:
            title, source = raw_title.rsplit(" - ", 1)
        if not source:
            src = entry.get("source")
            if isinstance(src, dict):
                source = src.get("title", "")
        articles.append(
            Article(
                title=title.strip(),
                link=entry.get("link", ""),
                source=source.strip(),
                published=entry.get("published", ""),
            )
        )
        if len(articles) >= max_articles:
            break
    return articles


def resolve_google_news_url(url: str) -> tuple[str, str]:
    """Resolves a news.google.com/rss/articles/... redirect wrapper to the real
    publisher URL. Google News links are not plain HTTP redirects -- following
    them directly lands on a Google cookie-consent page instead of the article,
    which is why we decode the wrapper's embedded payload instead.

    Returns (resolved_url, note). On failure, returns the original url unchanged
    and a note explaining why (caller then just tries the original url as-is).
    """
    if gnewsdecoder is None or "news.google.com" not in url:
        return url, ""
    try:
        result = gnewsdecoder(url, interval=1)
    except Exception as exc:
        return url, f"could not resolve Google News redirect ({exc.__class__.__name__})"
    if result.get("status") and result.get("decoded_url"):
        return result["decoded_url"], ""
    return url, f"could not resolve Google News redirect ({result.get('message', 'unknown error')})"


def fetch_article_text(url: str) -> tuple[str, str]:
    """Fetches and extracts readable text from an article URL.

    Returns (text, note). `note` is empty on a clean success and otherwise
    explains why the content is missing/short (used as a fallback marker).
    """
    resolved_url, resolve_note = resolve_google_news_url(url)
    try:
        resp = requests.get(
            resolved_url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        note = f"could not fetch article ({exc.__class__.__name__})"
        return "", f"{resolve_note}; {note}" if resolve_note else note

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # pragma: no cover - defensive
        return "", f"could not parse HTML ({exc.__class__.__name__})"

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    container = soup.find("article") or soup
    paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    text = "\n".join(p for p in paragraphs if len(p) > 40)

    if len(text) < MIN_ARTICLE_CHARS:
        note = "page content too short or blocked, falling back to RSS title only"
        return text, f"{resolve_note}; {note}" if resolve_note else note

    return text[:MAX_ARTICLE_CHARS], resolve_note


def enrich_articles(articles: list[Article]) -> list[Article]:
    for i, art in enumerate(articles, 1):
        logger.info(f"({i}/{len(articles)}) Fetching: {art.link}")
        text, note = fetch_article_text(art.link)
        art.text = text
        art.note = note
        if note:
            logger.warning(note)
        time.sleep(0.5)  # be polite to the target sites
    return articles


def build_prompt(foundation_name: str, weeks: int, articles: list[Article]) -> str:
    parts = [
        f'Below are news articles about the foundation "{foundation_name}" '
        f"published within roughly the last {weeks} week(s), collected via Google News.",
        "",
    ]
    for i, art in enumerate(articles, 1):
        parts.append(f"### Source [{i}]")
        parts.append(f"Title: {art.title}")
        if art.source:
            parts.append(f"Publisher: {art.source}")
        if art.published:
            parts.append(f"Published: {art.published}")
        parts.append(f"URL: {art.link}")
        if art.text:
            parts.append("Content:")
            parts.append(art.text)
        else:
            parts.append("Content: (not available - " + (art.note or "unknown reason") + ")")
        parts.append("")
    return "\n".join(parts)


SYSTEM_PROMPT = (
    "You are a research assistant summarizing recent news about a philanthropic "
    "foundation for someone who needs a quick, accurate briefing. Base your summary "
    "strictly on the provided sources. When you state a fact, reference the source "
    "number in square brackets, e.g. [1]. Structure the summary with short markdown "
    "headers covering, where applicable: New programs & initiatives, Grants & funding, "
    "Partnerships & collaborations, Personnel & leadership changes, Other notable news. "
    "If a section has no relevant information in the sources, omit it. If the sources "
    "are too thin or unrelated to draw conclusions, say so plainly instead of guessing. "
    "Keep the tone factual and concise."
)


def summarize_with_claude(foundation_name: str, weeks: int, articles: list[Article], model: str) -> str:
    if anthropic is None:
        raise RuntimeError(
            "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"
        )

    # Values come from bff/config.py (itself sourced from the environment) rather
    # than being read from os.environ directly here. Netlight's proxy expects
    # ANTHROPIC_AUTH_TOKEN; fall back to ANTHROPIC_API_KEY for anyone using a real
    # Anthropic key instead -- either one is fine, the anthropic client reads
    # whichever is present in the environment on its own.
    if config.ANTHROPIC_AUTH_TOKEN:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = config.ANTHROPIC_AUTH_TOKEN
    if config.ANTHROPIC_BASE_URL:
        os.environ["ANTHROPIC_BASE_URL"] = config.ANTHROPIC_BASE_URL

    if not os.environ.get("ANTHROPIC_AUTH_TOKEN") and not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning(
            "Neither ANTHROPIC_AUTH_TOKEN nor ANTHROPIC_API_KEY is set. Proceeding "
            "with a placeholder value - this is fine if ANTHROPIC_BASE_URL points at "
            "a proxy that does not require a real credential."
        )
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "not-required"

    # anthropic.Anthropic() reads ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
    # from the environment on its own; passing neither explicitly avoids triggering its
    # "both credentials set" warning when only one of the two is actually present.
    client = anthropic.Anthropic()

    user_prompt = build_prompt(foundation_name, weeks, articles)
    user_prompt += (
        f'\n\nWrite a summary of what "{foundation_name}" has been doing recently, '
        "based only on the sources above."
    )

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


router = APIRouter(
    prefix="/api/news",
    tags=["Foundation News"],
    dependencies=[Depends(get_current_user_token)],
)


@router.get("/{foundation_name}/summary", response_model=NewsSummary)
def get_foundation_news_summary(
    foundation_name: str,
    lang: str = "en",
    max_articles: int = DEFAULT_MAX_ARTICLES,
    weeks: int = DEFAULT_WEEKS,
    model: str = DEFAULT_MODEL,
):
    """
    Researches recent news about a foundation via Google News RSS, scrapes the
    top hits, and asks Claude for a summary of what it has recently been up to
    (new programs, grants/funding, partnerships, personnel changes, etc.).
    Requires a valid session cookie/token.
    """
    if lang not in LOCALE_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported lang '{lang}'. Choose one of: {sorted(LOCALE_PRESETS)}",
        )

    locale = LOCALE_PRESETS[lang]
    articles = fetch_news_entries(foundation_name, weeks, max_articles, locale)
    if not articles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'No news found for "{foundation_name}" in the last {weeks} week(s).',
        )

    # Blocking network calls (RSS parsing, redirect resolution, article scraping,
    # Claude API call) -- fine here since this is a plain `def` endpoint, which
    # FastAPI runs in a worker thread rather than on the event loop.
    articles = enrich_articles(articles)

    try:
        summary = summarize_with_claude(foundation_name, weeks, articles, model)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Claude API call failed: {exc}",
        ) from exc

    sources = [
        NewsSource(title=a.title, link=a.link, source=a.source, published=a.published, note=a.note)
        for a in articles
    ]
    return NewsSummary(
        foundation=foundation_name,
        summary=summary,
        sources=sources,
        searched_weeks=weeks,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _news_sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.get("/{foundation_name}/summary/stream")
def stream_foundation_news_summary(
    foundation_name: str,
    lang: str = "en",
    max_articles: int = DEFAULT_MAX_ARTICLES,
    weeks: int = DEFAULT_WEEKS,
    model: str = DEFAULT_MODEL,
):
    """Stream the real research stages before returning the completed briefing.

    The existing JSON endpoint remains available for integrations. The UI uses
    this SSE route so it can show an honest, server-driven research timeline
    rather than a generic spinner while the external RSS, article and model
    calls are running.
    """
    if lang not in LOCALE_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported lang '{lang}'. Choose one of: {sorted(LOCALE_PRESETS)}",
        )

    def generate() -> Iterator[str]:
        try:
            locale = LOCALE_PRESETS[lang]
            yield _news_sse_event("progress", {
                "step": "discovering",
                "label": "Searching recent Google News coverage",
            })
            articles = fetch_news_entries(foundation_name, weeks, max_articles, locale)
            if not articles:
                yield _news_sse_event("error", {
                    "detail": f'No news found for "{foundation_name}" in the last {weeks} week(s).',
                })
                return

            yield _news_sse_event("progress", {
                "step": "reading",
                "label": f"Reading and resolving {len(articles)} candidate article(s)",
                "article_count": len(articles),
            })
            articles = enrich_articles(articles)

            yield _news_sse_event("progress", {
                "step": "summarizing",
                "label": "Synthesizing a sourced AI briefing",
            })
            summary = summarize_with_claude(foundation_name, weeks, articles, model)
            result = NewsSummary(
                foundation=foundation_name,
                summary=summary,
                sources=[
                    NewsSource(title=a.title, link=a.link, source=a.source, published=a.published, note=a.note)
                    for a in articles
                ],
                searched_weeks=weeks,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
            yield _news_sse_event("complete", result.model_dump())
        except Exception as exc:
            logger.exception("Foundation news research failed for %s", foundation_name)
            yield _news_sse_event("error", {
                "detail": f"News research could not be completed: {exc}",
            })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

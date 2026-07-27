import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from bff.main import app
from bff.news import (
    Article,
    NewsSelection,
    discover_news,
    fetch_article_text,
    is_safe_public_url,
    select_news_articles,
)


NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def article(title, published, link="https://news.example/article", source="Example News"):
    return Article(title=title, link=link, source=source, published=published)


class TestNewsSelection(unittest.TestCase):
    @patch("bff.news.fetch_news_entries")
    def test_aliases_are_searched_and_merged_with_the_legal_name(self, fetch_entries):
        alias_article = article("Engineering charity expands Foothold support", "2026-07-20T10:00:00Z")
        generic_word_match = article("A foothold in a distant market", "2026-07-20T10:00:00Z")
        fetch_entries.side_effect = [[], [alias_article, generic_word_match]]

        selection = discover_news(
            "The Institution of Engineering and Technology Benevolent Fund",
            lang="en",
            weeks=4,
            max_articles=8,
            lookback=None,
            date_from=None,
            date_to=None,
            aliases=["Foothold", "foothold"],
        )

        self.assertEqual(
            [item.title for item in selection.articles],
            ["Engineering charity expands Foothold support"],
        )
        self.assertEqual(
            [call.args[0] for call in fetch_entries.call_args_list],
            [
                "The Institution of Engineering and Technology Benevolent Fund",
                "Foothold charity engineering",
            ],
        )
        self.assertEqual(
            [call.kwargs.get("exact_phrase", True) for call in fetch_entries.call_args_list],
            [True, False],
        )

    def test_recent_older_fallback_all_and_empty_are_explicit(self):
        recent = article("Recent", "2026-07-20T10:00:00Z")
        older = article("Older", "2026-01-20T10:00:00Z")
        current = select_news_articles([recent, older], now=NOW)
        self.assertEqual([item.title for item in current.articles], ["Recent"])
        self.assertFalse(current.fallback_used)

        fallback = select_news_articles([older], now=NOW)
        self.assertEqual([item.title for item in fallback.articles], ["Older"])
        self.assertTrue(fallback.fallback_used)
        self.assertEqual(fallback.lookback, "12m")

        all_articles = select_news_articles([older], lookback="all", now=NOW)
        self.assertEqual([item.title for item in all_articles.articles], ["Older"])
        empty = select_news_articles([], now=NOW)
        self.assertEqual(empty.source_status, "success_empty")
        self.assertEqual(empty.articles, [])

    def test_undated_and_tracking_urls_are_classified_and_deduplicated(self):
        undated = article("Undated", "", "https://news.example/undated")
        duplicate_one = article("Duplicate", "2026-07-20T10:00:00Z", "https://news.example/story?utm_source=mail&gclid=abc")
        duplicate_two = article("Duplicate copy", "2026-07-19T10:00:00Z", "https://news.example/story?fbclid=abc")
        selected = select_news_articles([undated, duplicate_one, duplicate_two], now=NOW)
        self.assertEqual(len(selected.articles), 2)
        self.assertEqual(selected.articles[-1].classification, "undated")
        self.assertEqual(selected.articles[0].link, "https://news.example/story")

    def test_ssrf_blocks_local_and_private_addresses(self):
        for url in (
            "http://localhost/news",
            "http://127.0.0.1/news",
            "http://10.0.0.2/news",
            "http://[::1]/news",
            "http://[fd00::1]/news",
            "file:///tmp/news.html",
        ):
            self.assertFalse(is_safe_public_url(url), url)

    @patch("bff.news.requests.get")
    @patch("bff.news.is_safe_public_url", side_effect=[True, False])
    def test_redirect_target_is_revalidated(self, safe_url, mocked_get):
        response = Mock()
        response.status_code = 302
        response.headers = {"location": "http://127.0.0.1/internal"}
        mocked_get.return_value = response
        text, note = fetch_article_text("https://public.example/article")
        self.assertEqual(text, "")
        self.assertIn("blocked", note)
        response.close.assert_called_once()
        self.assertEqual(safe_url.call_count, 2)


class TestNewsEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        login = self.client.post("/api/auth/login", json={"username": "admin", "password": "password"})
        self.cookies = login.cookies

    def selection(self, articles, *, fallback=False, source_status="success"):
        return NewsSelection(
            articles=articles,
            lookback="12m" if fallback else "4w",
            date_from="2026-06-29",
            date_to="2026-07-27",
            fallback_used=fallback,
            source_status=source_status,
        )

    @patch("bff.news.discover_news")
    def test_json_empty_and_source_failure_are_typed_successful_responses(self, discover):
        discover.return_value = self.selection([])
        empty = self.client.get("/api/news/Foothold/summary", cookies=self.cookies)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["status"], "success_empty")

        discover.return_value = self.selection([], source_status="failed")
        failed = self.client.get("/api/news/Foothold/summary", cookies=self.cookies)
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "source_unavailable")

    @patch("bff.news.summarize_with_claude", side_effect=RuntimeError("summary unavailable"))
    @patch("bff.news.enrich_articles", side_effect=lambda articles: articles)
    @patch("bff.news.discover_news")
    def test_json_and_sse_keep_articles_when_summary_fails(self, discover, _enrich, _summary):
        discover.return_value = self.selection([article("Older", "2026-01-20T10:00:00Z")], fallback=True)
        response = self.client.get("/api/news/Foothold/summary", cookies=self.cookies)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "partial_success")
        self.assertEqual(len(response.json()["sources"]), 1)

        stream = self.client.get("/api/news/Foothold/summary/stream", cookies=self.cookies)
        self.assertEqual(stream.status_code, 200)
        self.assertIn("event: progress", stream.text)
        self.assertIn("event: complete", stream.text)
        complete = stream.text.rsplit("data: ", 1)[1].strip()
        self.assertEqual(json.loads(complete)["status"], "partial_success")

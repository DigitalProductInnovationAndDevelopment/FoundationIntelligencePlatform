import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import requests

# Add project root to sys.path so we can import scraper and preprocessing
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from scrapers.philea import make_request, scrape
from preprocessing.extract_geo_topic import extract_tags, extract_geo

class TestPhileaScraper(unittest.TestCase):
    
    @patch("scrapers.philea.requests.request")
    def test_make_request_success(self, mock_request):
        # Setup mock response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_request.return_value = mock_resp
        
        resp = make_request("GET", "https://example.com")
        self.assertEqual(resp.status_code, 200)
        mock_request.assert_called_once_with("GET", "https://example.com", timeout=10)
        
    @patch("scrapers.philea.requests.request")
    @patch("scrapers.philea.time.sleep") # Mock sleep to speed up test run
    def test_make_request_retry_on_transient_error(self, mock_sleep, mock_request):
        # Setup mock responses: first two attempts return 500, third returns 200
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 500
        
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        
        mock_request.side_effect = [mock_resp_fail, mock_resp_fail, mock_resp_ok]
        
        resp = make_request("GET", "https://example.com", max_retries=3, backoff_factor=0.1)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_request.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        
    @patch("scrapers.philea.requests.request")
    @patch("scrapers.philea.time.sleep")
    def test_make_request_persistent_failure(self, mock_sleep, mock_request):
        # All requests fail with ConnectionError
        mock_request.side_effect = requests.exceptions.ConnectionError("Connection failed")
        
        with self.assertRaises(requests.exceptions.RequestException):
            make_request("GET", "https://example.com", max_retries=3, backoff_factor=0.1)
        self.assertEqual(mock_request.call_count, 3)

    @patch("scrapers.philea.make_request")
    @patch("scrapers.philea.BeautifulSoup")
    def test_scrape_limit(self, mock_bs, mock_make_request):
        # Mock members list call
        mock_list_resp = MagicMock()
        mock_list_resp.status_code = 200
        mock_list_resp.json.return_value = {
            "data": [
                {"name": "Member A", "link": "https://example.com/a"},
                {"name": "Member B", "link": "https://example.com/b"},
                {"name": "Member C", "link": "https://example.com/c"},
            ]
        }
        
        # Scrape detail page mock
        mock_detail_resp = MagicMock()
        mock_detail_resp.content = b"<html></html>"
        
        # Mock HTML parsing
        mock_content = MagicMock()
        mock_content.find.return_value = MagicMock(text="About content")
        mock_content.find_all.return_value = []
        mock_bs.return_value.find.return_value = mock_content
        
        mock_make_request.side_effect = [mock_list_resp, mock_detail_resp, mock_detail_resp]
        
        # Test scraping with a limit of 2
        members = scrape(limit=2, sleep_time=0)
        
        self.assertEqual(len(members), 2)
        self.assertEqual(members[0]["name"], "Member A")
        self.assertEqual(members[1]["name"], "Member B")
        self.assertEqual(members[0]["philea_info"]["About"], "About content")

class TestPhileaPreprocessor(unittest.TestCase):
    
    def test_extract_tags_normalization(self):
        members = [
            {
                "name": "Org A",
                "philea_info": {
                    # Needs normalization "Arts and Culture" -> "Arts & Culture"
                    "Programme Areas": "Arts and Culture, and some other text"
                }
            }
        ]
        extract_tags(members)
        self.assertEqual(members[0]["tags_focus"], ["Arts & Culture"])

    def test_extract_tags_fallback(self):
        members = [
            {
                "name": "Org B",
                "philea_info": {
                    # No master tags, must fall back to keywords
                    "Programme Areas": "We focus on learnings and schools for young students"
                }
            }
        ]
        extract_tags(members)
        self.assertEqual(members[0]["tags_focus"], ["Education"])

    def test_extract_tags_special_boundary(self):
        members = [
            {
                "name": "Org C",
                "philea_info": {
                    # Test boundary logic for special symbol "+" in lgbti+
                    "Programme Areas": "advocating for lgbti+ rights in society"
                }
            }
        ]
        extract_tags(members)
        self.assertEqual(members[0]["tags_focus"], ["Human/Civil Rights"])

    def test_extract_geo_taxonomy_matching(self):
        members = [
            {
                "name": "Org D",
                "philea_info": {
                    # Matching country name "Denmark" and macro region mapping
                    "Geographic Focus": "We work primarily in Denmark and Norway."
                }
            }
        ]
        extract_geo(members)
        # Verify Denmark and Norway map to "Europe (Nordic Region)"
        self.assertIn("Europe (Nordic Region)", members[0]["geo_locations"])
        self.assertEqual(
            sorted(members[0]["geo_locations"]["Europe (Nordic Region)"]),
            ["Denmark", "Norway"]
        )

    def test_extract_geo_abbreviation_and_alternation(self):
        members = [
            {
                "name": "Org E",
                "philea_info": {
                    # London and English are alternatives for United Kingdom
                    "Geographic Focus": "Our office is in London, doing english research."
                }
            }
        ]
        extract_geo(members)
        self.assertIn("Europe (Western / General)", members[0]["geo_locations"])
        self.assertIn("United Kingdom", members[0]["geo_locations"]["Europe (Western / General)"])

    def test_extract_geo_substring_safety(self):
        members = [
            {
                "name": "Org F",
                "philea_info": {
                    # Ukraine should NOT match UK
                    "Geographic Focus": "Supporting human rights for Ukraine."
                }
            }
        ]
        extract_geo(members)
        
        # Verify Ukraine is matched under Central & Eastern Europe
        self.assertIn("Europe (Central & Eastern / Balkans)", members[0]["geo_locations"])
        self.assertIn("Ukraine", members[0]["geo_locations"]["Europe (Central & Eastern / Balkans)"])
        
        # Verify UK / United Kingdom is NOT matched
        west_europe = members[0]["geo_locations"].get("Europe (Western / General)", [])
        self.assertNotIn("United Kingdom", west_europe)

    def test_extract_geo_fallback(self):
        members = [
            {
                "name": "The Social Change Nest",
                "address": "Albert House, 256-260 Old St, London EC1V 9DD, UK",
                "philea_info": {
                    # Geographic Focus doesn't mention any locations, but About and address do.
                    "Geographic Focus": "We tear down the barriers that prevent communities from creating change.",
                    "About": "Helping groups in the UK.",
                }
            }
        ]
        extract_geo(members)
        self.assertIn("Europe (Western / General)", members[0]["geo_locations"])
        self.assertIn("United Kingdom", members[0]["geo_locations"]["Europe (Western / General)"])

if __name__ == "__main__":
    unittest.main()

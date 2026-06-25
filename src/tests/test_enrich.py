import unittest
from unittest.mock import patch, MagicMock
import os
import json
import sys

# Ensure src directory is in sys.path so we can import preprocessing
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(TESTS_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from preprocessing.enrich_gemini import enrich_organizations, FunderEnrichment

class TestEnrichGemini(unittest.TestCase):
    
    def setUp(self):
        # Sample members
        self.members = [
            {
                "name": "Funder A",
                "website": "https://fundera.org",
                "address": "123 Street",
                "country": "Germany",
                "funding_info": {}
            },
            {
                "name": "Funder B",
                "website": "https://funderb.org",
                "address": "456 Avenue",
                "country": "France",
                "funding_info": {
                    "annual_giving": "€500,000 (2024)",
                    "average_grant": "€10,000",
                    "grant_range": "€5,000 - €20,000",
                    "funding_model": "Open applications",
                    "application_details": "Apply online",
                    "sources": ["https://funderb.org/grants"]
                }
            }
        ]

    @patch("preprocessing.enrich_gemini.genai.Client")
    def test_enrich_organizations_success(self, mock_client_class):
        # Set up mock client and responses
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_research_response = MagicMock()
        mock_research_response.text = "Some research text about Funder A"
        
        mock_parse_response = MagicMock()
        mock_parse_response.text = json.dumps({
            "annual_giving": "€1,000,000 (2025)",
            "average_grant": "€20,000",
            "grant_range": "€10,000 - €50,000",
            "funding_model": "Invitation only",
            "application_details": "No open calls",
            "sources": ["https://fundera.org/about"]
        })
        mock_client.models.generate_content.side_effect = [mock_research_response, mock_parse_response]
        
        # We pass api_key explicitly so it doesn't fail on environment check
        res = enrich_organizations(self.members, api_key="dummy_key", sleep_time=0.0)
        
        # Verify first was enriched
        self.assertEqual(res[0]["funding_info"]["annual_giving"], "€1,000,000 (2025)")
        self.assertEqual(res[0]["funding_info"]["funding_model"], "Invitation only")
        self.assertEqual(res[0]["funding_info"]["sources"], ["https://fundera.org/about"])
        
        # Verify second was skipped (resume mode)
        self.assertEqual(res[1]["funding_info"]["annual_giving"], "€500,000 (2024)")
        self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("preprocessing.enrich_gemini.genai.Client")
    def test_enrich_organizations_api_error(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_client.models.generate_content.side_effect = Exception("API Quota Exceeded")
        
        res = enrich_organizations(self.members, api_key="dummy_key", sleep_time=0.0)
        
        # Verify empty/fallback fields populated on error
        self.assertEqual(res[0]["funding_info"]["annual_giving"], "")
        self.assertEqual(res[0]["funding_info"]["average_grant"], "")
        self.assertEqual(res[0]["funding_info"]["sources"], [])

    def test_enrich_organizations_missing_key(self):
        # Temporarily clear env var
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                enrich_organizations(self.members, api_key=None)

    @patch("preprocessing.enrich_gemini.genai.Client")
    def test_enrich_organizations_incremental_save(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_research_response = MagicMock()
        mock_research_response.text = "Some research text"
        
        mock_parse_response = MagicMock()
        mock_parse_response.text = json.dumps({
            "annual_giving": "€1,000,000",
            "average_grant": "€20,000",
            "grant_range": "€10,000 - €50,000",
            "funding_model": "Invitation only",
            "application_details": "No open calls",
            "sources": []
        })
        mock_client.models.generate_content.side_effect = [mock_research_response, mock_parse_response]
        
        mock_save_fn = MagicMock()
        
        enrich_organizations(
            self.members,
            api_key="dummy_key",
            sleep_time=0.0,
            save_path="dummy_path.json",
            save_fn=mock_save_fn
        )
        
        # Verify save_fn was called with updated list
        mock_save_fn.assert_called_once_with(self.members, "dummy_path.json")

    def test_extract_number(self):
        from preprocessing.enrich_gemini import _extract_number
        self.assertEqual(_extract_number("EUR 6,550,184 (2024)"), 6550184.0)
        self.assertEqual(_extract_number("EUR 11,696.76 (2024)"), 11696.76)
        self.assertEqual(_extract_number("626,000 CHF (2022)"), 626000.0)
        self.assertEqual(_extract_number("€10.000"), 10000.0)
        self.assertEqual(_extract_number("€12,50"), 12.5)
        self.assertEqual(_extract_number("£4.24 million"), 4240000.0)
        self.assertEqual(_extract_number("€10m"), 10000000.0)
        self.assertEqual(_extract_number("1.5 billion"), 1500000000.0)
        self.assertEqual(_extract_number("50k"), 50000.0)
        self.assertIsNone(_extract_number("Not publicly available"))

    @patch("preprocessing.enrich_gemini.genai.Client")
    def test_enrich_organizations_plausibility_check_failed(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_research_response = MagicMock()
        mock_research_response.text = "Some research text"
        
        # Mock responses where average_grant is greater than annual_giving
        mock_parse_response = MagicMock()
        mock_parse_response.text = json.dumps({
            "annual_giving": "€50,000",
            "average_grant": "€100,000",  # Fails plausibility check (avg > annual)
            "grant_range": "€10,000 - €100,000",
            "funding_model": "Open applications",
            "application_details": "No open calls",
            "sources": []
        })
        mock_client.models.generate_content.side_effect = [mock_research_response, mock_parse_response]
        
        res = enrich_organizations(self.members, api_key="dummy_key", sleep_time=0.0)
        
        # Verify first was enriched and average_grant was reset to fallback
        self.assertEqual(res[0]["funding_info"]["annual_giving"], "€50,000")
        self.assertEqual(res[0]["funding_info"]["average_grant"], "Not publicly available")

    @patch("preprocessing.enrich_gemini.genai.Client")
    def test_enrich_organizations_empty_research_response(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        # Stage 1 returns empty content
        mock_research_response = MagicMock()
        mock_research_response.text = ""
        mock_client.models.generate_content.return_value = mock_research_response
        
        res = enrich_organizations(self.members, api_key="dummy_key", sleep_time=0.0)
        
        # Verify first was handled and populated with fallback
        self.assertEqual(res[0]["funding_info"]["annual_giving"], "")
        # Verify generate_content was only called ONCE (for research), NOT twice
        self.assertEqual(mock_client.models.generate_content.call_count, 1)

    @patch("preprocessing.enrich_gemini.time.sleep")
    @patch("preprocessing.enrich_gemini.genai.Client")
    def test_enrich_organizations_retry_logic(self, mock_client_class, mock_sleep):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_research_response = MagicMock()
        mock_research_response.text = "Some research text"
        
        mock_parse_response = MagicMock()
        mock_parse_response.text = json.dumps({
            "annual_giving": "€50,000",
            "average_grant": "€5,000",
            "grant_range": "€1,000 - €10,000",
            "funding_model": "Open applications",
            "application_details": "No open calls",
            "sources": []
        })
        
        # Raise an exception on first call, succeed on subsequent calls
        mock_client.models.generate_content.side_effect = [
            Exception("Quota limit hit"),
            mock_research_response,
            mock_parse_response
        ]
        
        res = enrich_organizations(self.members, api_key="dummy_key", sleep_time=0.0)
        
        # Verify first was successfully enriched despite first attempt failing
        self.assertEqual(res[0]["funding_info"]["annual_giving"], "€50,000")
        self.assertEqual(mock_sleep.call_count, 1) # Retried once

    def test_ensure_eur_helpers(self):
        from preprocessing.enrich_gemini import _ensure_eur, _ensure_eur_range
        
        # USD conversion
        self.assertEqual(_ensure_eur("$10,000"), "€9,200 (converted from USD)")
        self.assertEqual(_ensure_eur("$10,000 (2024)"), "€9,200 (2024) (converted from USD)")
        
        # GBP conversion
        self.assertEqual(_ensure_eur("£10,000"), "€12,000 (converted from GBP)")
        self.assertEqual(_ensure_eur("1,000 GBP"), "€1,200 (converted from GBP)")
        
        # CHF conversion
        self.assertEqual(_ensure_eur("1,000 CHF"), "€1,050 (converted from CHF)")
        
        # Normal inputs preserved
        self.assertEqual(_ensure_eur("€50,000"), "€50,000")
        self.assertEqual(_ensure_eur("Not publicly available"), "Not publicly available")
        
        # Range inputs
        self.assertEqual(_ensure_eur_range("£5,000 - £10,000"), "€6,000 - €12,000 (converted from GBP)")
        self.assertEqual(_ensure_eur_range("$5,000 to $10,000"), "€4,600 - €9,200 (converted from USD)")
        self.assertEqual(_ensure_eur_range("€5,000 - €10,000"), "€5,000 - €10,000")

    @patch("preprocessing.enrich_gemini.genai.Client")
    def test_enrich_organizations_advanced_plausibility_checks(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_research_response = MagicMock()
        mock_research_response.text = "Some research text"
        
        # Case 1: Out of order range (re-ordered), average outside range (reset average)
        mock_parse_response_1 = MagicMock()
        mock_parse_response_1.text = json.dumps({
            "annual_giving": "€50,000",
            "average_grant": "€25,000",          # Outside re-ordered range [1,000, 10,000]
            "grant_range": "€10,000 - €1,000",   # Out of order
            "funding_model": "Open applications",
            "application_details": "No open calls",
            "sources": []
        })
        
        mock_client.models.generate_content.side_effect = [mock_research_response, mock_parse_response_1]
        res = enrich_organizations(self.members[:1], api_key="dummy_key", sleep_time=0.0)
        self.assertEqual(res[0]["funding_info"]["grant_range"], "€1,000 - €10,000")
        self.assertEqual(res[0]["funding_info"]["average_grant"], "Not publicly available")

        # Reset mock
        self.members[0]["funding_info"] = {}
        
        # Case 2: Max grant exceeds annual giving
        mock_parse_response_2 = MagicMock()
        mock_parse_response_2.text = json.dumps({
            "annual_giving": "€50,000",
            "average_grant": "€5,000",
            "grant_range": "€1,000 - €100,000",  # Max grant (100k) > annual giving (50k)
            "funding_model": "Open applications",
            "application_details": "No open calls",
            "sources": []
        })
        
        mock_client.models.generate_content.side_effect = [mock_research_response, mock_parse_response_2]
        res = enrich_organizations(self.members[:1], api_key="dummy_key", sleep_time=0.0)
        self.assertEqual(res[0]["funding_info"]["grant_range"], "Not publicly available")

if __name__ == "__main__":
    unittest.main()


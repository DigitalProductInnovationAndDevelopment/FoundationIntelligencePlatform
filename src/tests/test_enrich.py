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
                "position": {"country": "Germany"},
                "philea_info": {}
            },
            {
                "name": "Funder B",
                "website": "https://funderb.org",
                "address": "456 Avenue",
                "position": {"country": "France"},
                "philea_info": {
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
        self.assertEqual(res[0]["philea_info"]["annual_giving"], "€1,000,000 (2025)")
        self.assertEqual(res[0]["philea_info"]["funding_model"], "Invitation only")
        self.assertEqual(res[0]["philea_info"]["sources"], ["https://fundera.org/about"])
        
        # Verify second was skipped (resume mode)
        self.assertEqual(res[1]["philea_info"]["annual_giving"], "€500,000 (2024)")
        self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("preprocessing.enrich_gemini.genai.Client")
    def test_enrich_organizations_api_error(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_client.models.generate_content.side_effect = Exception("API Quota Exceeded")
        
        res = enrich_organizations(self.members, api_key="dummy_key", sleep_time=0.0)
        
        # Verify empty/fallback fields populated on error
        self.assertEqual(res[0]["philea_info"]["annual_giving"], "")
        self.assertEqual(res[0]["philea_info"]["average_grant"], "")
        self.assertEqual(res[0]["philea_info"]["sources"], [])

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
        self.assertEqual(res[0]["philea_info"]["annual_giving"], "€50,000")
        self.assertEqual(res[0]["philea_info"]["average_grant"], "Not publicly available")

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
        self.assertEqual(res[0]["philea_info"]["annual_giving"], "")
        # Verify generate_content was only called ONCE (for research), NOT twice
        self.assertEqual(mock_client.models.generate_content.call_count, 1)

if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import requests

# Add project root to sys.path so we can import scrapers
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from scrapers.register_of_charities import (
    CharityCommissionAPI,
    get_all_charity_details,
    get_charity_assets_liabilities,
    get_check_primary_grants,
    get_charity_who_what_how,
    get_charity_financial_history,
    search_charity_name,
    scrape
)

class TestCharityCommissionAPI(unittest.TestCase):
    
    @patch("scrapers.register_of_charities.requests.Session.request")
    def test_api_key_header(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_request.return_value = mock_resp
        
        # Instantiate with api_key
        api = CharityCommissionAPI(api_key="test-secret-key")
        api.all_charity_details(12345, 0)
        
        # Check that request was called with key in session headers
        self.assertEqual(api.session.headers.get("Ocp-Apim-Subscription-Key"), "test-secret-key")

    @patch("scrapers.register_of_charities.requests.Session.request")
    def test_all_charity_details_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"charity_name": "Test Charity"}
        mock_request.return_value = mock_resp
        
        details = get_all_charity_details(12345, 0, api_key="dummy")
        self.assertEqual(details, {"charity_name": "Test Charity"})
        
    @patch("scrapers.register_of_charities.requests.Session.request")
    def test_charity_assets_liabilities_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"assets": 1000}
        mock_request.return_value = mock_resp
        
        res = get_charity_assets_liabilities(12345, 0, api_key="dummy")
        self.assertEqual(res, {"assets": 1000})

    @patch("scrapers.register_of_charities.requests.Session.request")
    def test_check_primary_grants_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"grants": []}
        mock_request.return_value = mock_resp
        
        res = get_check_primary_grants(12345, 0, api_key="dummy")
        self.assertEqual(res, {"grants": []})

    @patch("scrapers.register_of_charities.requests.Session.request")
    def test_charity_who_what_how_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"activities": "helping people"}
        mock_request.return_value = mock_resp
        
        res = get_charity_who_what_how(12345, 0, api_key="dummy")
        self.assertEqual(res, {"activities": "helping people"})

    @patch("scrapers.register_of_charities.requests.Session.request")
    def test_charity_financial_history_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"year": 2023}]
        mock_request.return_value = mock_resp
        
        res = get_charity_financial_history(12345, 0, api_key="dummy")
        self.assertEqual(res, [{"year": 2023}])

    @patch("scrapers.register_of_charities.requests.Session.request")
    def test_search_charity_name_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"reg_charity_number": 12345, "group_subsid_suffix": 0}]
        mock_request.return_value = mock_resp
        
        res = search_charity_name("Test", api_key="dummy")
        self.assertEqual(res, [{"reg_charity_number": 12345, "group_subsid_suffix": 0}])

    @patch("scrapers.register_of_charities.requests.Session.request")
    def test_all_charity_details_not_found(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_request.return_value = mock_resp
        
        details = get_all_charity_details(12345, 0, api_key="dummy")
        self.assertIsNone(details)

    @patch("scrapers.register_of_charities.requests.Session.request")
    @patch("scrapers.register_of_charities.time.sleep")
    def test_scrape_by_registered_numbers(self, mock_sleep, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = [
            {"name": "Details"},      # all_charity_details
            {"assets": 100},          # assets_liabilities
            {"grants": ["A"]},       # check_primary_grants
            {"who": "what"},          # charity_who_what_how
            {"history": []}           # charity_financial_history
        ]
        mock_request.return_value = mock_resp
        
        results = scrape(registered_numbers=[12345], sleep_time=0.01, api_key="dummy")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["registered_charity_number"], 12345)
        self.assertEqual(results[0]["all_details"], {"name": "Details"})
        self.assertEqual(results[0]["assets_liabilities"], {"assets": 100})
        self.assertEqual(results[0]["primary_grants"], {"grants": ["A"]})
        self.assertEqual(results[0]["who_what_how"], {"who": "what"})
        self.assertEqual(results[0]["financial_history"], {"history": []})

    @patch("scrapers.register_of_charities.requests.Session.request")
    @patch("scrapers.register_of_charities.time.sleep")
    def test_scrape_by_search_name(self, mock_sleep, mock_request):
        mock_resp_search = MagicMock()
        mock_resp_search.status_code = 200
        mock_resp_search.json.return_value = [{"reg_charity_number": 12345, "group_subsid_suffix": 0}]

        mock_resp_details = MagicMock()
        mock_resp_details.status_code = 200
        mock_resp_details.json.side_effect = [
            {"name": "Details"},      # all_charity_details
            {"assets": 100},          # assets_liabilities
            {"grants": []},           # check_primary_grants
            {"who": "what"},          # charity_who_what_how
            {"history": []}           # charity_financial_history
        ]
        
        mock_request.side_effect = [mock_resp_search, mock_resp_details, mock_resp_details, mock_resp_details, mock_resp_details, mock_resp_details]
        
        results = scrape(search_name="Test", sleep_time=0.01, api_key="dummy")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["registered_charity_number"], 12345)
        self.assertEqual(results[0]["all_details"], {"name": "Details"})

if __name__ == "__main__":
    unittest.main()

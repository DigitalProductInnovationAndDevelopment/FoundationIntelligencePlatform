import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import requests

import importlib

# Add project root to sys.path so we can import scrapers
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

giving = importlib.import_module("scrapers.360giving")
ThreeSixtyGivingAPI = giving.ThreeSixtyGivingAPI
get_organisations = giving.get_organisations
get_organisation_detail = giving.get_organisation_detail
get_grants_made = giving.get_grants_made
get_grants_received = giving.get_grants_received
scrape = giving.scrape

class TestThreeSixtyGivingAPI(unittest.TestCase):

    @patch.object(giving.requests.Session, "request")
    def test_user_agent_header(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_request.return_value = mock_resp
        
        api = ThreeSixtyGivingAPI(user_agent="CustomAgent")
        api.get_organisation_detail("GB-CHC-1164883")
        
        self.assertEqual(api.session.headers.get("User-Agent"), "CustomAgent")

    @patch.object(giving.requests.Session, "request")
    def test_get_organisations_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "count": 2,
            "results": [
                {"org_id": "ORG-1", "name": "Org 1"},
                {"org_id": "ORG-2", "name": "Org 2"}
            ]
        }
        mock_request.return_value = mock_resp
        
        res = get_organisations(limit=10, offset=0)
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["results"][0]["org_id"], "ORG-1")

    @patch.object(giving.requests.Session, "request")
    def test_get_organisation_detail_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "org_id": "ORG-1",
            "name": "Org 1",
            "funder": None,
            "recipient": {"aggregate": {"grants": 29}}
        }
        mock_request.return_value = mock_resp
        
        res = get_organisation_detail("ORG-1")
        self.assertEqual(res["org_id"], "ORG-1")
        self.assertEqual(res["recipient"]["aggregate"]["grants"], 29)

    @patch.object(giving.requests.Session, "request")
    def test_get_organisation_detail_not_found(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_request.return_value = mock_resp
        
        res = get_organisation_detail("INVALID")
        self.assertIsNone(res)

    @patch.object(giving.requests.Session, "request")
    def test_get_grants_made_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "count": 1,
            "results": [{"grant_id": "GRANT-1", "amountAwarded": 1000}]
        }
        mock_request.return_value = mock_resp
        
        res = get_grants_made("ORG-1", limit=10, offset=0)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["grant_id"], "GRANT-1")

    @patch.object(giving.requests.Session, "request")
    def test_get_grants_received_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "count": 1,
            "results": [{"grant_id": "GRANT-2", "amountAwarded": 2000}]
        }
        mock_request.return_value = mock_resp
        
        res = get_grants_received("ORG-2", limit=10, offset=0)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["grant_id"], "GRANT-2")

    @patch.object(giving.requests.Session, "request")
    def test_iter_organisations_pagination(self, mock_request):
        mock_resp_page1 = MagicMock()
        mock_resp_page1.status_code = 200
        mock_resp_page1.json.return_value = {
            "next": "https://api.threesixtygiving.org/v1/org/?limit=2&offset=2",
            "results": [
                {"org_id": "ORG-1", "name": "Org 1"},
                {"org_id": "ORG-2", "name": "Org 2"}
            ]
        }
        
        mock_resp_page2 = MagicMock()
        mock_resp_page2.status_code = 200
        mock_resp_page2.json.return_value = {
            "next": None,
            "results": [
                {"org_id": "ORG-3", "name": "Org 3"}
            ]
        }
        
        mock_request.side_effect = [mock_resp_page1, mock_resp_page2]
        
        api = ThreeSixtyGivingAPI()
        orgs = list(api.iter_organisations(limit=2))
        
        self.assertEqual(len(orgs), 3)
        self.assertEqual(orgs[0]["org_id"], "ORG-1")
        self.assertEqual(orgs[2]["org_id"], "ORG-3")

    @patch.object(giving.requests.Session, "request")
    def test_iter_grants_made_pagination(self, mock_request):
        mock_resp_page1 = MagicMock()
        mock_resp_page1.status_code = 200
        mock_resp_page1.json.return_value = {
            "next": "https://api.threesixtygiving.org/v1/org/ORG-1/grants_made/?limit=1&offset=1",
            "results": [
                {"grant_id": "GRANT-1"}
            ]
        }
        
        mock_resp_page2 = MagicMock()
        mock_resp_page2.status_code = 200
        mock_resp_page2.json.return_value = {
            "next": None,
            "results": [
                {"grant_id": "GRANT-2"}
            ]
        }
        
        mock_request.side_effect = [mock_resp_page1, mock_resp_page2]
        
        api = ThreeSixtyGivingAPI()
        grants = list(api.iter_grants_made("ORG-1", limit=1))
        
        self.assertEqual(len(grants), 2)
        self.assertEqual(grants[0]["grant_id"], "GRANT-1")
        self.assertEqual(grants[1]["grant_id"], "GRANT-2")

    @patch.object(giving.requests.Session, "request")
    def test_iter_grants_received_pagination(self, mock_request):
        mock_resp_page1 = MagicMock()
        mock_resp_page1.status_code = 200
        mock_resp_page1.json.return_value = {
            "next": "https://api.threesixtygiving.org/v1/org/ORG-2/grants_received/?limit=1&offset=1",
            "results": [
                {"grant_id": "GRANT-1"}
            ]
        }
        
        mock_resp_page2 = MagicMock()
        mock_resp_page2.status_code = 200
        mock_resp_page2.json.return_value = {
            "next": None,
            "results": [
                {"grant_id": "GRANT-2"}
            ]
        }
        
        mock_request.side_effect = [mock_resp_page1, mock_resp_page2]
        
        api = ThreeSixtyGivingAPI()
        grants = list(api.iter_grants_received("ORG-2", limit=1))
        
        self.assertEqual(len(grants), 2)
        self.assertEqual(grants[0]["grant_id"], "GRANT-1")
        self.assertEqual(grants[1]["grant_id"], "GRANT-2")

    @patch.object(giving.requests.Session, "request")
    @patch.object(giving.time, "sleep")
    def test_transient_error_retry(self, mock_sleep, mock_request):
        # Setup mock responses: first transient rate limit (429), then success (200)
        mock_resp_err = MagicMock()
        mock_resp_err.status_code = 429
        
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {"org_id": "ORG-1"}
        
        mock_request.side_effect = [mock_resp_err, mock_resp_ok]
        
        api = ThreeSixtyGivingAPI(max_retries=3, backoff_factor=0.01)
        res = api.get_organisation_detail("ORG-1")
        
        self.assertEqual(mock_request.call_count, 2)
        self.assertEqual(res["org_id"], "ORG-1")
        # Ensure sleep was called for backoff
        mock_sleep.assert_called_once()

    @patch.object(giving.requests.Session, "request")
    @patch.object(giving.time, "sleep")
    def test_persistent_error_throws(self, mock_sleep, mock_request):
        mock_resp_err = MagicMock()
        mock_resp_err.status_code = 500
        mock_resp_err.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        mock_request.return_value = mock_resp_err
        
        api = ThreeSixtyGivingAPI(max_retries=2, backoff_factor=0.01)
        
        with self.assertRaises(requests.exceptions.HTTPError):
            api.get_organisation_detail("ORG-1")

    @patch.object(giving.requests.Session, "request")
    @patch.object(giving.time, "sleep")
    def test_scrape_by_org_ids_with_grants(self, mock_sleep, mock_request):
        # 1. Detail call, 2. Grants Made, 3. Grants Received
        mock_resp_detail = MagicMock()
        mock_resp_detail.status_code = 200
        mock_resp_detail.json.return_value = {"name": "Org 1"}
        
        mock_resp_grants_made = MagicMock()
        mock_resp_grants_made.status_code = 200
        mock_resp_grants_made.json.return_value = {
            "results": [{"grant_id": "MADE-1"}],
            "next": None
        }
        
        mock_resp_grants_received = MagicMock()
        mock_resp_grants_received.status_code = 200
        mock_resp_grants_received.json.return_value = {
            "results": [{"grant_id": "RCVD-1"}],
            "next": None
        }
        
        mock_request.side_effect = [mock_resp_detail, mock_resp_grants_made, mock_resp_grants_received]
        
        results = scrape(org_ids=["ORG-1"], scrape_grants=True, sleep_time=0.001)
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["org_id"], "ORG-1")
        self.assertEqual(results[0]["detail"]["name"], "Org 1")
        self.assertEqual(len(results[0]["grants_made"]), 1)
        self.assertEqual(results[0]["grants_made"][0]["grant_id"], "MADE-1")
        self.assertEqual(len(results[0]["grants_received"]), 1)
        self.assertEqual(results[0]["grants_received"][0]["grant_id"], "RCVD-1")


if __name__ == "__main__":
    unittest.main()

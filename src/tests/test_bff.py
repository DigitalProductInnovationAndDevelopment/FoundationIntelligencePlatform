import unittest
import os
import tempfile
import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from jose import jwt
import httpx

# Ensure the correct path configuration before imports
import sys
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from bff.main import app
from bff.config import JWT_SECRET_KEY, JWT_ALGORITHM, DATA_PATH
from bff.repositories import JSONCharityRepository, get_charity_repository

class TestBFF(unittest.TestCase):
    def setUp(self):
        # Create a temporary JSON data file for testing repository
        self.test_data = [
            {
                "registered_charity_number": 1001,
                "suffix": 0,
                "link": "http://charity.example.com/1001",
                "all_details": {
                    "organisation_number": 1001,
                    "reg_charity_number": 1001,
                    "group_subsid_suffix": 0,
                    "charity_name": "Active Charity One",
                    "reg_status": "R",
                    "latest_income": 500000.0,
                    "latest_expenditure": 450000.0,
                    "reporting_status": "Registered"
                },
                "assets_liabilities": [
                    {
                        "organisation_number": 1001,
                        "fin_period_end_date": "2025-12-31T00:00:00",
                        "assets_total_liabilities": 20000.0
                    }
                ],
                "financial_history": [
                    {
                        "financial_period_end_date": "2025-12-31T00:00:00",
                        "income": 500000.0,
                        "expenditure": 450000.0
                    }
                ]
            },
            {
                "registered_charity_number": 1002,
                "suffix": 0,
                "link": "http://charity.example.com/1002",
                "all_details": {
                    "organisation_number": 1002,
                    "reg_charity_number": 1002,
                    "group_subsid_suffix": 0,
                    "charity_name": "Removed Charity Two",
                    "reg_status": "RM",
                    "latest_income": None,  # Test fallback to financial history
                    "latest_expenditure": None,
                    "reporting_status": "Removed",
                    "removal_reason": "CEASED TO EXIST"
                },
                "assets_liabilities": [],
                "financial_history": [
                    {
                        "financial_period_end_date": "2024-12-31T00:00:00",
                        "income": 100000.0,
                        "expenditure": 90000.0
                    }
                ]
            }
        ]
        
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.temp_file.write(json.dumps(self.test_data).encode("utf-8"))
        self.temp_file.close()

        # Instantiate repository with temporary file
        self.test_repo = JSONCharityRepository(data_path=self.temp_file.name)
        
        # Override the FastAPI app dependency to inject test repo
        app.dependency_overrides[get_charity_repository] = lambda: self.test_repo
        
        self.client = TestClient(app)

    def tearDown(self):
        # Clean up temporary files and dependency overrides
        os.unlink(self.temp_file.name)
        app.dependency_overrides.clear()

    # --- Authentication Tests ---

    def test_login_success(self):
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "Successfully logged in"})
        self.assertIn("session_id", response.cookies)

    def test_login_invalid_credentials(self):
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrongpassword"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("session_id", response.cookies)

    def test_logout_unauthenticated(self):
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 401)

    def test_logout_success(self):
        # First log in
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")
        
        # Call logout with the cookie
        response = self.client.post(
            "/api/auth/logout",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "Successfully logged out"})
        
        # Verify cookie is deleted (or value is set to empty / expires is past)
        logout_cookie = response.cookies.get("session_id")
        self.assertTrue(logout_cookie is None or logout_cookie == "")

    # --- Charity Endpoints Tests ---

    def test_list_charities_unauthenticated(self):
        response = self.client.get("/api/charities")
        self.assertEqual(response.status_code, 401)

    def test_list_charities_success(self):
        # Login to get cookie
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        # Fetch list
        response = self.client.get(
            "/api/charities",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["charity_name"], "Active Charity One")
        self.assertEqual(data[0]["latest_income"], 500000.0)
        
        # Test fallback financials on second charity
        self.assertEqual(data[1]["charity_name"], "Removed Charity Two")
        self.assertEqual(data[1]["latest_income"], 100000.0)

    def test_list_charities_filters(self):
        # Login
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        # Filter by search
        response = self.client.get(
            "/api/charities?search=Active",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["charity_name"], "Active Charity One")

        # Filter by status
        response = self.client.get(
            "/api/charities?reg_status=RM",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["charity_name"], "Removed Charity Two")

    def test_get_charity_detail_success(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        # Fetch detail of existing charity
        response = self.client.get(
            "/api/charities/1001",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["registered_charity_number"], 1001)
        self.assertEqual(data["all_details"]["charity_name"], "Active Charity One")
        self.assertEqual(len(data["financial_history"]), 1)

    def test_get_charity_detail_not_found(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        response = self.client.get(
            "/api/charities/9999",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 404)

    def test_charity_stats(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        response = self.client.get(
            "/api/charities/stats",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total_charities"], 2)
        self.assertEqual(data["active_charities"], 1)
        self.assertEqual(data["removed_charities"], 1)
        # Average income: (500000 + 100000) / 2 = 300000
        self.assertEqual(data["average_income"], 300000.0)
        # Average expenditure: (450000 + 90000) / 2 = 270000
        self.assertEqual(data["average_expenditure"], 270000.0)

    # --- Downstream Proxy Tests ---

    def test_proxy_unauthenticated(self):
        response = self.client.get("/api/core/users")
        self.assertEqual(response.status_code, 401)

    @patch("httpx.AsyncClient.request")
    def test_proxy_success_translates_cookie_to_bearer(self, mock_request):
        # Create token and login
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        # Mock downstream core response
        mock_downstream_resp = MagicMock()
        mock_downstream_resp.status_code = 200
        mock_downstream_resp.content = b'{"success": true}'
        mock_downstream_resp.headers = {"Content-Type": "application/json"}
        mock_request.return_value = mock_downstream_resp

        # Send request through the proxy
        response = self.client.get(
            "/api/core/v1/data?filter=active",
            cookies={"session_id": session_cookie},
            headers={"X-Test-Header": "yes"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})

        # Verify that mock_request was called correctly
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        
        self.assertEqual(kwargs["method"], "GET")
        # Ensure query string was preserved and path appended to core url
        self.assertTrue(kwargs["url"].endswith("/v1/data?filter=active"))
        # Ensure cookie was stripped and replaced with Bearer Authorization
        headers = kwargs["headers"]
        self.assertNotIn("Cookie", headers)
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("Bearer "))
        # Ensure other request headers were forwarded
        # ASGI lowercases header names, so check case-insensitively
        test_header_val = headers.get("x-test-header") or headers.get("X-Test-Header")
        self.assertEqual(test_header_val, "yes")


    @patch("httpx.AsyncClient.request")
    def test_proxy_downstream_connection_error(self, mock_request):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        # Mock httpx raising a request error (e.g. connection refused)
        mock_request.side_effect = httpx.RequestError("Connection refused")

        response = self.client.get(
            "/api/core/users",
            cookies={"session_id": session_cookie}
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), 
            {"detail": "Downstream core service is currently unavailable or timed out."}
        )

    # --- Health Check and Redirect Tests ---

    def test_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy", "service": "bff"})

    def test_root_redirect(self):
        # We set follow_redirects=False to inspect the 307 redirect itself
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers.get("location"), "/docs")


if __name__ == "__main__":
    unittest.main()

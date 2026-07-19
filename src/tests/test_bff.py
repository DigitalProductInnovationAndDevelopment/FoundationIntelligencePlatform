import unittest
import os
import tempfile
import json
import sqlite3
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

    def test_list_charities_new_filters(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        with patch.object(self.test_repo, "get_all", return_value=[]) as mock_get_all:
            response = self.client.get(
                "/api/charities?tag=Education&region=Europe",
                cookies={"session_id": session_cookie}
            )
            self.assertEqual(response.status_code, 200)
            mock_get_all.assert_called_once_with(
                search=None,
                reg_status=None,
                tag="Education",
                region="Europe",
                skip=0,
                limit=20
            )

    def test_get_grants_map(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        mock_map_data = [{"region": "London", "total_amount_eur": 100000.0, "grants_count": 5}]
        with patch.object(self.test_repo, "get_grants_map", return_value=mock_map_data) as mock_grants_map:
            response = self.client.get(
                "/api/charities/grants/map",
                cookies={"session_id": session_cookie}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), mock_map_data)
            mock_grants_map.assert_called_once()

    def test_get_charity_grants_success(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        response = self.client.get(
            "/api/charities/1001/grants?role=all",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["grant_id"], "MOCK-G1")

    def test_get_pipeline_status_success(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        response = self.client.get(
            "/api/admin/pipeline/status",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)

    @patch("bff.admin.run_pipeline_task")
    def test_trigger_pipeline_success(self, mock_run_task):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        with patch("bff.admin.read_status", return_value={"status": "idle"}):
            response = self.client.post(
                "/api/admin/pipeline/trigger",
                json={"source": "quick_consolidate"},
                cookies={"session_id": session_cookie}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "running")
            mock_run_task.assert_called_once_with("quick_consolidate")

    def test_get_pipeline_logs_success(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data="Line 1\nLine 2\n")):
                response = self.client.get(
                    "/api/admin/pipeline/logs",
                    cookies={"session_id": session_cookie}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"logs": "Line 1\nLine 2\n"})

    def test_get_sankey_data_success(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        response = self.client.get(
            "/api/charities/1001/sankey",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("nodes", data)
        self.assertIn("links", data)
        
        nodes = data["nodes"]
        self.assertTrue(any(n["id"] == "Grants Received" for n in nodes))
        self.assertTrue(any(n["id"] == "Charity" for n in nodes))
        
        links = data["links"]
        self.assertTrue(any(l["source"] == "Grants Received" and l["target"] == "Charity" for l in links))


class TestSQLiteCharityRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # We ALWAYS use a temporary SQLite database for predictable, isolated unit tests.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_charities.db")
        conn = sqlite3.connect(self.db_path)
        import data.db_loader as db_loader
        db_loader.create_tables(conn)
        
        # Insert a predictable seed charity
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO charities (charity_id, name, type, annual_income, annual_expenditure, thematic_focus, geographic_focus, raw_cc_data)
            VALUES (202918, 'Oxfam GB', 'Charity', 400000000.0, 395000000.0, '["Socio-economic Development, Poverty"]', '{"Europe (Western / General)": ["United Kingdom"]}', '{"all_details": {"reg_status": "R", "organisation_number": 202918}}')
        """)
        # Insert a predictable grant
        cursor.execute("""
            INSERT INTO grants (grant_id, funding_charity_id, recipient_name, recipient_charity_id, amount_eur, currency, description, date, recipient_region, tags, geographic_focus)
            VALUES ('G1', 202918, 'Test Recipient', 1002, 10000.0, 'GBP', 'Test Grant', '2024-01-01', 'London', '["Health"]', '{"Europe (Western / General)": ["United Kingdom"]}')
        """)
        conn.commit()
        conn.close()
        self.has_temp = True

        from bff.repositories import SQLiteCharityRepository
        self.repo = SQLiteCharityRepository(self.db_path)

    def tearDown(self):
        if self.has_temp:
            self.temp_dir.cleanup()

    async def test_get_all(self):
        res = await self.repo.get_all(limit=5)
        self.assertTrue(len(res) > 0)
        self.assertEqual(res[0]["registered_charity_number"], 202918)

    async def test_get_all_filters(self):
        res = await self.repo.get_all(search="Oxfam", reg_status="R", tag="Socio-economic Development, Poverty", region="United Kingdom")
        self.assertTrue(len(res) > 0)

    async def test_get_by_id(self):
        res = await self.repo.get_by_id(202918)
        self.assertIsNotNone(res)
        self.assertEqual(res["all_details"]["organisation_number"], 202918)

    async def test_get_by_id_missing(self):
        res = await self.repo.get_by_id(999999)
        self.assertIsNone(res)

    async def test_get_stats(self):
        res = await self.repo.get_stats()
        self.assertTrue(res["total_charities"] > 0)

    async def test_get_grants_map(self):
        res = await self.repo.get_grants_map()
        self.assertIsNotNone(res)

    async def test_get_grants_for_charity(self):
        res = await self.repo.get_grants_for_charity(202918, role="all")
        self.assertIsNotNone(res)
        res_funder = await self.repo.get_grants_for_charity(202918, role="funder")
        self.assertIsNotNone(res_funder)
        res_recipient = await self.repo.get_grants_for_charity(202918, role="recipient")
        self.assertIsNotNone(res_recipient)

    async def test_get_sankey_data(self):
        res = await self.repo.get_sankey_data(202918)
        self.assertIn("nodes", res)
        self.assertIn("links", res)


class TestAdminPipelineExtra(unittest.TestCase):
    def setUp(self):
        self.login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        self.session_cookie = self.login_resp.cookies.get("session_id")

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @patch("subprocess.Popen")
    def test_run_pipeline_task_success(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        
        from bff.admin import run_pipeline_task
        with patch("bff.admin.write_status") as mock_write:
            run_pipeline_task("quick_consolidate")
            mock_write.assert_any_call(status="running", source="quick_consolidate")
            mock_write.assert_any_call(status="success", source="quick_consolidate")

    @patch("subprocess.Popen")
    def test_run_pipeline_task_failure(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc
        
        from bff.admin import run_pipeline_task
        with patch("bff.admin.write_status") as mock_write:
            run_pipeline_task("quick_consolidate")
            mock_write.assert_any_call(status="running", source="quick_consolidate")
            # Inspect keyword arguments dictionary in mock calls
            calls_kwargs = [c.kwargs for c in mock_write.mock_calls]
            self.assertTrue(any(k.get("status") == "failed" for k in calls_kwargs))

    def test_trigger_pipeline_already_running(self):
        with patch("bff.admin.read_status", return_value={"status": "running"}):
            response = self.client.post(
                "/api/admin/pipeline/trigger",
                json={"source": "quick_consolidate"},
                cookies={"session_id": self.session_cookie}
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("already in progress", response.json()["detail"])

    def test_trigger_pipeline_invalid_mode(self):
        response = self.client.post(
            "/api/admin/pipeline/trigger",
            json={"source": "invalid_mode"},
            cookies={"session_id": self.session_cookie}
        )
        self.assertEqual(response.status_code, 400)

    def test_get_pipeline_logs_not_found(self):
        with patch("os.path.exists", return_value=False):
            response = self.client.get(
                "/api/admin/pipeline/logs",
                cookies={"session_id": self.session_cookie}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"logs": "No pipeline runs recorded yet."})

    def test_get_pipeline_logs_error(self):
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", side_effect=IOError("Permission denied")):
                response = self.client.get(
                    "/api/admin/pipeline/logs",
                    cookies={"session_id": self.session_cookie}
                )
                self.assertEqual(response.status_code, 500)

    def test_read_status_error(self):
        from bff.admin import read_status
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", side_effect=IOError("Locked")):
                status_data = read_status()
                self.assertEqual(status_data["status"], "idle")

    def test_write_status_error(self):
        from bff.admin import write_status
        with patch("builtins.open", side_effect=IOError("ReadOnly")):
            write_status("success", "quick_consolidate")


if __name__ == "__main__":
    unittest.main()

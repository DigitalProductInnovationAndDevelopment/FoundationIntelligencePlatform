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
from bff import admin as admin_module

class TestBFF(unittest.TestCase):
    def setUp(self):
        self.admin_temp_dir = tempfile.TemporaryDirectory()
        self.admin_paths = patch.multiple(
            admin_module,
            STATUS_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_status.json"),
            LOCK_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_run.lock"),
            LOG_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_run.log"),
        )
        self.admin_paths.start()
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
        self.admin_paths.stop()
        self.admin_temp_dir.cleanup()

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

    def test_list_charities_can_include_page_score_summaries(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"},
        )
        response = self.client.get(
            "/api/charities?include_score=true",
            cookies={"session_id": login_resp.cookies.get("session_id")},
        )
        self.assertEqual(response.status_code, 200)
        score = response.json()[0]
        self.assertIn("relevance_score", score)
        self.assertIn("score_completeness", score)
        self.assertEqual(score["score_version"], "example-relevance-v2")
        self.assertEqual(score["score_configuration_status"], "experimental")

    def test_directory_score_sort_is_global_before_pagination_and_honours_maximums(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"},
        )
        cookies = {"session_id": login_resp.cookies.get("session_id")}
        first_page = self.client.get(
            "/api/charities?include_score=true&sort=score_desc&limit=1&skip=0",
            cookies=cookies,
        )
        second_page = self.client.get(
            "/api/charities?include_score=true&sort=score_desc&limit=1&skip=1",
            cookies=cookies,
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        first_score = first_page.json()[0]["relevance_score"]
        second_score = second_page.json()[0]["relevance_score"]
        self.assertGreaterEqual(first_score, second_score)

        maximum = self.client.get(
            "/api/charities?max_annual_giving=100000",
            cookies=cookies,
        )
        self.assertEqual(maximum.status_code, 200)
        self.assertEqual([item["charity_name"] for item in maximum.json()], ["Removed Charity Two"])

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
                size=None,
                tags=None,
                foundation_regions=None,
                funding_regions=None,
                sources=None,
                min_annual_giving=None,
                max_annual_giving=None,
                min_avg_grant_size=None,
                max_avg_grant_size=None,
                include_score=False,
                sort="name_asc",
                skip=0,
                limit=20
            )

    def test_list_charities_size_filter(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        with patch.object(self.test_repo, "get_all", return_value=[]) as mock_get_all:
            response = self.client.get(
                "/api/charities?size=medium",
                cookies={"session_id": session_cookie}
            )
            self.assertEqual(response.status_code, 200)
            mock_get_all.assert_called_once_with(
                search=None,
                reg_status=None,
                tag=None,
                region=None,
                size="medium",
                tags=None,
                foundation_regions=None,
                funding_regions=None,
                sources=None,
                min_annual_giving=None,
                max_annual_giving=None,
                min_avg_grant_size=None,
                max_avg_grant_size=None,
                include_score=False,
                sort="name_asc",
                skip=0,
                limit=20
            )

    def test_list_charities_parses_selected_sources(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        with patch.object(self.test_repo, "get_all", return_value=[]) as mock_get_all:
            response = self.client.get(
                "/api/charities?sources=360Giving,Philea",
                cookies={"session_id": session_cookie}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_get_all.call_args.kwargs["sources"], ["360Giving", "Philea"])

    def test_get_grants_map(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        response = self.client.get(
            "/api/charities/grants/map",
            cookies={"session_id": session_cookie}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "transaction_data_unavailable")
        self.assertEqual(data["items"], [])
        self.assertEqual(data["metadata"]["data_mode"], "cached_source_without_transactions")

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
        self.assertEqual(data["status"], "transaction_data_unavailable")
        self.assertEqual(data["grant_count"], 0)
        self.assertEqual(data["grants"], [])

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

    @patch("bff.charity._fast_link_confirmed_profiles")
    def test_enrich_source_funders_is_bounded_and_uses_confirmed_numbers(self, mock_fast_link):
        mock_fast_link.return_value = []
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"},
        )
        session_cookie = login_resp.cookies.get("session_id")

        response = self.client.post(
            "/api/charities/grants/funders/enrich",
            json={"reg_numbers": [1002, 1001, 1001]},
            cookies={"session_id": session_cookie},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["last_run_source"], "directory_enrichment")
        mock_fast_link.assert_called_once_with([1001, 1002])

    def test_enrich_source_funders_rejects_more_than_five_organizations(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"},
        )
        session_cookie = login_resp.cookies.get("session_id")
        response = self.client.post(
            "/api/charities/grants/funders/enrich",
            json={"reg_numbers": [1, 2, 3, 4, 5, 6]},
            cookies={"session_id": session_cookie},
        )
        self.assertEqual(response.status_code, 422)

    @patch("bff.charity._fast_link_confirmed_profiles")
    def test_registry_detail_enrichment_is_single_and_uses_confirmed_number(self, mock_fast_link):
        mock_fast_link.return_value = []
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"},
        )
        session_cookie = login_resp.cookies.get("session_id")

        response = self.client.post(
            "/api/charities/directory/organizations/enrich",
            json={"reg_numbers": [1165944]},
            cookies={"session_id": session_cookie},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["last_run_source"], "registry_enrichment")
        self.assertEqual(response.json()["status"], "success")
        mock_fast_link.assert_called_once_with([1165944])

    def test_registry_detail_enrichment_rejects_more_than_one_organization(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"},
        )
        session_cookie = login_resp.cookies.get("session_id")
        response = self.client.post(
            "/api/charities/directory/organizations/enrich",
            json={"reg_numbers": [1001, 1002]},
            cookies={"session_id": session_cookie},
        )
        self.assertEqual(response.status_code, 400)

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
            mock_run_task.assert_called_once_with("quick_consolidate", None, False, None, None, False, True)

    @patch("bff.admin.run_pipeline_task")
    def test_trigger_pipeline_full_run_success(self, mock_run_task):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")

        with patch("bff.admin.read_status", return_value={"status": "idle"}):
            response = self.client.post(
                "/api/admin/pipeline/trigger",
                json={"source": "full_run", "limit": 10, "fresh": True},
                cookies={"session_id": session_cookie}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "running")
            mock_run_task.assert_called_once_with("full_run", 10, True, None, None, False, True)

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
        self.assertEqual(data["status"], "transaction_data_unavailable")
        self.assertEqual(data["nodes"], [])
        self.assertEqual(data["links"], [])
        self.assertEqual(data["metadata"]["grant_count"], 0)

    def test_experimental_score_endpoint(self):
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        session_cookie = login_resp.cookies.get("session_id")
        response = self.client.post(
            "/api/charities/1001/score",
            json={"target_profile": {
                "programme_areas": ["Education"],
                "geographies": ["United Kingdom"],
                "organization_types": ["charity"],
            }},
            cookies={"session_id": session_cookie},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["configuration_status"], "experimental")
        self.assertEqual(data["score_version"], "example-relevance-v2")
        self.assertTrue(data["not_a_prediction"])
        self.assertIn("components", data)


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
            INSERT INTO charities (
                charity_id, name, type, annual_income, annual_expenditure,
                thematic_focus, geographic_focus, programme_areas_inferred,
                headquarters_country, raw_cc_data
            ) VALUES (
                202918, 'Oxfam GB', 'Charity', 400000000.0, 395000000.0,
                '["Socio-economic Development, Poverty"]',
                '{"Europe (Western / General)": ["United Kingdom"]}',
                '["Socio-economic Development, Poverty"]', 'United Kingdom',
                '{"all_details": {"reg_status": "R", "organisation_number": 202918}}'
            )
        """)
        # Insert a predictable grant
        cursor.execute("""
            INSERT INTO grants (
                grant_id, funding_charity_id, funding_name, funding_org_source_id,
                recipient_name, recipient_charity_id, recipient_org_source_id,
                amount, amount_eur, conversion_status, currency, description, date, beneficiary_geography,
                beneficiary_geography_normalized, tags, source, source_record_id, source_url
            ) VALUES (
                'G1', 202918, 'Oxfam GB', 'GB-CHC-202918',
                'Test Recipient', 1002, 'GB-CHC-1002',
                10000.0, 10000.0, 'ecb_award_date', 'GBP', 'Test Grant', '2024-01-01',
                '[{"name": "United Kingdom", "countryCode": "GB"}]',
                '[{"name": "United Kingdom", "code": "GB", "macro_region": "Europe (Western / General)", "scope": "country"}]',
                '["Health"]', '360Giving', 'G1', 'https://example.test/grants/G1'
            )
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
        self.assertEqual(res["status"], "available")
        self.assertEqual(res["known_geography_count"], 1)
        self.assertEqual(res["items"][0]["region_or_country_code"], "GB")
        self.assertEqual(res["items"][0]["total_amount"], 10000.0)

    async def test_get_grants_for_charity(self):
        res = await self.repo.get_grants_for_charity(202918, role="all")
        self.assertEqual(res["grant_count"], 1)
        self.assertEqual(res["grants"][0]["amount"], 10000.0)
        self.assertEqual(res["grants"][0]["source"], "360Giving")
        res_funder = await self.repo.get_grants_for_charity(202918, role="funder")
        self.assertEqual(res_funder["grant_count"], 1)
        res_recipient = await self.repo.get_grants_for_charity(202918, role="recipient")
        self.assertEqual(res_recipient["grant_count"], 0)

    async def test_get_sankey_data(self):
        res = await self.repo.get_sankey_data(202918)
        self.assertEqual(res["status"], "available")
        self.assertEqual(len(res["links"]), 1)
        self.assertEqual(res["links"][0]["value"], 10000.0)
        self.assertEqual(res["metadata"]["included_grant_count"], 1)

    async def test_automatic_score_uses_eur_grant_values(self):
        automatic = await self.repo.get_score(202918)
        historical = automatic["components"]["historical_grant_size_fit"]
        self.assertEqual(historical["evidence"][0]["currency"], "EUR")
        self.assertEqual(historical["evidence"][0]["observed_average_grant"], 10_000.0)


class TestAdminPipelineExtra(unittest.TestCase):
    def setUp(self):
        self.admin_temp_dir = tempfile.TemporaryDirectory()
        self.admin_paths = patch.multiple(
            admin_module,
            STATUS_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_status.json"),
            LOCK_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_run.lock"),
            LOG_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_run.log"),
        )
        self.admin_paths.start()
        self.login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password"}
        )
        self.session_cookie = self.login_resp.cookies.get("session_id")

    def tearDown(self):
        self.admin_paths.stop()
        self.admin_temp_dir.cleanup()

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
        with patch("bff.admin.claim_pipeline_run", return_value=False):
            response = self.client.post(
                "/api/admin/pipeline/trigger",
                json={"source": "quick_consolidate"},
                cookies={"session_id": self.session_cookie}
            )
            self.assertEqual(response.status_code, 409)
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

    def test_news_summary_endpoint(self):
        from bff.news import Article
        mock_articles = [
            Article(
                title="Netlight Open Source",
                link="http://netlight.com/news1",
                source="Netlight News",
                published="2026-07-20",
                text="Foundations news detail",
                note="Article content"
            )
        ]
        with patch("bff.news.fetch_news_entries", return_value=mock_articles):
            with patch("bff.news.enrich_articles", return_value=mock_articles):
                with patch("bff.news.summarize_with_claude", return_value="This is a summary."):
                    response = self.client.get(
                        "/api/news/Netlight%20Foundation/summary",
                        cookies={"session_id": self.session_cookie}
                    )
                    self.assertEqual(response.status_code, 200)
                    json_data = response.json()
                    self.assertEqual(json_data["foundation"], "Netlight Foundation")
                    self.assertEqual(json_data["summary"], "This is a summary.")
                    self.assertEqual(len(json_data["sources"]), 1)
                    self.assertEqual(json_data["sources"][0]["title"], "Netlight Open Source")

    def test_news_summary_stream_reports_research_stages_and_result(self):
        from bff.news import Article
        mock_articles = [
            Article(
                title="Netlight Open Source",
                link="http://netlight.com/news1",
                source="Netlight News",
                published="2026-07-20",
                text="Foundations news detail",
                note="Article content",
            )
        ]
        with patch("bff.news.fetch_news_entries", return_value=mock_articles):
            with patch("bff.news.enrich_articles", return_value=mock_articles):
                with patch("bff.news.summarize_with_claude", return_value="This is a summary."):
                    response = self.client.get(
                        "/api/news/Netlight%20Foundation/summary/stream",
                        cookies={"session_id": self.session_cookie},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
                    self.assertIn("event: progress", response.text)
                    self.assertIn('"step": "discovering"', response.text)
                    self.assertIn('"step": "reading"', response.text)
                    self.assertIn('"step": "summarizing"', response.text)
                    self.assertIn("event: complete", response.text)
                    self.assertIn('"summary": "This is a summary."', response.text)


if __name__ == "__main__":
    unittest.main()

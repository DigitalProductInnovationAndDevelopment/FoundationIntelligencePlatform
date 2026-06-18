import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Add project root to sys.path so we can import scraper and preprocessing
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from scrapers.hinchilla import (
    parse_rsc_payload,
    resolve_rsc_references,
    parse_quick_stats,
    scrape,
    find_sections_container
)

class TestHinchillaScraper(unittest.TestCase):
    
    def test_parse_rsc_payload_basic(self):
        # A simple text segment and a json segment
        content = "a:T5,hello\n0:[1,2,3]\n"
        blocks = parse_rsc_payload(content)
        
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks["a"]["type"], "text")
        self.assertEqual(blocks["a"]["content"], "hello")
        self.assertEqual(blocks["0"]["type"], "json_or_other")
        self.assertEqual(blocks["0"]["content"], "[1,2,3]")

    def test_parse_rsc_payload_newline_in_text(self):
        # A text segment containing newlines, followed immediately by another segment
        content = "d:Tb,line1\nline2e:T4,test"
        blocks = parse_rsc_payload(content)
        
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks["d"]["type"], "text")
        self.assertEqual(blocks["d"]["content"], "line1\nline2")
        self.assertEqual(blocks["e"]["type"], "text")
        self.assertEqual(blocks["e"]["content"], "test")

    def test_resolve_rsc_references(self):
        blocks = {
            "d": {"type": "text", "content": "Funding Details Text"},
            "e": {"type": "text", "content": "Trustees List"},
            "json_ref": {"type": "json_or_other", "content": '{"nested": "value"}'}
        }
        
        # Test basic list containing references
        entry_json = '[["$", "$Lc", null, {"sections": [{"title": "Funding", "content": "$d"}, {"title": "Gov", "content": "$e"}, {"title": "Extra", "content": "$json_ref"}]}]]'
        resolved = resolve_rsc_references(blocks, entry_json)
        
        self.assertIsNotNone(resolved)
        sections = resolved[0][3]["sections"]
        self.assertEqual(sections[0]["content"], "Funding Details Text")
        self.assertEqual(sections[1]["content"], "Trustees List")
        self.assertEqual(sections[2]["content"], {"nested": "value"})

    def test_find_sections_container(self):
        resolved_data = [
            ["$", "script", None, {}],
            ["$", "$Lc", None, {
                "data": {"name": "Three Peas", "areaOfOperation": "Greece"},
                "sections": [{"title": "Overview", "content": "About us"}]
            }]
        ]
        
        container = find_sections_container(resolved_data)
        self.assertIsNotNone(container)
        self.assertEqual(container["data"]["name"], "Three Peas")
        self.assertEqual(container["sections"][0]["content"], "About us")

    def test_parse_quick_stats(self):
        stats_text = """
- **Annual Giving**: £90,636 (year ending Sept 2024)
- **Success Rate**: Not applicable - no public application process
- **Decision Time**: Not applicable - relationship-based funding
- **Grant Range**: Variable - based on project needs
- **Funding Model**: Partnership-based, no open applications
"""
        stats = parse_quick_stats(stats_text)
        self.assertEqual(stats.get("Annual Giving"), "£90,636 (year ending Sept 2024)")
        self.assertEqual(stats.get("Success Rate"), "Not applicable - no public application process")
        self.assertEqual(stats.get("Decision Time"), "Not applicable - relationship-based funding")
        self.assertEqual(stats.get("Grant Range"), "Variable - based on project needs")
        self.assertEqual(stats.get("Funding Model"), "Partnership-based, no open applications")

    def test_parse_quick_stats_table(self):
        stats_text = """
| Metric | Value |
|--------|-------|
| Annual Giving | £83,629 (2023-24) |
| Grant Range | £4,000 - £10,000 |
| Average Grant | £6,000 |
| Application Method | No Public Process |
| Geographic Focus | Dorset, Purbeck, Swanage |
"""
        stats = parse_quick_stats(stats_text)
        self.assertEqual(stats.get("Annual Giving"), "£83,629 (2023-24)")
        self.assertEqual(stats.get("Grant Range"), "£4,000 - £10,000")
        self.assertEqual(stats.get("Average Grant"), "£6,000")
        self.assertEqual(stats.get("Application Method"), "No Public Process")
        self.assertEqual(stats.get("Geographic Focus"), "Dorset, Purbeck, Swanage")

    @patch("scrapers.hinchilla.make_request")
    def test_scrape_limited(self, mock_make_request):
        # Mock responses
        # 1. Main Directory HTML response
        mock_dir_resp = MagicMock()
        mock_dir_resp.status_code = 200
        mock_dir_resp.content = b"""
        <html>
            <body>
                <a href="/funder-directory/three-peas">Three Peas</a>
                <a href="/funder-directory/choose-love">Choose Love</a>
            </body>
        </html>
        """
        
        # 2. Detail Page RSC response (as text)
        mock_detail_resp = MagicMock()
        mock_detail_resp.status_code = 200
        mock_detail_resp.text = """
        d:T17,Funding Priorities Text
        9:[["$", "$Lc", null, {"data": {"name": "Three Peas", "areaOfOperation": "Greece, Czechia", "expenditure": "90000", "website": "https://threepeas.org"}, "sections": [{"title": "Overview", "content": "About Three Peas"}, {"title": "Funding Priorities", "content": "$d"}, {"title": "Quick Stats", "content": "- **Annual Giving**: 90k"}]}]]
        """
        
        mock_make_request.side_effect = [mock_dir_resp, mock_detail_resp, mock_detail_resp]
        
        members = scrape(limit=1, sleep_time=0)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["name"], "Three Peas")
        self.assertEqual(members[0]["link"], "https://www.hinchilla.com/funder-directory/three-peas")
        
        philea_info = members[0]["philea_info"]
        self.assertEqual(philea_info["About"], "About Three Peas")
        self.assertEqual(philea_info["Programme Areas"], "Funding Priorities Text")
        self.assertIn("Greece, Czechia", philea_info["Geographic Focus"])
        self.assertEqual(philea_info["website"], "https://threepeas.org")
        self.assertEqual(philea_info["annual_giving"], "90k")

if __name__ == "__main__":
    unittest.main()

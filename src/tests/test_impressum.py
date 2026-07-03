import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Ensure src directory is in sys.path
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(TESTS_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from preprocessing.extract_impressum import (
    get_base_domain,
    is_internal_link,
    extract_links,
    extract_generic_emails,
    extract_physical_address,
    crawl_impressum
)

class TestImpressumExtractor(unittest.TestCase):

    def test_get_base_domain(self):
        self.assertEqual(get_base_domain("https://example-foundation.org/about"), "example-foundation.org")
        self.assertEqual(get_base_domain("http://www.sub.example.com"), "sub.example.com")
        self.assertEqual(get_base_domain("invalid-url"), "")

    def test_is_internal_link(self):
        base = "https://example-foundation.org"
        self.assertTrue(is_internal_link(base, "https://example-foundation.org/impressum"))
        self.assertTrue(is_internal_link(base, "/contact"))
        self.assertFalse(is_internal_link(base, "https://google.com"))
        self.assertFalse(is_internal_link(base, "https://other-foundation.org/about"))

    def test_extract_links_prioritization(self):
        base_url = "https://example.org"
        html = """
        <html>
            <a href="/impressum">Legal Notice</a>
            <a href="/about-us">About Us</a>
            <a href="/kontakt">Contact</a>
            <a href="/some-project">Project Page</a>
            <a href="https://external.com">External Link</a>
        </html>
        """
        links = extract_links(base_url, html)
        
        # Verify that external link is ignored and internal links are prioritized
        urls = [item["url"] for item in links]
        self.assertIn("https://example.org/impressum", urls)
        self.assertIn("https://example.org/about-us", urls)
        self.assertIn("https://example.org/kontakt", urls)
        self.assertNotIn("https://external.com", urls)
        
        # Priority check: legal (impressum) = 1, contact = 2, about = 3, other = 4
        self.assertEqual(links[0]["priority"], 1)  # impressum
        self.assertEqual(links[1]["priority"], 2)  # kontakt
        self.assertEqual(links[2]["priority"], 3)  # about-us
        self.assertEqual(links[3]["priority"], 4)  # some-project

    def test_extract_generic_emails(self):
        text = """
        Reach out to us:
        info@example-foundation.org (should be whitelisted)
        kontakt@example-foundation.org (should be whitelisted)
        john.doe@example-foundation.org (should be ignored due to dot in name)
        m.mueller@example-foundation.org (should be ignored due to dot)
        president@example-foundation.org (should be ignored as not whitelisted)
        office@example-foundation.org (should be whitelisted)
        """
        emails = extract_generic_emails(text)
        self.assertIn("info@example-foundation.org", emails)
        self.assertIn("kontakt@example-foundation.org", emails)
        self.assertIn("office@example-foundation.org", emails)
        
        self.assertNotIn("john.doe@example-foundation.org", emails)
        self.assertNotIn("m.mueller@example-foundation.org", emails)
        self.assertNotIn("president@example-foundation.org", emails)

    def test_extract_physical_address(self):
        # Case 1: Multi-line address with country
        text_multiline = """
        Foundations details:
        Toni Piëch Foundation
        Musterstraße 42
        80333 München
        Germany
        Tel: +49 89 123456
        Fax: +49 89 654321
        """
        address1 = extract_physical_address(text_multiline)
        self.assertEqual(address1, "Toni Piëch Foundation, Musterstraße 42, 80333 München, Germany")

        # Case 2: Single line address
        text_singleline = """
        Our headquarters are at Musterstraße 42, 80333 München, Germany.
        """
        address2 = extract_physical_address(text_singleline)
        self.assertEqual(address2, "Musterstraße 42, 80333 München, Germany.")

        # Case 3: Phone number scrubbing
        text_with_phone = """
        Contact address:
        Musterstraße 42
        80333 München
        Tel: +49 89 123456
        """
        address3 = extract_physical_address(text_with_phone)
        self.assertEqual(address3, "Musterstraße 42, 80333 München")
        self.assertNotIn("Tel", address3)
        self.assertNotIn("+49", address3)

    @patch("preprocessing.extract_impressum.requests.get")
    def test_crawl_impressum_flow(self, mock_get):
        base_url = "https://example.org"
        
        # Mock Landing Page: contains only a link to impressum
        mock_landing = MagicMock()
        mock_landing.status_code = 200
        mock_landing.text = """
        <html>
            <h1>Welcome to Example Foundation</h1>
            <a href="/impressum">Impressum & Legal Notice</a>
        </html>
        """
        
        # Mock Impressum Page: contains address and generic email
        mock_impressum = MagicMock()
        mock_impressum.status_code = 200
        mock_impressum.text = """
        <html>
            <p>Example Foundation e.V.</p>
            <p>Musterstraße 42</p>
            <p>80333 München</p>
            <p>Email: info@example.org</p>
        </html>
        """
        
        # Set side effect: first call gets landing page, second gets impressum
        mock_get.side_effect = [mock_landing, mock_impressum]
        
        result = crawl_impressum(base_url)
        
        self.assertEqual(result["organization_url"], base_url)
        self.assertEqual(result["source_page_used"], "https://example.org/impressum")
        self.assertEqual(result["generic_email"], "info@example.org")
        self.assertEqual(result["address"], "Example Foundation e.V., Musterstraße 42, 80333 München")

if __name__ == "__main__":
    unittest.main()

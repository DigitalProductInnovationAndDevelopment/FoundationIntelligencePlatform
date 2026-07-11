import unittest
import os
import sys

# Ensure src directory is in sys.path
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(TESTS_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from preprocessing.consolidate import (
    extract_domain,
    normalize_name,
    jaccard_similarity,
    match_members,
    convert_gbp_to_eur,
    normalize_to_clean_schema,
    merge_members,
    consolidate_datasets
)

class TestConsolidate(unittest.TestCase):
    
    def test_extract_domain(self):
        self.assertEqual(extract_domain("http://www.womenwin.org"), "womenwin.org")
        self.assertEqual(extract_domain("https://womenwin.org/about/us"), "womenwin.org")
        self.assertEqual(extract_domain("http://sub.domain.com/test?arg=1"), "sub.domain.com")
        self.assertEqual(extract_domain(""), "")
        self.assertEqual(extract_domain(None), "")

    def test_normalize_name(self):
        self.assertEqual(normalize_name("Toni Piëch Foundation"), ["toni", "piëch"])
        self.assertEqual(normalize_name("Women Win"), ["women", "win"])
        self.assertEqual(normalize_name("The Novartis Charity Trust e.V."), ["novartis"])
        self.assertEqual(normalize_name(""), [])
        self.assertEqual(normalize_name(None), [])

    def test_jaccard_similarity(self):
        self.assertAlmostEqual(jaccard_similarity(["a", "b"], ["b", "c"]), 1/3)
        self.assertEqual(jaccard_similarity([], ["a"]), 0.0)

    def test_match_members(self):
        # 1. Matching by Website
        m1 = {"name": "Test Org A", "website": "https://testorga.org"}
        m2 = {"name": "Diff Name", "website": "http://www.testorga.org/home"}
        self.assertTrue(match_members(m1, m2))
        
        # 2. Matching by normalized name exact/Jaccard
        m3 = {"name": "Toni Piëch Foundation", "website": ""}
        m4 = {"name": "Toni Piëch", "website": ""}
        self.assertTrue(match_members(m3, m4))
        
        # 3. Numeric ID exclusion
        m5 = {"name": "136193034", "website": ""}
        m6 = {"name": "Novartis US Foundation", "website": ""}
        self.assertFalse(match_members(m5, m6))
        
        # 4. No match
        m7 = {"name": "Women Win", "website": ""}
        m8 = {"name": "Toni Piëch Foundation", "website": ""}
        self.assertFalse(match_members(m7, m8))

    def test_convert_gbp_to_eur(self):
        self.assertEqual(convert_gbp_to_eur("£10,000 (2024)"), "€12,000 (2024) (converted from GBP)")
        self.assertEqual(convert_gbp_to_eur("10,000 GBP"), "€12,000 (converted from GBP)")
        self.assertEqual(convert_gbp_to_eur("€10,000"), "€10,000") # No conversion
        self.assertEqual(convert_gbp_to_eur("Not publicly available"), "Not publicly available")

    def test_normalize_to_clean_schema(self):
        raw_philea = {
            "name": "Women Win",
            "website": "http://www.womenwin.org",
            "address": "Amsterdam",
            "email": "info@womenwin.org",
            "tags_focus": ["Health"],
            "geo_locations": {"Worldwide": ["Global"]},
            "position": {
                "address": "Amsterdam, NL",
                "city": "Amsterdam",
                "state": "North Holland",
                "country": "Netherlands",
                "lat": "52.3676",
                "lng": "4.9041"
            },
            "philea_info": {
                "About": "Short description of Women Win",
                "annual_giving": "€50,000 (2024)",
                "funding_model": "Open applications",
                "sources": ["http://source1.com"]
            }
        }
        
        clean = normalize_to_clean_schema(raw_philea, "Philea")
        
        self.assertEqual(clean["name"], "Women Win")
        self.assertEqual(clean["source"], "Philea")
        self.assertEqual(clean["website"], "http://www.womenwin.org")
        self.assertEqual(clean["email"], "info@womenwin.org")
        self.assertEqual(clean["address"], "Amsterdam, NL")
        self.assertEqual(clean["city"], "Amsterdam")
        self.assertEqual(clean["state"], "North Holland")
        self.assertEqual(clean["country"], "Netherlands")
        self.assertEqual(clean["latitude"], 52.3676)
        self.assertEqual(clean["longitude"], 4.9041)
        self.assertEqual(clean["funding_info"]["annual_giving"], "€50,000 (2024)")
        self.assertEqual(clean["funding_info"]["funding_model"], "Open applications")
        self.assertEqual(clean["thematic_focus"], ["Health"])
        self.assertEqual(clean["geographic_focus"], {"Worldwide": ["Global"]})

    def test_merge_members(self):
        p_member = normalize_to_clean_schema({
            "name": "Women Win",
            "website": "http://www.womenwin.org",
            "address": "Amsterdam",
            "email": "info@womenwin.org",
            "tags_focus": ["Health"],
            "geo_locations": {"Worldwide": ["Global"]},
            "philea_info": {
                "About": "Short description of Women Win",
                "annual_giving": "€50,000 (2024)",
                "funding_model": "Open applications",
                "sources": ["http://source1.com"]
            }
        }, "Philea")
        
        h_member = normalize_to_clean_schema({
            "name": "Women Win",
            "tags_focus": ["Human/Civil Rights"],
            "geo_locations": {"Europe (Western / General)": ["Netherlands"]},
            "philea_info": {
                "About": "A much longer and detailed description of Women Win from Hinchilla website.",
                "website": "http://www.womenwin.org",
                "annual_giving": "£45,000 (2024)", # 45,000 * 1.2 = 54,000 EUR vs 50,000 EUR -> Discrepancy!
                "funding_model": "Invitation only", # Conflict!
                "charityNumber": "12345",
                "sources": ["http://source2.com"]
            }
        }, "Hinchilla")
        
        merged = merge_members(p_member, h_member)
        
        # Verify preferred fields and merges
        self.assertEqual(merged["name"], "Women Win")
        self.assertEqual(merged["source"], "Philea, Hinchilla")
        self.assertEqual(merged["funding_info"]["charity_number"], "12345") # Extracted from Hinchilla
        
        # Verify tag and geolocation merging
        self.assertEqual(sorted(merged["thematic_focus"]), ["Health", "Human/Civil Rights"])
        self.assertEqual(merged["geographic_focus"]["Worldwide"], ["Global"])
        self.assertEqual(merged["geographic_focus"]["Europe (Western / General)"], ["Netherlands"])
        
        # Verify sources merging
        self.assertEqual(sorted(merged["funding_info"]["sources"]), ["http://source1.com", "http://source2.com"])
        
        # Verify discrepancies tracking
        self.assertIn("_discrepancies", merged)
        self.assertIn("annual_giving", merged["_discrepancies"])
        self.assertIn("funding_model", merged["_discrepancies"])
        
        # Check conversion details
        self.assertEqual(merged["_discrepancies"]["annual_giving"]["hinchilla_value"], "€54,000 (2024) (converted from GBP)")

    def test_consolidate_datasets(self):
        philea = [
            {"name": "Women Win", "website": "http://www.womenwin.org", "tags_focus": ["Health"]},
            {"name": "Toni Piëch Foundation", "website": "https://www.tonipiechfoundation.org", "tags_focus": ["Environment/Climate"]}
        ]
        hinchilla = [
            {"name": "Women Win", "philea_info": {"website": "http://www.womenwin.org", "annual_giving": "£10,000"}, "tags_focus": ["Health"]},
            {"name": "Other Org", "philea_info": {"website": "https://other.org", "annual_giving": "£20,000"}, "tags_focus": ["Education"]}
        ]
        
        consolidated = consolidate_datasets(philea, hinchilla)
        self.assertEqual(len(consolidated), 3) # 1 merged, 1 Philea-only, 1 Hinchilla-only
        
        # Verify unmatched Hinchilla was converted
        other_org = [m for m in consolidated if m["name"] == "Other Org"][0]
        self.assertEqual(other_org["funding_info"]["annual_giving"], "€24,000 (converted from GBP)")
        self.assertEqual(other_org["website"], "https://other.org")
        self.assertEqual(other_org["source"], "Hinchilla")

if __name__ == "__main__":
    unittest.main()

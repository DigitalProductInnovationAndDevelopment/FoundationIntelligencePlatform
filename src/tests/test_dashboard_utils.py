import os
import sys
import unittest


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(TESTS_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dashboard.dashboard_utils import is_missing_value, parse_annual_giving_value, parse_money_range


class TestDashboardMoneyParsing(unittest.TestCase):
    def test_money_parser_ignores_years_and_word_prefixes(self):
        self.assertEqual(
            parse_money_range("€144,000 (2025 budget) (converted from GBP)")["midpoint_amount"],
            144000.0,
        )
        self.assertEqual(
            parse_money_range("€213 million (2022 base investment)")["midpoint_amount"],
            213000000.0,
        )

    def test_money_parser_does_not_average_descriptive_multi_value_text(self):
        parsed = parse_money_range("$3.7 billion globally (2024), over $1 billion in grants in the US (2023)")
        self.assertEqual(parsed["midpoint_amount"], 3700000000.0)
        self.assertEqual(parsed["confidence"], "first_value_from_multi_value_text")

    def test_money_parser_still_handles_ranges(self):
        parsed = parse_money_range("€1,200 - €18,000 (typical range) (converted from GBP)")
        self.assertEqual(parsed["midpoint_amount"], 9600.0)
        self.assertEqual(parsed["confidence"], "range")

    def test_placeholder_prefix_with_punctuation_is_missing(self):
        value = "Not publicly disclosed; leverages total programming of $648m+ through co-financing model"
        self.assertTrue(is_missing_value(value))
        self.assertFalse(parse_money_range(value)["parsed"])

    def test_annual_giving_parser_rejects_non_annual_aggregates(self):
        examples = [
            "€10+ billion (2021-2027 program)",
            "€2,040,000,000+ since 1967 (converted from GBP)",
            "€358,800,000+ invested to date (converted from GBP)",
            "€132,000,000 total deployed (2019-2024) (converted from GBP)",
            "€164,400,000 government endowment (2022) (converted from GBP)",
            "€1,117,200,000+ since 2000 (24,809+ grants awarded) (converted from GBP)",
        ]
        for value in examples:
            with self.subTest(value=value):
                parsed = parse_annual_giving_value(value)
                self.assertFalse(parsed["parsed"])
                self.assertEqual(parsed["confidence"], "non_annual_aggregate")

    def test_annual_giving_parser_uses_explicit_annual_amount(self):
        parsed = parse_annual_giving_value(
            "€120,000,000+ pledged since 2016 (approx. €13-12 million annually) (converted from GBP)"
        )
        self.assertTrue(parsed["parsed"])
        self.assertEqual(parsed["confidence"], "explicit_annual_range")
        self.assertEqual(parsed["midpoint_amount"], 12500000.0)

    def test_annual_giving_parser_prefers_amount_before_annual_context(self):
        parsed = parse_annual_giving_value(
            "€1,320,000,000+ (over €1,320,000,000 in capital grants to local authorities annually, "
            "plus €1,680,000,000 in revenue grants) (converted from GBP)"
        )
        self.assertTrue(parsed["parsed"])
        self.assertEqual(parsed["confidence"], "explicit_annual_value")
        self.assertEqual(parsed["midpoint_amount"], 1320000000.0)


if __name__ == "__main__":
    unittest.main()

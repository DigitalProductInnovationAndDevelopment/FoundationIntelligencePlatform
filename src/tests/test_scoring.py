import copy
import unittest

from scoring.engine import (
    ScoreConfigurationError,
    load_score_configuration,
    score_relevance,
    validate_score_configuration,
)


def strong_organization():
    return {
        "programme_areas_source": ["Education", "Health"],
        "programme_areas_inferred": [],
        "geographic_focus_source": ["United Kingdom", "Ghana"],
        "geographic_focus_inferred": [],
        "annual_expenditure": 2_000_000,
        "organization_type": "foundation",
    }


def target_profile():
    return {
        "programme_areas": ["Education", "Health"],
        "geographies": ["United Kingdom", "Ghana"],
        "minimum_annual_expenditure": 1_000_000,
        "target_average_grant_amount": 50_000,
        "currency": "GBP",
        "organization_types": ["foundation"],
    }


def grant_stats():
    return {"average_amount": 60_000, "currency": "GBP", "grant_count": 4}


class TestScoreConfiguration(unittest.TestCase):
    def test_checked_in_example_configuration_loads_and_is_experimental(self):
        config = load_score_configuration()
        self.assertEqual(config.score_version, "example-relevance-v1")
        self.assertEqual(config.configuration_status, "experimental")
        self.assertAlmostEqual(sum(config.weights.values()), 1.0)

    def test_invalid_configuration_is_rejected(self):
        base = {
            "score_version": "test",
            "configuration_status": "experimental",
            "score_target": "test relevance",
            "missing_data_behavior": "renormalize_available_components",
            "review_confidence_threshold": 0.6,
            "weights": {
                "thematic_fit": 0.35,
                "geographic_fit": 0.25,
                "funding_capacity_fit": 0.15,
                "historical_grant_size_fit": 0.15,
                "organization_type_fit": 0.10,
            },
            "example_target_profile": {},
            "assumptions": [],
        }
        invalid_sum = copy.deepcopy(base)
        invalid_sum["weights"]["thematic_fit"] = 0.5
        with self.assertRaisesRegex(ScoreConfigurationError, "sum to 1.0"):
            validate_score_configuration(invalid_sum)
        missing_weight = copy.deepcopy(base)
        del missing_weight["weights"]["geographic_fit"]
        with self.assertRaisesRegex(ScoreConfigurationError, "Missing score component"):
            validate_score_configuration(missing_weight)
        invalid_status = copy.deepcopy(base)
        invalid_status["configuration_status"] = "client-approved-ish"
        with self.assertRaisesRegex(ScoreConfigurationError, "experimental or approved"):
            validate_score_configuration(invalid_status)


class TestRelevanceScore(unittest.TestCase):
    def setUp(self):
        self.config = load_score_configuration()

    def test_complete_strong_match_scores_one_hundred(self):
        result = score_relevance(
            strong_organization(), target_profile(), grant_stats(), self.config
        )
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["data_completeness"], 1.0)
        self.assertGreater(result["confidence"], 0.8)
        self.assertFalse(result["review_required"])
        self.assertTrue(result["not_a_prediction"])

    def test_weak_thematic_match_is_component_evidence_not_prediction(self):
        organization = strong_organization()
        organization["programme_areas_source"] = ["Education"]
        result = score_relevance(organization, target_profile(), grant_stats(), self.config)
        component = result["components"]["thematic_fit"]
        self.assertEqual(component["score"], 50.0)
        self.assertEqual(component["evidence"][0]["matched_values"], ["education"])
        self.assertEqual(result["configuration_status"], "experimental")

    def test_strong_geography_with_no_financial_data_renormalizes(self):
        organization = strong_organization()
        organization["annual_expenditure"] = None
        result = score_relevance(organization, target_profile(), {}, self.config)
        self.assertEqual(result["components"]["geographic_fit"]["score"], 100.0)
        self.assertFalse(result["components"]["funding_capacity_fit"]["available"])
        self.assertFalse(result["components"]["historical_grant_size_fit"]["available"])
        self.assertEqual(result["data_completeness"], 0.7)
        self.assertIn("annual expenditure missing", result["missing_inputs"])

    def test_missing_programme_and_geography_are_disclosed(self):
        organization = strong_organization()
        organization["programme_areas_source"] = []
        organization["geographic_focus_source"] = []
        result = score_relevance(organization, target_profile(), grant_stats(), self.config)
        self.assertIn("organization programme areas missing", result["missing_inputs"])
        self.assertIn("organization geographic focus missing", result["missing_inputs"])
        self.assertEqual(result["data_completeness"], 0.4)
        self.assertTrue(result["review_required"])

    def test_all_inputs_missing_returns_no_relevance_score(self):
        result = score_relevance({}, target_profile(), {}, self.config)
        self.assertIsNone(result["score"])
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["data_completeness"], 0.0)
        self.assertTrue(result["review_required"])

    def test_currency_mismatch_does_not_combine_grant_values(self):
        result = score_relevance(
            strong_organization(), target_profile(),
            {"average_amount": 60_000, "currency": "EUR", "grant_count": 4},
            self.config,
        )
        component = result["components"]["historical_grant_size_fit"]
        self.assertFalse(component["available"])
        self.assertEqual(component["missing_reason"], "grant currency does not match requested currency")

    def test_result_is_deterministic_and_component_arithmetic_is_explicit(self):
        first = score_relevance(strong_organization(), target_profile(), grant_stats(), self.config)
        second = score_relevance(strong_organization(), target_profile(), grant_stats(), self.config)
        self.assertEqual(first, second)
        weighted_total = sum(
            component["score"] * component["weight"]
            for component in first["components"].values()
            if component["available"]
        )
        available_weight = sum(
            component["weight"]
            for component in first["components"].values()
            if component["available"]
        )
        self.assertEqual(first["score"], round(weighted_total / available_weight, 2))
        self.assertLessEqual(first["confidence"], 1.0)
        self.assertNotEqual(first["confidence"], first["score"])

    def test_configurable_weights_change_result(self):
        raw = {
            "score_version": "custom-test-v1",
            "configuration_status": "experimental",
            "score_target": "test relevance",
            "missing_data_behavior": "renormalize_available_components",
            "review_confidence_threshold": 0.6,
            "weights": {
                "thematic_fit": 0.7,
                "geographic_fit": 0.1,
                "funding_capacity_fit": 0.05,
                "historical_grant_size_fit": 0.05,
                "organization_type_fit": 0.1,
            },
            "example_target_profile": target_profile(),
            "assumptions": ["test"],
        }
        custom = validate_score_configuration(raw)
        organization = strong_organization()
        organization["programme_areas_source"] = ["Education"]
        default_result = score_relevance(organization, target_profile(), grant_stats(), self.config)
        custom_result = score_relevance(organization, target_profile(), grant_stats(), custom)
        self.assertNotEqual(default_result["score"], custom_result["score"])
        self.assertEqual(custom_result["score_version"], "custom-test-v1")


if __name__ == "__main__":
    unittest.main()

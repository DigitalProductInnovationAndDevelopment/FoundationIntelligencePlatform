import unittest

from preprocessing.enrichment import (
    COMPILED_GEOGRAPHY_RULES,
    COMPILED_PROGRAMME_RULES,
    RULE_VERSION,
    RegexRule,
    RuleConfigurationError,
    apply_rules,
    build_enrichment_report,
    classify_geography_fields,
    classify_programme_fields,
    compile_rules,
    enrich_organization,
    normalize_country_value,
)


class TestRuleConfiguration(unittest.TestCase):
    def test_all_active_rules_compile_with_unique_ids(self):
        self.assertGreater(len(COMPILED_PROGRAMME_RULES), 10)
        self.assertGreater(len(COMPILED_GEOGRAPHY_RULES), 10)
        ids = [item.config.rule_id for item in COMPILED_PROGRAMME_RULES + COMPILED_GEOGRAPHY_RULES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_invalid_regex_is_rejected_with_rule_id(self):
        with self.assertRaisesRegex(RuleConfigurationError, "invalid.rule"):
            compile_rules((RegexRule("invalid.rule", "test", "Test", "("),))

    def test_invalid_weight_is_rejected(self):
        with self.assertRaisesRegex(RuleConfigurationError, "outside 0..1"):
            compile_rules((RegexRule("weight.rule", "test", "Test", "test", weight=1.2),))


class TestProgrammeEnrichment(unittest.TestCase):
    def test_exact_source_classification_stays_separate_from_inference(self):
        result = classify_programme_fields(
            {"programme_area_text": "Education"},
            ["Education/training"],
        )
        self.assertEqual(result["source_categories"], ["Education"])
        self.assertNotIn("Education", result["categories"])
        self.assertEqual(result["source_evidence"][0]["source_field"], "source_classification")

    def test_synonym_plural_casing_punctuation_and_abbreviation(self):
        result = classify_programme_fields({
            "description": "SCHOOLS, students' learning and STEM; plus AI-driven software."
        })
        self.assertIn("Education", result["categories"])
        self.assertIn("tech-enablement", result["categories"])
        self.assertTrue(all(item["rule_id"] for item in result["evidence"]))

    def test_extended_tech_enablement_taxonomy_requires_concrete_technology(self):
        result = classify_programme_fields({
            "description": (
                "Open data and digital public infrastructure; civic technology, EdTech, "
                "cybersecurity and technology transfer for local organisations."
            )
        })
        self.assertIn("tech-enablement", result["categories"])
        rule_ids = {item["rule_id"] for item in result["evidence"] if item["accepted"]}
        self.assertIn("programme.technology_infrastructure", rule_ids)
        self.assertIn("programme.technology_public_interest", rule_ids)
        self.assertIn("programme.technology_learning", rule_ids)
        self.assertIn("programme.technology_security_transfer", rule_ids)

    def test_stem_engineering_and_ict_are_tech_enablement(self):
        result = classify_programme_fields({
            "description": "STEM engineering and ICT training, including computer programming."
        })
        self.assertIn("tech-enablement", result["categories"])
        rule_ids = {item["rule_id"] for item in result["evidence"] if item["accepted"]}
        self.assertIn("programme.technology_stem_engineering", rule_ids)
        self.assertIn("programme.technology_computing", rule_ids)

    def test_generic_innovation_is_not_a_tech_enablement_match(self):
        result = classify_programme_fields(
            {"description": "We fund innovation across the voluntary sector."},
            ["Innovation"],
        )
        self.assertNotIn("tech-enablement", result["source_categories"])
        self.assertNotIn("tech-enablement", result["categories"])

    def test_word_boundaries_prevent_substring_false_positive(self):
        result = classify_programme_fields({"description": "We build partnerships and repair artifacts."})
        self.assertNotIn("Arts & Culture", result["categories"])
        self.assertNotIn("tech-enablement", result["categories"])

    def test_negative_context_is_evidenced_and_requires_review(self):
        result = classify_programme_fields({"description": "We do not fund education programmes."})
        self.assertNotIn("Education", result["categories"])
        self.assertTrue(result["review_required"])
        self.assertIn("negative_context_detected", result["review_reasons"])
        self.assertTrue(any(item["negative_context"] for item in result["evidence"]))

    def test_multiple_categories_are_supported(self):
        result = classify_programme_fields({
            "description": "Medical research and climate education for children."
        })
        self.assertTrue({"Health", "Sciences & Research", "Environment/Climate", "Education", "Youth/Children Development"}.issubset(result["categories"]))

    def test_weak_only_match_requires_review(self):
        result = classify_programme_fields({"description": "We support social innovation."})
        self.assertEqual(result["categories"], ["Socio-economic Development, Poverty"])
        self.assertTrue(result["review_required"])
        self.assertIn("weak_rules_only", result["review_reasons"])

    def test_empty_text_has_truthful_unavailable_state(self):
        result = classify_programme_fields({"description": ""})
        self.assertEqual(result["categories"], [])
        self.assertEqual(result["method"], "unavailable")
        self.assertTrue(result["insufficient_source_text"])

    def test_custom_overlapping_and_conflicting_rules_are_reported(self):
        rules = compile_rules((
            RegexRule("one", "test", "One", r"climate change", conflicts_with=("Two",)),
            RegexRule("two", "test", "Two", r"climate", conflicts_with=("One",)),
        ))
        result = apply_rules({"description": "Climate change"}, rules)
        self.assertGreater(result["overlapping_match_count"], 0)
        self.assertEqual(result["conflicting_categories"], [["One", "Two"]])
        self.assertTrue(result["review_required"])


class TestGeographyEnrichment(unittest.TestCase):
    def test_country_iso_and_spelling_variants_normalize(self):
        self.assertEqual(normalize_country_value("GB")["name"], "United Kingdom")
        self.assertEqual(normalize_country_value("U.K.")["name"], "United Kingdom")
        self.assertEqual(normalize_country_value("Deutschland")["code"], "DE")
        self.assertEqual(normalize_country_value("Österreich")["code"], "AT")

    def test_multiple_countries_region_and_global_scope(self):
        result = classify_geography_fields({
            "focus": "Work across Denmark, Norway and the DACH region, with global partners."
        })
        self.assertTrue({"Denmark", "Norway", "DACH region", "Worldwide"}.issubset(result["categories"]))

    def test_ambiguous_place_is_flagged(self):
        result = classify_geography_fields({"focus": "Georgia"})
        self.assertIn("Georgia", result["categories"])
        self.assertTrue(result["review_required"])
        self.assertIn("ambiguous_geography", result["review_reasons"])

    def test_unsupported_subnational_place_is_not_inferred(self):
        result = classify_geography_fields({"focus": "Reading"})
        self.assertEqual(result["categories"], [])

    def test_boundaries_prevent_ukraine_from_matching_uk(self):
        result = classify_geography_fields({"focus": "Support for Ukraine."})
        self.assertIn("Ukraine", result["categories"])
        self.assertNotIn("United Kingdom", result["categories"])

    def test_headquarters_is_separate_from_programme_geography(self):
        result = enrich_organization({
            "country": "Germany",
            "state": "Berlin",
            "description": "Programmes for communities in Ghana.",
        })
        self.assertEqual(result["headquarters_country"], "Germany")
        self.assertEqual(result["headquarters_region"], "Berlin")
        self.assertIn("Ghana", result["geographic_focus_inferred"])
        self.assertNotIn("Germany", result["geographic_focus_inferred"])

    def test_no_geography_is_unavailable(self):
        result = classify_geography_fields({"focus": ""})
        self.assertEqual(result["categories"], [])
        self.assertEqual(result["method"], "unavailable")


class TestEnrichmentReport(unittest.TestCase):
    def test_report_separates_coverage_from_accuracy(self):
        organizations = [{
            "programme_areas_source": '["Education"]',
            "programme_areas_inferred": "[]",
            "geographic_focus_source": "[]",
            "geographic_focus_inferred": '["Ghana"]',
            "programme_area_method": "source_normalization",
            "geography_method": "deterministic_regex",
            "source": "Charity Commission",
            "programme_area_review_required": False,
            "geography_review_required": True,
            "enrichment_review_reasons": '["ambiguous_geography"]',
        }]
        report = build_enrichment_report(organizations, [])
        self.assertEqual(report["total_records_processed"], 1)
        self.assertEqual(report["records_with_source_programme_areas"], 1)
        self.assertEqual(report["records_with_inferred_geographic_focus"], 1)
        self.assertEqual(report["records_requiring_review"], 1)
        self.assertFalse(report["coverage_is_accuracy"])
        self.assertFalse(report["predictive_accuracy_measured"])
        self.assertEqual(report["rule_version"], RULE_VERSION)


if __name__ == "__main__":
    unittest.main()

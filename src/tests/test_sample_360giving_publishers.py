import unittest

from pipelines.sample_360giving_publishers import registry_publishers, select_organisation_sample


class TestRandomPublisherSample(unittest.TestCase):
    def test_sample_is_deterministic_unique_and_skips_missing_ids(self):
        organisations = [
            {"org_id": "one"}, {"org_id": "two"}, {"org_id": "two", "name": "Latest"},
            {"org_id": "three"}, {"name": "missing"},
        ]
        first = select_organisation_sample(organisations, 2, 42)
        second = select_organisation_sample(organisations, 2, 42)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(len({item["org_id"] for item in first}), 2)

    def test_large_sample_returns_all_usable_organisations_in_stable_order(self):
        sample = select_organisation_sample([{"org_id": "b"}, {"org_id": "a"}], 10, 1)
        self.assertEqual([item["org_id"] for item in sample], ["a", "b"])

    def test_registry_publishers_deduplicates_publishers_and_keeps_dataset_context(self):
        publishers = registry_publishers([
            {"title": "One", "publisher": {"org_id": "publisher-a", "name": "A"}},
            {"title": "Two", "publisher": {"org_id": "publisher-a", "name": "A"}},
            {"title": "Three", "publisher": {"org_id": "publisher-b", "name": "B"}},
        ])
        self.assertEqual([item["org_id"] for item in publishers], ["publisher-a", "publisher-b"])
        self.assertEqual(publishers[0]["dataset_count"], 2)
        self.assertEqual(publishers[0]["dataset_titles"], ["One", "Two"])


if __name__ == "__main__":
    unittest.main()

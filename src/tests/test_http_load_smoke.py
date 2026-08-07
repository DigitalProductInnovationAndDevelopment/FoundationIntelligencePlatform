import unittest

from scripts.http_load_smoke import percentile


class TestHttpLoadSmoke(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self):
        values = [100, 20, 50, 10, 80]
        self.assertEqual(percentile(values, 0.5), 50)
        self.assertEqual(percentile(values, 0.95), 100)

    def test_percentile_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            percentile([], 0.95)


if __name__ == "__main__":
    unittest.main()

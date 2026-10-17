import unittest

from usage import parse_usage

SNAKE = {
    "rate_limit": {
        "primary_window": {"used_percent": 30.0, "window_minutes": 300, "resets_at": 1790000000},
        "secondary_window": {"used_percent": 62.0, "window_minutes": 10080, "resets_at": 1790000000},
    }
}
CAMEL = {
    "rate_limits": {
        "primary": {"usedPercent": 10.0, "windowDurationMins": 300, "resetsAt": 1790000000},
        "secondary": {"usedPercent": 55.0, "limit_window_seconds": 604800, "resetsAt": 1790000000},
    }
}


class TestParseUsage(unittest.TestCase):
    def test_snake_case(self):
        snap = parse_usage(SNAKE)
        self.assertEqual(snap.weekly.used_pct, 62.0)
        self.assertEqual(snap.weekly.window_minutes, 10080)
        self.assertEqual(snap.weekly.resets_at, 1790000000)
        self.assertEqual(snap.five_hour.used_pct, 30.0)
        self.assertIsNone(snap.error)

    def test_camel_case_and_seconds(self):
        snap = parse_usage(CAMEL)
        self.assertEqual(snap.weekly.window_minutes, 10080)
        self.assertEqual(snap.weekly.used_pct, 55.0)
        self.assertEqual(snap.five_hour.used_pct, 10.0)

    def test_real_like_primary_is_weekly(self):
        # prolite 计划：primary_window 即周窗口，字段为 reset_at
        snap = parse_usage({
            "rate_limit": {
                "primary_window": {"used_percent": 25, "limit_window_seconds": 604800,
                                   "reset_at": 1792649667},
                "secondary_window": None,
            }
        })
        self.assertEqual(snap.weekly.used_pct, 25.0)
        self.assertEqual(snap.weekly.window_minutes, 10080)
        self.assertEqual(snap.weekly.resets_at, 1792649667)
        self.assertIsNone(snap.five_hour)

    def test_reset_after_seconds_fallback(self):
        snap = parse_usage({
            "rate_limit": {"primary_window": {"used_percent": 25,
                                              "limit_window_seconds": 604800,
                                              "reset_after_seconds": 3600}}
        })
        self.assertAlmostEqual(snap.weekly.resets_at - snap.fetched_at, 3600, delta=2)

    def test_empty_rate_limit(self):
        snap = parse_usage({"rate_limit": {}})
        self.assertIsNone(snap.weekly)
        self.assertIsNone(snap.five_hour)


if __name__ == "__main__":
    unittest.main()

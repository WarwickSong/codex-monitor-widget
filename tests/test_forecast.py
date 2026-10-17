import unittest
from datetime import datetime

from forecast import forecast, weekday_rate_from_history, working_hours_between


class TestWorkingHours(unittest.TestCase):
    def test_full_week(self):
        # 2026-10-12 是周一，完整一周工作日 = 5*24
        self.assertAlmostEqual(
            working_hours_between(datetime(2026, 10, 12), datetime(2026, 10, 19)), 120.0)

    def test_across_weekend(self):
        # 周五 23:00 → 周一 01:00：周五 1h + 周一 1h，周末不计
        self.assertAlmostEqual(
            working_hours_between(datetime(2026, 10, 16, 23), datetime(2026, 10, 19, 1)), 2.0)

    def test_empty_range(self):
        t = datetime(2026, 10, 15, 12)
        self.assertEqual(working_hours_between(t, t), 0.0)


class TestForecast(unittest.TestCase):
    NOW = datetime(2026, 10, 14, 12, 0)      # 周三
    RESETS = datetime(2026, 10, 16, 12, 0)   # 周五，48h 后

    def test_every_day_and_fallback(self):
        f = forecast(50.0, self.RESETS, 10080, now=self.NOW)
        # 窗口起点 = 10-16 12:00 − 7天 = 10-09 12:00（周五）→ 全程 120h
        self.assertAlmostEqual(f.rate_all, 50.0 / 120.0)
        self.assertAlmostEqual(f.remaining_all_h, 48.0)
        self.assertAlmostEqual(f.every_day.end_remaining_pct, 50.0 - (50.0 / 120.0) * 48.0)
        self.assertAlmostEqual(f.every_day.days_left, 120.0 / 24.0)  # 5.0 天
        # 无历史 → 保守回退：全部消耗视为工作日发生
        self.assertTrue(f.fallback_wd)
        # 窗口内工作日小时：周五12 + 周一24 + 周二24 + 周三12 = 72
        self.assertAlmostEqual(f.rate_wd, 50.0 / 72.0)
        # 距重置工作日小时：周三12 + 周四24 + 周五12 = 48（无周末）
        self.assertAlmostEqual(f.remaining_wd_h, 48.0)
        self.assertAlmostEqual(f.working_day.end_remaining_pct, 50.0 - (50.0 / 72.0) * 48.0)

    def test_working_day_hours_across_weekend(self):
        f = forecast(50.0, datetime(2026, 10, 19, 12, 0), 10080, now=self.NOW)
        # 周三12→周一12：周三12 + 周四24 + 周五24 + 周一12 = 72，周末不计
        self.assertAlmostEqual(f.remaining_wd_h, 72.0)

    def test_history_overrides_fallback(self):
        samples = [
            (datetime(2026, 10, 12, 12), 10.0),
            (datetime(2026, 10, 13, 12), 30.0),  # 周二 +20
            (datetime(2026, 10, 14, 12), 30.0),
            (datetime(2026, 10, 15, 12), 30.0),
            (datetime(2026, 10, 16, 12), 30.0),
        ]
        f = forecast(50.0, self.RESETS, 10080,
                     now=datetime(2026, 10, 16, 12), history_samples=samples)
        self.assertFalse(f.fallback_wd)
        self.assertAlmostEqual(f.rate_wd, 20.0 / 96.0)  # 观测期工作日 96h


class TestHistoryRate(unittest.TestCase):
    def test_insufficient_span_returns_none(self):
        samples = [(datetime(2026, 10, 15, 12), 10.0), (datetime(2026, 10, 16, 12), 30.0)]
        self.assertIsNone(weekday_rate_from_history(samples, now=datetime(2026, 10, 16, 12)))

    def test_nonpositive_usage_returns_none(self):
        samples = [(datetime(2026, 10, 12, 12), 30.0), (datetime(2026, 10, 16, 12), 30.0)]
        self.assertIsNone(weekday_rate_from_history(samples, now=datetime(2026, 10, 16, 12)))


if __name__ == "__main__":
    unittest.main()

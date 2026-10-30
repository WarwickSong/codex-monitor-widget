import tempfile
import unittest
from pathlib import Path

from tokens import (
    SessionTokens, cache_hit_rate, fit_tokens_per_pct, fmt_tok,
    refresh, session_totals, window_usage,
)


def _count_evt(ts, inp, cached, out, pct, resets, cache_write=0):
    return (
        '{"timestamp":"%s","type":"event_msg","payload":{"type":"token_count",'
        '"info":{"total_token_usage":{"input_tokens":%d,"cached_input_tokens":%d,'
        '"cache_write_input_tokens":%d,"output_tokens":%d,"total_tokens":%d}},'
        '"rate_limits":{"primary":{"used_percent":%s,"window_minutes":10080,'
        '"resets_at":%d}}}}'
        % (ts, inp, cached, cache_write, out, inp + cached + out, pct, resets)
    )


class TestSessionTotals(unittest.TestCase):
    def test_parse_last_event(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "rollout-2026-10-30T10-00-00-01a1-1.jsonl"
            f.write_text("\n".join([
                '{"timestamp":"2026-10-30T02:00:41Z","type":"other"}',
                _count_evt("2026-10-30T02:00:41Z", 100, 0, 10, "1.0", 1000),
                _count_evt("2026-10-30T03:00:41Z", 200, 50, 20, "2.5", 1000),
            ]), encoding="utf-8")
            s = session_totals(f)
            self.assertEqual((s.input_t, s.cached_t, s.output_t), (200, 50, 20))
            self.assertEqual(s.pct_end, 2.5)
            self.assertEqual(s.resets_at, 1000)
            self.assertEqual(s.total, 270)

    def test_no_token_events(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "rollout-x.jsonl"
            f.write_text('{"timestamp":"2026-10-30T02:00:41Z","type":"other"}',
                         encoding="utf-8")
            self.assertIsNone(session_totals(f))

    def test_dedup_resumed_chain(self):
        """续聊链两个文件只计最新（其累计含前史，不能重复求和）。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conv = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            old = root / f"rollout-2026-10-29T10-00-00-{conv}_11111111-2222-3333-4444-555555555555.jsonl"
            new = root / f"rollout-2026-10-30T10-00-00-{conv}_66666666-7777-8888-9999-000000000000.jsonl"
            old.write_text(_count_evt("2026-10-29T02:00:41Z", 100, 0, 10, "1.0", 1000),
                           encoding="utf-8")
            new.write_text(_count_evt("2026-10-30T02:00:41Z", 500, 200, 50, "3.0", 1000),
                           encoding="utf-8")
            ss = refresh(root)
            self.assertEqual(len(ss), 1)
            self.assertEqual(ss[0].total, 750)


class TestFitAndUsage(unittest.TestCase):
    def _sess(self, pct, total, resets=1000, end_ts=1.0):
        return SessionTokens(end_ts=end_ts, resets_at=resets, pct_end=pct,
                             input_t=total // 2, cached_t=total // 4,
                             output_t=total - total // 2 - total // 4)

    def test_fit_linear(self):
        # 累计 2M→2%，4M→4%，8M→8%：单会话值 2M/2M/4M
        ss = [self._sess(2, 2_000_000, end_ts=1), self._sess(4, 2_000_000, end_ts=2),
              self._sess(8, 4_000_000, end_ts=3)]
        per_pct = fit_tokens_per_pct(ss)
        self.assertAlmostEqual(per_pct, 1_000_000, delta=1_000)

    def test_fit_needs_samples(self):
        self.assertIsNone(fit_tokens_per_pct([self._sess(1, 100)]))
        self.assertIsNone(fit_tokens_per_pct([self._sess(1, 100), self._sess(1.2, 200)]))

    def test_fit_separates_windows(self):
        """不同 resets_at 分组累积，互不污染。"""
        ss = [self._sess(2, 2_000_000, resets=1000, end_ts=1),
              self._sess(4, 2_000_000, resets=1000, end_ts=2),
              self._sess(8, 4_000_000, resets=1000, end_ts=3),
              self._sess(2, 2_000_000, resets=2000, end_ts=4),
              self._sess(4, 2_000_000, resets=2000, end_ts=5),
              self._sess(8, 4_000_000, resets=2000, end_ts=6)]
        self.assertAlmostEqual(fit_tokens_per_pct(ss), 1_000_000, delta=1_000)

    def test_window_usage_filters(self):
        ss = [self._sess(2, 100, end_ts=10), self._sess(2, 50, end_ts=20)]
        in_, cached, out = window_usage(ss, 15)
        self.assertEqual(in_ + cached + out, 50)

    def test_hit_rate_and_fmt(self):
        self.assertAlmostEqual(cache_hit_rate(25, 75), 0.75)
        self.assertIsNone(cache_hit_rate(0, 0))
        self.assertEqual(fmt_tok(999), "999")
        self.assertEqual(fmt_tok(35_000), "4万")
        self.assertEqual(fmt_tok(716_462_780), "7.16亿")


if __name__ == "__main__":
    unittest.main()

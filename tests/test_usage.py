import unittest
from unittest.mock import Mock, patch

from usage import _parse_window, fetch_remote_usage


class UsageTests(unittest.TestCase):
    def test_window_converts_used_to_remaining(self):
        window = _parse_window(
            {"used_percent": 11, "limit_window_seconds": 604800, "reset_at": 1784995996}
        )
        self.assertEqual(window.label, "每周")
        self.assertEqual(window.remaining_percent, 89)

    @patch("usage._read_auth", return_value=("token-value-that-is-long-enough", "account-id"))
    @patch("usage.requests.get")
    def test_remote_payload_is_parsed(self, request_get, _read_auth):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "plan_type": "plus",
            "rate_limit": {
                "allowed": True,
                "primary_window": {
                    "used_percent": 25,
                    "limit_window_seconds": 18000,
                    "reset_at": 1784995996,
                },
            },
            "credits": {"has_credits": False, "balance": "0"},
        }
        request_get.return_value = response
        result = fetch_remote_usage()
        self.assertEqual(result.source, "remote")
        self.assertEqual(result.plan_type, "plus")
        self.assertEqual(result.windows[0].label, "5 小时")
        self.assertEqual(result.windows[0].remaining_percent, 75)


if __name__ == "__main__":
    unittest.main()

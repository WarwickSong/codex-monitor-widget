import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import history
from usage import RateWindow, UsageSnapshot


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = (history.DATA_DIR, history.HISTORY_FILE, history.SETTINGS_FILE)
        history.DATA_DIR = Path(self.tmp.name)
        history.HISTORY_FILE = history.DATA_DIR / "history.jsonl"
        history.SETTINGS_FILE = history.DATA_DIR / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()
        history.DATA_DIR, history.HISTORY_FILE, history.SETTINGS_FILE = self._old

    def test_append_and_load_roundtrip(self):
        snap = UsageSnapshot(
            fetched_at=datetime.now().timestamp(),
            weekly=RateWindow(62.0, 10080, 1790000000),
        )
        history.append_sample(snap)
        samples = history.load_samples()
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0][1], 62.0)

    def test_append_without_weekly_is_noop(self):
        history.append_sample(UsageSnapshot(fetched_at=0.0, error="x"))
        self.assertEqual(history.load_samples(), [])

    def test_corrupt_lines_skipped(self):
        history.DATA_DIR.mkdir(parents=True, exist_ok=True)
        history.HISTORY_FILE.write_text("not json\n{\"ts\": 1, \"used\": \"x\"}\n", encoding="utf-8")
        self.assertEqual(history.load_samples(), [])

    def test_settings_roundtrip(self):
        history.save_settings({"pos": [100, 200]})
        self.assertEqual(history.load_settings(), {"pos": [100, 200]})


if __name__ == "__main__":
    unittest.main()

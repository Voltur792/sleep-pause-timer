import unittest
import json
import tempfile
from datetime import datetime
from pathlib import Path

from sleep_pause import AlarmEngine, _idle_elapsed_ms


class IdleTickTests(unittest.TestCase):
    def test_delta_across_uint32_wrap(self):
        self.assertEqual(_idle_elapsed_ms(25, 0xFFFFFFF0), 41)

    def test_regular_delta(self):
        self.assertEqual(_idle_elapsed_ms(50_000, 49_000), 1_000)

    def test_corrupt_weekday_schedule_is_ignored_and_does_not_loop(self):
        class SilentPlayer:
            def play_once(self, path, should_continue=None):
                return True

            def stop(self):
                pass

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "alarm.json"
            path.write_text(json.dumps({"alarm": {
                "hour": 8, "minute": 0, "days_of_week": [99]
            }}), encoding="utf-8")
            engine = AlarmEngine(
                state_path=path,
                now_fn=lambda: datetime(2026, 9, 16, 7, 30),
                player=SilentPlayer(),
            )
            try:
                self.assertFalse(engine.state()["scheduled"])
                engine._days_of_week = [99]
                result = engine._next_occurrence(8, 0)
                self.assertLess((result - datetime(2026, 9, 16, 7, 30)).days, 8)
            finally:
                engine.stop_thread()


if __name__ == "__main__":
    unittest.main()

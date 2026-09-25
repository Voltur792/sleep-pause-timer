import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from sleep_pause import AlarmEngine


class SilentPlayer:
    def play_once(self, path, should_continue=None):
        return True

    def stop(self):
        pass


class MultipleMonthlyAlarmTests(unittest.TestCase):
    def make_engine(self, path):
        return AlarmEngine(state_path=path, now_fn=datetime.now, player=SilentPlayer())

    def test_multiple_times_on_one_date_persist_and_cancel_individually(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "alarm.json"
            day = date.today() + timedelta(days=2)
            engine = self.make_engine(path)
            try:
                self.assertTrue(engine.set_monthly_alarm(day, 9, 0))
                self.assertTrue(engine.set_monthly_alarm(day, 11, 0))
                self.assertFalse(engine.set_monthly_alarm(day, 9, 0))
                self.assertEqual(engine.get_monthly_alarms()[day.isoformat()], [
                    {"hour": 9, "minute": 0}, {"hour": 11, "minute": 0}
                ])
                self.assertEqual(engine.state()["monthly_count"], 2)
                self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["monthly_alarms"][day.isoformat()]), 2)
                with engine._condition:
                    self.assertTrue(engine._claim_monthly_alarm_locked(day.isoformat(), 9, 0))
                self.assertEqual(engine.get_monthly_alarms()[day.isoformat()], [
                    {"hour": 11, "minute": 0}
                ])
                self.assertEqual(engine.state()["monthly_count"], 1)
            finally:
                engine.stop_thread()

            restored = self.make_engine(path)
            try:
                self.assertEqual(restored.get_monthly_alarms()[day.isoformat()], [
                    {"hour": 11, "minute": 0}
                ])
                self.assertTrue(restored.cancel_monthly_alarm(day, 11, 0))
                self.assertNotIn(day.isoformat(), restored.get_monthly_alarms())
            finally:
                restored.stop_thread()

    def test_legacy_single_alarm_format_is_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "alarm.json"
            day = date.today() + timedelta(days=2)
            path.write_text(json.dumps({"monthly_alarms": {
                day.isoformat(): {"hour": 9, "minute": 0}
            }}), encoding="utf-8")
            engine = self.make_engine(path)
            try:
                self.assertEqual(engine.get_monthly_alarms()[day.isoformat()], [
                    {"hour": 9, "minute": 0}
                ])
            finally:
                engine.stop_thread()


if __name__ == "__main__":
    unittest.main()

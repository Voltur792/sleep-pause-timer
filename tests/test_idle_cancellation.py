import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sleep_pause import SleepPauseApp


class IdleCancellationTests(unittest.TestCase):
    def test_stop_cancels_already_queued_power_action(self):
        for command in ('shutdown', 'sleep'):
            app = SleepPauseApp.__new__(SleepPauseApp)
            callbacks = []
            app.root = SimpleNamespace(after=lambda delay, fn: callbacks.append(fn))
            app.idle_stop_event = threading.Event()
            app.key_var = SimpleNamespace(get=lambda: command)
            app.win_title_entry = SimpleNamespace(get=lambda: '')
            app._set_status = Mock()
            app.warning = '#efc577'
            shutdown, sleep = Mock(), Mock()
            api = SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(ExitWindowsEx=shutdown),
                                                        PowrProf=SimpleNamespace(SetSuspendState=sleep)))
            with patch('sleep_pause.ctypes', api):
                app._on_idle_timeout()
                self.assertEqual(len(callbacks), 2)
                app.idle_stop_event.set()
                for callback in callbacks: callback()
            shutdown.assert_not_called()
            sleep.assert_not_called()


if __name__ == '__main__': unittest.main()

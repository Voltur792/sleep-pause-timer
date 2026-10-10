"""Windows GUI smoke test. Media and power actions are never executed."""
import os
import tempfile
import threading
import tkinter as tk
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sleep_pause
from timer_ui import show_tab, load_preferences, show_idle_warning
from timer_widgets import RoundedEntry


@unittest.skipUnless(os.name == 'nt', 'Native Windows interface')
class InterfaceTests(unittest.TestCase):
    def test_themes_controls_alarm_calendar_and_timer_actions(self):
        with tempfile.TemporaryDirectory(prefix='.tmp-ui-', dir=Path(__file__).resolve().parents[1]) as folder:
            with patch.dict(os.environ, {'SPT_APP_ALARM_STATE_DIR': folder}), \
                    patch.object(sleep_pause, 'pyautogui', SimpleNamespace(press=lambda key: sent.append(key))):
                root = tk.Tk()
                root.withdraw()
                errors, sent, checkpoints = [], [], []
                root.report_callback_exception = lambda *args: errors.append(args[1])
                app = sleep_pause.SleepPauseApp(root)
                app._on_idle_timeout = lambda: None
                app._show_idle_alert = lambda minutes: None
                app._update_tray = lambda: None
                root.deiconify()

                def exercise():
                    try:
                        self.assertEqual(app.idle_spin.get(), '0')
                        self.assertEqual(app.btn_pause.cget('state'), 'disabled')
                        app.toggle_theme()
                        self.assertEqual(load_preferences(app.preferences_path)['theme'], 'light')
                        self.assertEqual(app.combo.cget('bg'), app.entry_bg)
                        warning = show_idle_warning(app, 10)
                        self.assertEqual(warning.title(), 'Предупреждение о бездействии')
                        self.assertTrue(hasattr(warning, '_header_icon'))
                        app._cancel_idle_alert(warning)
                        app.combo._open()
                        app.combo._close()
                        show_tab(app, 'alarm')
                        future = date.today() + timedelta(days=2)
                        app.alarm.set_monthly_alarm(future, 9, 30)
                        self.assertFalse(app.updater._active(), 'Scheduled alarms must allow updating')
                        app._show_time_picker(future, future.isoformat(), lambda: None)
                        self.assertTrue(any(isinstance(child, tk.Toplevel) for child in root.winfo_children()))
                        for child in root.winfo_children():
                            if isinstance(child, tk.Toplevel): child.destroy()
                        app._show_detect_dialog(['Test window'])
                        for child in root.winfo_children():
                            if isinstance(child, tk.Toplevel): child.destroy()
                        app.alarm.cancel_monthly_alarm(future, 9, 30)
                        show_tab(app, 'timer')
                        app.idle_spin.delete(0, 'end')
                        app.idle_spin.insert(0, '1')
                        app.start_idle()
                        self.assertTrue(app.updater._active())
                        app.cancel_idle()
                        app.entry_sec.delete(0, 'end')
                        app.entry_sec.insert(0, '2')
                        app.start()
                        self.assertIsNone(app.idle_counter_thread, 'Timer must not start inactivity tracking, even with a nonzero value')
                        root.event_generate('<Motion>', x=10, y=10)
                        self.assertTrue(app.idle_stop_event.is_set())
                        app.toggle_pause()
                        self.assertTrue(app.paused)
                        app.toggle_pause()
                        self.assertFalse(app.paused)
                        app.start_idle()
                        app.cancel_idle()
                        self.assertTrue(app.worker.is_alive(), 'Stopping inactivity must not stop the timer')
                        self.assertEqual(app.btn_cancel.cget('state'), 'normal')
                        checkpoints.append(True)
                    except Exception as exc:
                        errors.append(exc)

                def finish():
                    try:
                        self.assertEqual(sent, ['playpause'])
                        self.assertEqual(app.btn_start.cget('state'), 'normal')
                        self.assertGreater(app.status.winfo_height(), 5)
                        self.assertLess(app.status.winfo_rooty() + app.status.winfo_height(), root.winfo_rooty() + root.winfo_height())
                    except Exception as exc: errors.append(exc)
                    app.stop_event.set()
                    app.idle_stop_event.set()
                    app.alarm.stop_thread()
                    root.destroy()
                root.after(200, exercise)
                root.after(5000, finish)
                root.mainloop()
                self.assertEqual(errors, [])
                self.assertTrue(checkpoints)


if __name__ == '__main__': unittest.main()

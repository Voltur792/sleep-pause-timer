#!/usr/bin/env python3
import ctypes
import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk

from timer_updates import installed_version, run_helper, UpdateController
from timer_widgets import RoundedButton, RoundedEntry
from timer_ui import build_interface, apply_palette, apply_colors, load_preferences, save_preferences, show_idle_warning, ACTION_TITLES

if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
    sys.exit(run_helper(sys.argv[2]))

# Опциональные зависимости
try:
    import pyautogui
except Exception:
    pyautogui = None

try:
    import pygetwindow as gw
except Exception:
    gw = None

# Tray dependencies
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
except Exception:
    pystray = None
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageOps = None
    ImageTk = None

# Доп. инструменты для активации окна (опционально)
try:
    import win32gui
    import win32con
except Exception:
    win32gui = None
    win32con = None

# Для поиска процессов (опционально)
try:
    import psutil
except Exception:
    psutil = None

# Константы
DEFAULT_HOUR = "0"
DEFAULT_MIN = "0"
DEFAULT_SEC = "0"
KEY_OPTIONS = ["playpause", "shutdown", "sleep"]
APP_ICON_PNG = "app_icon.png"
APP_ICON_ICO = "app_icon.ico"

# Будильник: каталог состояния (отдельный от плагина Astra) и дефолтная
# мелодия (системный звук Windows, если пользователь не выбрал свою).
ALARM_STATE_DIR_ENV = "SPT_APP_ALARM_STATE_DIR"
DEFAULT_ALARM_SOUND = r"C:\Windows\Media\Alarm01.wav"


def _idle_elapsed_ms(current_ticks, last_input_ticks):
    """Unsigned 32-bit tick delta used by LASTINPUTINFO.dwTime."""
    return (int(current_ticks) - int(last_input_ticks)) & 0xFFFFFFFF


# ----------------- Icon -----------------
def load_app_icon():
    base = os.path.dirname(os.path.abspath(__file__))
    png_path = os.path.join(base, APP_ICON_PNG)
    ico_path = os.path.join(base, APP_ICON_ICO)
    try:
        if os.path.exists(png_path) and Image is not None:
            img = Image.open(png_path).convert("RGBA")
            return img
        if os.path.exists(ico_path):
            if Image is not None:
                img = Image.open(ico_path).convert("RGBA")
                return img
            return None
    except Exception:
        return None
    return None

# ----------------- Alarm engine (порт из плагина sleep-pause-timer) -----------------
class AlarmError(ValueError):
    pass


def default_alarm_state_path():
    base = os.environ.get(ALARM_STATE_DIR_ENV)
    if not base:
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "SleepPauseTimer" / "alarm.json"


class AlarmStore:
    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def save(self, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


def normalize_sound_path(path):
    raw = str(path or "").strip()
    if not raw:
        raise AlarmError("Укажите путь к аудиофайлу")
    expanded = os.path.expandvars(os.path.expanduser(raw))
    candidate = Path(expanded)
    if not candidate.is_file():
        raise AlarmError(f"Аудиофайл не найден: {expanded}")
    return str(candidate)


class WmpPlayer:
    """Windows Media Player (legacy wmplayer.exe) через subprocess — надежно играет MP3, WAV, WMA, M4A на Windows."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._process = None
        self._wmp_path = self._find_wmplayer()

    def _find_wmplayer(self):
        """Найти путь к wmplayer.exe в стандартных местах Windows."""
        candidates = [
            r"C:\Program Files (x86)\Windows Media Player\wmplayer.exe",
            r"C:\Program Files\Windows Media Player\wmplayer.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        # Фоллбэк — полагаемся на PATH (редко работает)
        return "wmplayer.exe"

    def play_once(self, path, should_continue=None):
        log = logging.getLogger("alarm")

        if '"' in path:
            raise AlarmError("Путь к аудиофайлу не должен содержать кавычки")

        if not Path(path).exists():
            raise AlarmError(f"Аудиофайл не найден: {path}")

        self._stop_event.clear()

        try:
            # wmplayer.exe (legacy Windows Media Player) с флагами /play /close:
            # /play — начать воспроизведение сразу, /close — закрыть после завершения.
            cmd = [self._wmp_path, "/play", "/close", path]

            log.info("Starting wmplayer.exe: %s", " ".join(cmd))

            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            proc = subprocess.Popen(
                cmd,
                creationflags=creation_flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self._process = proc
            log.info("Started wmplayer.exe PID=%d for: %s", proc.pid, path)

            # Ждём завершения процесса или стоп-события
            while not self._stop_event.wait(0.2):
                if should_continue is not None:
                    try:
                        allowed = should_continue()
                    except Exception:
                        allowed = False
                    if not allowed:
                        break
                # Проверяем, не завершился ли процесс сам
                ret = proc.poll()
                if ret is not None:
                    log.info("WMP process exited with code %d", ret)
                    break

            return True
        finally:
            self.stop()

    def stop(self):
        self._stop_event.set()
        if self._process:
            try:
                if self._process.poll() is None:
                    self._process.terminate()
                    self._process.wait(timeout=2)
            except Exception:
                pass
            self._process = None


class AlarmEngine:
    def __init__(self, state_path=None, now_fn=None, player=None):
        self._store = AlarmStore(state_path or default_alarm_state_path())
        self._player = player or WmpPlayer()
        self._now = now_fn or datetime.now
        self._condition = threading.Condition(threading.RLock())
        self._generation = 0
        self._thread = None
        self._scheduled = False
        self._playing = False
        self._hour = 0
        self._minute = 0
        self._repeat = True
        self._interval = 5
        self._days_of_week = [0, 1, 2, 3, 4, 5, 6]  # 0=Mon, 6=Sun
        self._sound_path = ""
        self._monthly_alarms = {}  # date_str -> list of {"hour": h, "minute": m}
        self._next_weekly_trigger = None
        self._next_trigger = None
        self._next_trigger_type = None
        self._next_trigger_date = None
        self._outcome = ""
        self._info = ""
        self._storage_error = ""
        with self._condition:
            self._restore_locked()
            self._start_thread_locked()

    def _restore_locked(self):
        data = self._store.load()
        self._sound_path = str(data.get("sound_path", "") or "")

        monthly = data.get("monthly_alarms")
        restored_monthly = {}
        if isinstance(monthly, dict):
            for date_str, stored in monthly.items():
                try:
                    date_str = str(date_str)
                    datetime.strptime(date_str, "%Y-%m-%d")
                    # Earlier versions stored one object per date. Accept both
                    # that format and the new list of times without data loss.
                    candidates = stored if isinstance(stored, list) else [stored]
                    alarms = []
                    seen = set()
                    for alarm in candidates:
                        if not isinstance(alarm, dict):
                            continue
                        hour, minute = int(alarm["hour"]), int(alarm["minute"])
                        if not (0 <= hour <= 23 and 0 <= minute <= 59):
                            continue
                        key = (hour, minute)
                        if key not in seen:
                            alarms.append({"hour": hour, "minute": minute})
                            seen.add(key)
                    if alarms:
                        restored_monthly[date_str] = sorted(alarms, key=lambda item: (item["hour"], item["minute"]))
                except (KeyError, TypeError, ValueError):
                    continue
        self._monthly_alarms = restored_monthly

        alarm = data.get("alarm")
        if isinstance(alarm, dict):
            try:
                hour = int(alarm["hour"])
                minute = int(alarm["minute"])
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise ValueError
                repeat = bool(alarm.get("repeat", True))
                interval = int(alarm.get("interval", 5))
                if interval < 1:
                    interval = 5
                days_of_week = alarm.get("days_of_week")
                if days_of_week is None:
                    days_of_week = [0, 1, 2, 3, 4, 5, 6]
                elif not isinstance(days_of_week, list) or len(days_of_week) == 0:
                    days_of_week = [0, 1, 2, 3, 4, 5, 6]
                if any(type(day) is not int or not 0 <= day <= 6 for day in days_of_week):
                    raise ValueError("invalid days_of_week")
                days_of_week = sorted(set(days_of_week))
            except (KeyError, TypeError, ValueError):
                hour = None
            else:
                self._scheduled = True
                self._hour = hour
                self._minute = minute
                self._repeat = repeat
                self._interval = interval
                self._days_of_week = days_of_week
                self._next_weekly_trigger = self._next_occurrence(hour, minute)

        if not self._scheduled and not self._monthly_alarms:
            return

        self._refresh_next_trigger_locked(self._now())
        self._info = "будильник и месячные будильники восстановлены" if self._scheduled and self._monthly_alarms else (
            "будильник восстановлен" if self._scheduled else "месячные будильники восстановлены"
        )
        self._start_thread_locked()

    def _claim_monthly_alarm_locked(self, date_str, hour, minute):
        """Снимает одно сработавшее время и сохраняет остальные на дату."""
        alarms = self._monthly_alarms.get(date_str, [])
        alarm_index = next((index for index, item in enumerate(alarms)
                            if int(item.get("hour", -1)) == int(hour)
                            and int(item.get("minute", -1)) == int(minute)), None)
        if alarm_index is None:
            return False
        alarms.pop(alarm_index)
        if alarms:
            self._monthly_alarms[date_str] = alarms
        else:
            self._monthly_alarms.pop(date_str, None)
        self._save_locked()
        return True

    def _next_monthly_trigger_locked(self, now):
        trigger = None
        trigger_date = None
        expired_dates = []
        for date_str, alarms in list(self._monthly_alarms.items()):
            try:
                date_value = datetime.strptime(str(date_str), "%Y-%m-%d")
                if date_value.date() < now.date():
                    expired_dates.append(date_str)
                    continue
                for alarm in alarms:
                    hour = int(alarm["hour"])
                    minute = int(alarm["minute"])
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        continue
                    candidate = datetime(date_value.year, date_value.month, date_value.day, hour, minute)
                    if trigger is None or candidate < trigger:
                        trigger = candidate
                        trigger_date = date_str
            except (KeyError, TypeError, ValueError):
                expired_dates.append(date_str)
        for date_str in expired_dates:
            self._monthly_alarms.pop(date_str, None)
        if expired_dates:
            self._save_locked()
        return trigger, trigger_date

    def _refresh_next_trigger_locked(self, now):
        monthly_trigger, monthly_date = self._next_monthly_trigger_locked(now)
        weekly_trigger = self._next_weekly_trigger if self._scheduled else None

        if weekly_trigger is None:
            self._next_trigger = monthly_trigger
            self._next_trigger_type = "monthly" if monthly_trigger is not None else None
            self._next_trigger_date = monthly_date
            return

        if monthly_trigger is None or weekly_trigger <= monthly_trigger:
            self._next_trigger = weekly_trigger
            self._next_trigger_type = "weekly"
            self._next_trigger_date = None
        else:
            self._next_trigger = monthly_trigger
            self._next_trigger_type = "monthly"
            self._next_trigger_date = monthly_date

    def _next_occurrence(self, hour, minute, after=None):
        """Ближайшее HH:MM строго после `after` (по умолчанию — после «сейчас»),
        учитывая дни недели из self._days_of_week.

        Интервал повтора здесь ни при чём: он задаёт паузу между звонками уже
        зазвонившего будильника (см. _run), а не шаг расписания.
        """
        base = after if after is not None else self._now()
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            candidate += timedelta(days=1)
        
        # Если заданы дни недели, ищем ближайший подходящий день
        if hasattr(self, '_days_of_week') and self._days_of_week:
            # Не доверяем внутреннему состоянию без конца: внешние/старые
            # сохранённые данные могли содержать неверные дни.
            for _ in range(7):
                if candidate.weekday() in self._days_of_week:
                    break
                candidate += timedelta(days=1)
        
        return candidate

    def _payload_locked(self):
        if not self._scheduled:
            return {"sound_path": self._sound_path, "monthly_alarms": self._monthly_alarms}
        return {
            "sound_path": self._sound_path,
            "alarm": {
                "hour": self._hour,
                "minute": self._minute,
                "repeat": self._repeat,
                "interval": self._interval,
                "days_of_week": self._days_of_week,
            },
            "monthly_alarms": self._monthly_alarms,
        }

    def _save_locked(self):
        try:
            self._store.save(self._payload_locked())
            self._storage_error = ""
        except OSError as exc:
            self._storage_error = str(exc)

    def _start_thread_locked(self):
        self._generation += 1
        thread = threading.Thread(target=self._run, daemon=True)
        self._thread = thread
        thread.start()

    def set_alarm(self, hour, minute, sound_path, repeat=True, interval=5, days_of_week=None):
        normalized_path = normalize_sound_path(sound_path)
        hour = int(hour)
        minute = int(minute)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise AlarmError("Время будильника должно быть в диапазоне 00:00–23:59")
        interval = max(1, int(interval))
        if days_of_week is None:
            days_of_week = [0, 1, 2, 3, 4, 5, 6]
        elif not isinstance(days_of_week, list) or len(days_of_week) == 0:
            days_of_week = [0, 1, 2, 3, 4, 5, 6]
        # Validate days of week (0-6)
        if not isinstance(days_of_week, list) or not days_of_week or any(
            type(d) is not int or not 0 <= d <= 6 for d in days_of_week
        ):
            raise AlarmError("Выберите дни недели из диапазона 0-6 (0=Пн, 6=Вс)")
        days_of_week = sorted(set(days_of_week))
        with self._condition:
            self._generation += 1
            try:
                self._player.stop()
            except Exception:
                pass
            self._scheduled = True
            self._playing = False
            self._hour = hour
            self._minute = minute
            self._repeat = bool(repeat)
            self._interval = interval
            self._days_of_week = days_of_week
            self._sound_path = normalized_path
            self._next_weekly_trigger = self._next_occurrence(hour, minute)
            self._refresh_next_trigger_locked(self._now())
            self._outcome = ""
            self._info = "будильник установлен"
            self._storage_error = ""
            self._save_locked()
            self._start_thread_locked()
            return self._state_locked()

    def set_sound(self, sound_path):
        normalized_path = normalize_sound_path(sound_path)
        with self._condition:
            self._sound_path = normalized_path
            self._storage_error = ""
            self._save_locked()
            return self._state_locked()

    def cancel(self):
        with self._condition:
            was_active = self._scheduled or self._playing
            self._generation += 1
            try:
                self._player.stop()
            except Exception:
                pass
            self._scheduled = False
            self._playing = False
            self._next_weekly_trigger = None
            self._outcome = "cancelled" if was_active else ""
            self._info = "будильник отменён" if was_active else "будильник не установлен"
            self._storage_error = ""
            # Месячные будильники отмена недельного не трогает: поток должен
            # остаться жить, иначе они перестанут звонить.
            self._refresh_next_trigger_locked(self._now())
            self._save_locked()
            self._condition.notify_all()
            if self._next_trigger is not None:
                self._start_thread_locked()
            return self._state_locked()

    def stop_playback(self):
        """Глушит текущий звонок, не отменяя расписание.

        Повторяющийся будильник остаётся и звонит завтра в то же время,
        одиночный после состоявшегося звонка больше не активен.
        """
        with self._condition:
            was_playing = self._playing
            self._generation += 1
            try:
                self._player.stop()
            except Exception:
                pass
            self._playing = False
            self._outcome = "stopped" if was_playing else ""
            if was_playing and self._scheduled:
                if self._repeat:
                    self._next_weekly_trigger = self._next_occurrence(
                        self._hour,
                        self._minute,
                        after=self._now(),
                    )
                    self._info = "воспроизведение остановлено; следующий звонок завтра"
                else:
                    self._scheduled = False
                    self._next_weekly_trigger = None
                    self._info = "воспроизведение остановлено"
            else:
                self._info = "воспроизведение остановлено" if was_playing else "будильник не играл"
            self._refresh_next_trigger_locked(self._now())
            rescheduled = self._next_trigger is not None
            self._storage_error = ""
            self._save_locked()
            self._condition.notify_all()
            if rescheduled:
                self._start_thread_locked()
            return self._state_locked()

    def _playback_allowed(self, generation):
        with self._condition:
            return generation == self._generation and self._playing

    def _run(self):
        log = logging.getLogger("alarm")
        with self._condition:
            generation = self._generation
        log.info("Alarm thread started, generation=%d", generation)
        while True:
            with self._condition:
                if generation != self._generation:
                    log.info("Alarm thread exiting: generation mismatch")
                    return
                if self._playing:
                    self._condition.wait()
                    continue

                now = self._now()
                self._refresh_next_trigger_locked(now)
                trigger = self._next_trigger
                if trigger is None:
                    log.info("No triggers scheduled, stopping thread")
                    return

                delay = (trigger - now).total_seconds()
                if delay > 0:
                    log.debug("Alarm waiting %.1f seconds until %s (%s)", delay, trigger, self._next_trigger_type)
                    # Ограничение обязательно: Windows-лок падает с OverflowError
                    # при timeout > ~24.8 суток (месячный будильник далеко впереди).
                    # Часовые «пробуждения» дешёвы и заодно пересчитывают расписание.
                    self._condition.wait(timeout=min(delay, 3600.0))
                    continue

                trigger_type = self._next_trigger_type
                trigger_date = self._next_trigger_date
                self._playing = True
                self._outcome = "fired"
                self._info = "будильник сработал"

                if trigger_type == "weekly":
                    hour = self._hour
                    minute = self._minute
                    repeat = self._repeat
                    interval = self._interval
                    sound_path = self._sound_path
                else:
                    if not self._claim_monthly_alarm_locked(trigger_date, trigger.hour, trigger.minute):
                        self._playing = False
                        self._refresh_next_trigger_locked(self._now())
                        continue
                    hour = trigger.hour
                    minute = trigger.minute
                    repeat = False
                    interval = 5
                    sound_path = self._sound_path

                log.info("Alarm firing at %s, sound=%s, type=%s", now.strftime("%H:%M:%S"), sound_path, trigger_type)

            try:
                while self._playback_allowed(generation):
                    self._player.play_once(
                        sound_path,
                        lambda: self._playback_allowed(generation),
                    )
                    if not repeat or not self._playback_allowed(generation):
                        break
                    with self._condition:
                        self._condition.wait(timeout=interval * 60)
            except Exception as exc:
                with self._condition:
                    if generation != self._generation:
                        return
                    self._playing = False
                    self._scheduled = False
                    self._next_weekly_trigger = None
                    self._next_trigger = None
                    self._next_trigger_type = None
                    self._next_trigger_date = None
                    self._outcome = "error"
                    self._info = f"ошибка воспроизведения: {exc}"
                    self._storage_error = ""
                    self._save_locked()
                    log.error("Alarm playback error: %s", exc)
                return

            with self._condition:
                if generation != self._generation:
                    return
                self._playing = False
                if trigger_type == "weekly" and repeat:
                    self._next_weekly_trigger = self._next_occurrence(
                        hour,
                        minute,
                        after=self._now(),
                    )
                    self._info = "будильник сработал; следующий запуск подготовлен"
                elif trigger_type == "weekly":
                    self._scheduled = False
                    self._next_weekly_trigger = None
                    self._info = "будильник сработал"

                self._refresh_next_trigger_locked(self._now())
                self._outcome = "fired"
                self._storage_error = ""
                self._save_locked()
                log.info(
                    "Alarm finished, scheduled=%s, next_trigger=%s, monthly_count=%d",
                    self._scheduled,
                    self._next_trigger,
                    sum(len(items) for items in self._monthly_alarms.values()),
                )
                if self._next_trigger is None:
                    return

    def _state_locked(self):
        now = self._now()
        if self._scheduled and self._next_weekly_trigger is None:
            # Не показываем «красивый» триггер, которого нет у потока:
            # пересчитываем честное расписание.
            self._next_weekly_trigger = self._next_occurrence(self._hour, self._minute)
        self._refresh_next_trigger_locked(now)
        trigger = self._next_trigger
        remaining = 0
        if trigger is not None:
            remaining = max(0, int((trigger - now).total_seconds()))
        if self._sound_path:
            try:
                sound_name = Path(self._sound_path).name
            except (OSError, ValueError):
                sound_name = self._sound_path
        else:
            sound_name = ""
        if self._playing:
            status = "playing"
        elif self._scheduled:
            status = "scheduled"
        else:
            status = "idle"
        return {
            "scheduled": self._scheduled,
            "playing": self._playing,
            "status": status,
            "hour": self._hour if self._scheduled else 0,
            "minute": self._minute if self._scheduled else 0,
            "repeat": self._repeat,
            "interval": self._interval,
            "days_of_week": self._days_of_week,
            "sound_path": self._sound_path,
            "sound_name": sound_name,
            "next_time": trigger.strftime("%H:%M") if trigger else "",
            "next_datetime": trigger.isoformat(timespec="seconds") if trigger else "",
            "remaining_seconds": remaining,
            "outcome": self._outcome,
            "info": self._info,
            "storage_error": self._storage_error,
            "monthly_count": sum(len(items) for items in self._monthly_alarms.values()),
        }

    def state(self):
        with self._condition:
            return self._state_locked()

    def get_monthly_alarms(self):
        """Возвращает копию расписания: дата -> список времён."""
        with self._condition:
            return {day: [dict(item) for item in alarms]
                    for day, alarms in self._monthly_alarms.items()}

    def set_monthly_alarm(self, date_obj, hour, minute, sound_path=None):
        """Добавляет время на дату; пустые hour/minute удаляют все её времена."""
        normalized_path = normalize_sound_path(sound_path) if sound_path else self._sound_path
        date_str = date_obj.isoformat()
        with self._condition:
            if hour is None or minute is None:
                existed = date_str in self._monthly_alarms
                if existed:
                    del self._monthly_alarms[date_str]
                    self._save_locked()
                self._condition.notify_all()
                return existed

            hour, minute = int(hour), int(minute)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise AlarmError("Время будильника должно быть в диапазоне 00:00–23:59")
            scheduled_at = datetime.combine(date_obj, datetime.min.time()).replace(hour=hour, minute=minute)
            if scheduled_at <= self._now():
                raise AlarmError("Выберите дату и время в будущем")
            alarms = self._monthly_alarms.setdefault(date_str, [])
            if any(item["hour"] == hour and item["minute"] == minute for item in alarms):
                return False
            alarms.append({"hour": hour, "minute": minute})
            alarms.sort(key=lambda item: (item["hour"], item["minute"]))
            self._save_locked()
            self._start_thread_locked()
            self._condition.notify_all()
            return True

    def cancel_monthly_alarm(self, date_obj, hour, minute):
        """Удаляет только указанное время, сохраняя другие на этой дате."""
        date_str = date_obj.isoformat()
        hour, minute = int(hour), int(minute)
        with self._condition:
            alarms = self._monthly_alarms.get(date_str, [])
            remaining = [item for item in alarms
                         if (item["hour"], item["minute"]) != (hour, minute)]
            removed = len(remaining) != len(alarms)
            if removed:
                if remaining:
                    self._monthly_alarms[date_str] = remaining
                else:
                    self._monthly_alarms.pop(date_str, None)
                self._save_locked()
                self._start_thread_locked()
                self._condition.notify_all()
            return removed

    def stop_thread(self):
        with self._condition:
            self._generation += 1
            try:
                self._player.stop()
            except Exception:
                pass
            self._playing = False
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)


# ----------------- Main app -----------------
class SleepPauseApp:
    def __init__(self, root):
        self.root = root
        root.title("Sleep Pause Timer")
        root.resizable(False, False)

        # Theme
        self.style = ttk.Style()
        self.current_theme = "dark"

        # Отключаем takefocus для combobox через стиль
        self.style.configure("TCombobox", takefocus=0)

        # State
        self.stop_event = threading.Event()
        self.worker = None
        self.paused = False
        self.tray_icon = None
        self._icon_image = None
        self.current_remaining = 0

        # Для детекта окон: список заголовков, индексируемый с 1
        self.detected_windows = []  # list of strings

        # Для отслеживания бездействия мыши/клавиатуры
        self.idle_stop_event = threading.Event()
        self.idle_counter_thread = None
        self.idle_alert_window = None
        self.idle_action_executed = False  # Флаг: действие уже выполнено

        # Будильник: движок восстанавливает сохранённое расписание сам
        self.alarm = AlarmEngine()
        self._alarm_notified = False
        self._alarm_last_signature = None
        self._alarm_after_id = None

        # Load icon - используем большой размер для панели задач (512 для высокого DPI)
        self.app_icon_img = load_app_icon()
        
        # Пробуем использовать .ico для панели задач (лучше для Windows)
        try:
            ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), APP_ICON_ICO)
            if os.path.exists(ico_path):
                root.iconbitmap(ico_path)
            elif self.app_icon_img is not None and ImageTk is not None:
                # Если .ico нет, используем PNG с большим размером (512)
                tk_img = ImageTk.PhotoImage(self.app_icon_img.resize((512, 512), Image.LANCZOS))
                root.iconphoto(False, tk_img)
                self._tk_icon_ref = tk_img
        except Exception:
            pass

        self.preferences_path = default_alarm_state_path().parent / "ui-settings.json"
        self.preferences = load_preferences(self.preferences_path)
        self.current_theme = self.preferences["theme"]
        self.app_version = installed_version()
        self._pulse_after_id = None
        self._status_pulse_phase = 0.0
        self._button_anim_after_ids = {}
        self.button_roles = {}
        self._apply_theme_palette()
        build_interface(self)
        self.updater = UpdateController(self)
        self._set_status("Готово к запуску", self.success, pulse=False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Кнопка «–» при активном таймере/отслеживании прячет окно в трей
        root.bind("<Unmap>", self._on_root_unmap)

        # Сброс кнопок при потере фокуса окном (клик на рабочий стол)
        root.bind("<FocusOut>", lambda e: self._reset_all_buttons())
        
        # Windows LASTINPUTINFO already tracks mouse and keyboard activity.
        # Input events must not clear a stopped worker's cancellation event.

        # Поллер будильника: статус, кнопка, трей, уведомление о звонке
        self._alarm_tick(force=True)

    # ---------- UI helpers ----------
    def _hex_to_rgb(self, value):
        value = value.lstrip("#")
        return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))

    def _rgb_to_hex(self, rgb):
        r, g, b = [max(0, min(255, int(v))) for v in rgb]
        return f"#{r:02x}{g:02x}{b:02x}"

    def _mix_color(self, c1, c2, t):
        r1, g1, b1 = self._hex_to_rgb(c1)
        r2, g2, b2 = self._hex_to_rgb(c2)
        return self._rgb_to_hex((
            r1 + (r2 - r1) * t,
            g1 + (g2 - g1) * t,
            b1 + (b2 - b1) * t,
        ))

    def _lighten(self, color, amount=0.12):
        return self._mix_color(color, "#ffffff", amount)

    def _darken(self, color, amount=0.12):
        return self._mix_color(color, "#000000", amount)

    def _apply_theme_palette(self):
        apply_palette(self)

    def _apply_button_palette(self):
        for button, role in self.button_roles.items():
            base_color = self.button_palette.get(role, self.accent)
            fg = "#101722" if role in ("accent", "success") else self.input_fg
            if role == "error":
                fg = self.error
            elif role == "warning":
                fg = self.warning

            # Вычисляем glow-цвет (светлая обводка)
            glow_color = self._lighten(base_color, 0.35)
            button._base_bg = base_color
            button._hover_bg = self._lighten(base_color, 0.25)  # заметнее при наведении
            button._press_bg = self._darken(base_color, 0.20)  # заметнее при нажатии
            button._glow_color = glow_color
            button._role = role

            button.config(
                bg=base_color,
                fg=fg,
                activebackground=button._hover_bg,
                disabledforeground="#9ca3af" if self.current_theme == "dark" else "#94a3b8",
                relief="flat",
            )

    # ---------- Idle (бездействие) ----------
    def _ui_call(self, fn):
        """Безопасно планирует вызов в главном потоке Tk.

        Ошибки планирования (например, при закрытии окна) не должны
        ронять поток отслеживания бездействия.
        """
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def _start_idle_counter(self):
        """Запуск потока отслеживания бездействия."""
        idle_minutes = 0
        try:
            idle_minutes = int(self.idle_spin.get())
        except ValueError:
            pass
        if idle_minutes <= 0:
            return  # функция отключена
        
        # Each run owns its cancellation token; an old worker cannot resume.
        stop_event = self.idle_stop_event = threading.Event()
        
        import ctypes
        
        def thread():
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            
            # Создаём структуру LASTINPUTINFO
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("dwTime", ctypes.c_uint),
                ]
            
            # action_cooldown_until — метка времени, до которой игнорируем «активность»:
            # после нажатия клавиши через pyautogui системный idle-таймер сбрасывается,
            # и без этой паузы приложение считало бы, что пользователь вернулся.
            action_cooldown_until = 0.0
            
            def ui(fn):
                self._ui_call(lambda: fn() if not stop_event.is_set() else None)

            while not stop_event.is_set():
                # Ждём 1 секунду
                if stop_event.wait(1.0):
                    break  # остановка
                
                # Получаем время последнего ввода
                try:
                    lli = LASTINPUTINFO()
                    lli.cbSize = ctypes.sizeof(LASTINPUTINFO)
                    user32.GetLastInputInfo(ctypes.byref(lli))
                    # LASTINPUTINFO.dwTime is a 32-bit tick count. Subtracting
                    # it from GetTickCount64 breaks after Windows uptime wraps.
                    now = kernel32.GetTickCount()
                    idle_ms = _idle_elapsed_ms(now, lli.dwTime)
                    idle_secs = idle_ms / 1000.0
                except Exception:
                    continue
                
                in_cooldown = time.time() < action_cooldown_until
                
                # Если пользователь вернулся (реальная активность, не наш ввод) — сбрасываем флаг
                if idle_secs < 2.0 and self.idle_action_executed and not in_cooldown:
                    self.idle_action_executed = False
                    ui(lambda: self._set_status("✓ Вы вернулись — отслеживание продолжено", self.success, pulse=False))
                
                # Если предупреждение открыто и пользователь активен - закрываем его
                if self.idle_alert_window is not None and idle_secs < 2.0 and not in_cooldown:
                    alert_ref = self.idle_alert_window
                    ui(lambda ref=alert_ref: self._cancel_idle_alert(ref))
                
                # Проверяем порог
                if idle_secs >= idle_minutes * 60 and not self.idle_action_executed:
                    # Время бездействия истекло — выполняем действие
                    self.idle_action_executed = True  # Устанавливаем флаг ДО нажатия
                    action_cooldown_until = time.time() + 15  # игнорируем ввод 15 сек после действия
                    ui(self._on_idle_timeout)
                    # Закрываем предупреждение если есть
                    if self.idle_alert_window is not None:
                        alert_ref = self.idle_alert_window
                        ui(lambda ref=alert_ref: self._cancel_idle_alert(ref))
                    continue
                
                # Показываем предупреждение за 5 минут до истечения
                # Но только если действие ещё не выполнялось
                if not self.idle_action_executed:
                    time_left = (idle_minutes * 60) - idle_secs
                    if 0 < time_left <= 300:  # 5 минут = 300 сек
                        if self.idle_alert_window is None:
                            self._idle_warning_remaining_secs = time_left
                            ui(lambda: self._show_idle_alert(idle_minutes))
        
        self.idle_counter_thread = threading.Thread(target=thread, daemon=True)
        self.idle_counter_thread.start()

    def _stop_idle_counter(self):
        """Остановка потока отслеживания бездействия."""
        self.idle_stop_event.set()
        self.idle_counter_thread = None
        # Закрываем предупреждение если есть
        if self.idle_alert_window is not None:
            try:
                self.idle_alert_window.destroy()
            except Exception:
                pass
            self.idle_alert_window = None
        # Убираем уведомление в трее, если показывали его
        try:
            if self.tray_icon is not None:
                self.tray_icon.remove_notification()
        except Exception:
            pass

    def _show_idle_alert(self, idle_minutes):
        """Показывает предупреждение за 5 минут до срабатывания бездействия."""
        # Если окно спрятано в трей — предупреждаем уведомлением от значка в трее
        if self._is_window_hidden() and self.tray_icon is not None:
            self.idle_alert_window = True  # маркер «предупреждение активно»
            try:
                self.tray_icon.notify(
                    f"Скоро будет выполнено действие:\n{ACTION_TITLES.get(self.key_var.get(), self.key_var.get())}\n\n"
                    "Подвигайте мышь, чтобы отменить.",
                    "⏳ Sleep Pause Timer",
                )
            except Exception as e:
                print(f"DEBUG: tray notify error: {e}")
            return
        try:
            try:
                ctypes.windll.user32.MessageBeep(ctypes.c_uint(-1))
            except Exception:
                pass
            show_idle_warning(self, idle_minutes)
        except Exception as exc:
            logging.warning("Cannot show inactivity warning: %s", exc)

    def _cancel_idle_alert(self, alert=None):
        """Отменяет предупреждение при активности."""
        if self.idle_alert_window is None:
            return
        self.idle_alert_window = None
        # Убираем уведомление в трее, если показывали его
        try:
            if self.tray_icon is not None:
                self.tray_icon.remove_notification()
        except Exception:
            pass
        if alert is not None:
            try:
                alert.destroy()
            except Exception:
                pass

    def _on_idle_timeout(self):
        """Выполняет действие при истечении времени бездействия."""
        cancellation = self.idle_stop_event
        if cancellation.is_set():
            return
        self._set_status("⏳ Бездействие: действие выполняется...", self.warning, pulse=False)
        
        # Вызываем то же действие, что и при завершении таймера
        key = self.key_var.get()
        title = self.win_title_entry.get().strip()
        
        # Выполняем действие
        if key == "shutdown":
            self.root.after(0, lambda: self._set_status("🔴 Выключение...", self.warning, pulse=False))
            self.root.after(1000, lambda: None if cancellation.is_set() else ctypes.windll.user32.ExitWindowsEx(0x00000001 | 0x00000004, 0xFFFFFFFF))
            return
        
        elif key == "sleep":
            self.root.after(0, lambda: self._set_status("💤 Спящий режим...", self.warning, pulse=False))
            self.root.after(1000, lambda: None if cancellation.is_set() else ctypes.windll.PowrProf.SetSuspendState(0, 0, 0))
            return
        
        if pyautogui is None:
            self.root.after(0, lambda: messagebox.showerror("Ошибка", "pyautogui не установлен"))
            return
        
        # Активируем окно и отправляем клавишу
        def send_key():
            if cancellation.is_set():
                return
            if title:
                self._activate_window(title)
            
            try:
                pyautogui.press(key)
                self.root.after(0, lambda: self._set_status("✓ Клавиша отправлена!", self.success, pulse=False))
            except Exception as e:
                self.root.after(0, lambda err=e: messagebox.showerror("Ошибка", f"Не удалось отправить клавишу '{key}': {err}"))
        
        if title:
            self._activate_window(title)
            self.root.after(200, send_key)  # Даем 200мс на активацию окна
        else:
            send_key()
        
        # Отслеживание продолжается — кнопки не сбрасываем,
        # остаётся "⏳ Отслеживание..." и кнопка отмены бездействия
        self.root.after(0, lambda: self._set_status("✓ Действие выполнено — отслеживание продолжается", self.success, pulse=False))

    def _set_button_state(self, button, enabled=True):
        if enabled:
            button.config(state="normal", bg=button._base_bg, activebackground=button._hover_bg,
                         relief="flat", overrelief="raised")
        else:
            disabled_color = self.button_palette["disabled"]
            button.config(state="disabled", bg=disabled_color, activebackground=disabled_color,
                         relief="flat", overrelief="flat")

    def _reset_all_buttons(self):
        """Сброс всех кнопок в плоское состояние при клике на фон"""
        for btn in self.button_roles.keys():
            if str(btn.cget("state")) != "disabled":
                btn.config(relief="flat")
                btn.config(bg=btn._base_bg)

    def _set_status(self, text, color=None, pulse=False):
        if color is None:
            color = self.input_fg
        self.status.config(text=text, fg=color)
        if self._pulse_after_id is not None:
            try:
                self.root.after_cancel(self._pulse_after_id)
            except Exception:
                pass
            self._pulse_after_id = None
        if pulse:
            self._status_pulse_phase = 0.0
            self._run_status_pulse(color)

    def _run_status_pulse(self, base_color):
        self._status_pulse_phase += 0.23
        intensity = (math.sin(self._status_pulse_phase) + 1.0) / 2.0
        pulse_color = self._mix_color(base_color, self._lighten(base_color, 0.35), 0.15 + intensity * 0.35)
        try:
            self.status.config(fg=pulse_color)
        except Exception:
            return
        self._pulse_after_id = self.root.after(90, lambda: self._run_status_pulse(base_color))

    def _animate_progress_to(self, target_value, duration_ms=260):
        try:
            start_value = float(self.progress["value"])
        except Exception:
            self.progress.config(value=target_value)
            return

        target_value = float(target_value)
        steps = max(1, int(duration_ms / 16))
        delta = target_value - start_value

        def step(i=1):
            t = i / steps
            eased = 1 - (1 - t) * (1 - t)
            value = start_value + delta * eased
            try:
                self.progress.config(value=value)
            except Exception:
                return
            if i < steps:
                self.root.after(16, lambda: step(i + 1))

        step()

    def toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme_palette()
        self.update_ui_colors()
        try:
            save_preferences(self)
        except OSError as exc:
            logging.warning("Cannot save appearance settings: %s", exc)

    def update_ui_colors(self):
        apply_colors(self)

    # ---------- Timer ----------
    def start(self):
        key = self.key_var.get()
        # pyautogui не требуется для shutdown и sleep
        if pyautogui is None and key not in ("shutdown", "sleep"):
            messagebox.showerror("Ошибка", "Требуется пакет pyautogui. Установите: pip install pyautogui")
            return
        try:
            hours = int(self.entry_hour.get())
            mins = int(self.entry_min.get())
            secs = int(self.entry_sec.get())
        except ValueError:
            messagebox.showerror("Ошибка", "Введите числа в поля часов, минут и секунд")
            return
        total = hours * 3600 + mins * 60 + secs
        if total <= 0:
            messagebox.showerror("Ошибка", "Время должно быть больше нуля")
            return

        self.stop_event.clear()
        self.paused = False
        self._set_button_state(self.btn_start, enabled=False)
        self._set_button_state(self.btn_pause, enabled=True)
        self.btn_pause.config(text="⏸ Пауза")
        self._set_button_state(self.btn_cancel, enabled=True)
        self.progress["maximum"] = total
        self.progress["value"] = 0
        self._set_status("⏳ Таймер запущен", self.accent, pulse=True)
        
        self.worker = threading.Thread(target=self._timer_thread, args=(total, key), daemon=True)
        self.worker.start()

    def start_idle(self):
        """Запуск только отслеживания бездействия (без основного таймера)."""
        if self.idle_counter_thread is not None and self.idle_counter_thread.is_alive():
            return
        idle_minutes = 0
        try:
            idle_minutes = int(self.idle_spin.get())
        except ValueError:
            pass
        
        if idle_minutes <= 0:
            messagebox.showerror("Ошибка", "Укажите время бездействия (минуты > 0)")
            return
        
        key = self.key_var.get()
        
        # pyautogui не требуется для shutdown и sleep
        if key not in ("shutdown", "sleep") and pyautogui is None:
            messagebox.showerror("Ошибка", "Требуется пакет pyautogui. Установите: pip install pyautogui")
            return
        
        # Если выбрана команда shutdown/sleep — сразу запускаем бездействие
        self.idle_action_executed = False
        self._set_button_state(self.btn_idle, enabled=False)
        self.btn_idle.config(text="⏳ Отслеживание...")
        
        self.btn_cancel_idle.pack(side="right", padx=(8, 0))
        self._set_button_state(self.btn_cancel_idle, enabled=True)
        
        if not (self.worker and self.worker.is_alive()):
            self.progress["maximum"] = 1
            self.progress["value"] = 0
            self._set_status("⏳ Отслеживание бездействия запущено", self.accent, pulse=True)
        
        self._start_idle_counter()

    def cancel(self):
        """Остановка основного таймера."""
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self._set_status("⏹ Отмена...", self.warning, pulse=False)
            self._set_button_state(self.btn_cancel, enabled=False)
        
    def cancel_idle(self):
        """Остановка отслеживания бездействия."""
        self._stop_idle_counter()
        if not (self.worker and self.worker.is_alive()):
            self._set_status("⏹ Бездействие отменено", self.warning, pulse=False)
        self._set_button_state(self.btn_idle, enabled=True)
        self.btn_idle.config(text="⏳ Бездействие")
        
        self.btn_cancel_idle.pack_forget()

    def toggle_pause(self):
        if not self.worker or not self.worker.is_alive():
            return
        self.paused = not self.paused
        if self.paused:
            self._set_status("⏸ Пауза", self.warning, pulse=False)
            self.button_roles[self.btn_pause] = "success"
            self._apply_button_palette()
            self.btn_pause.config(text="▶ Продолжить")
            self._set_button_state(self.btn_pause, enabled=True)
        else:
            # Снимаем с паузы - сбрасываем флаг idle action
            self.idle_action_executed = False
            self._set_status("▶ Возобновлено", self.success, pulse=False)
            self.button_roles[self.btn_pause] = "warning"
            self._apply_button_palette()
            self.btn_pause.config(text="⏸ Пауза")
            self._set_button_state(self.btn_pause, enabled=True)

    # ---------- Alarm ----------
    def _alarm_pick_sound(self):
        """Выбор мелодии будильника через диалог."""
        path = filedialog.askopenfilename(
            title="Мелодия будильника",
            filetypes=[
                ("Аудиофайлы", "*.mp3;*.wav;*.wma;*.m4a;*.aac;*.flac;*.ogg;*.mid;*.midi"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self.alarm_sound_entry.delete(0, "end")
            self.alarm_sound_entry.insert(0, path)

    def _alarm_resolve_sound(self):
        """Мелодия: поле → сохранённая → системная Windows → пусто (ошибка)."""
        path = self.alarm_sound_entry.get().strip()
        if not path:
            try:
                path = str(self.alarm.state().get("sound_path", "") or "")
            except Exception:
                path = ""
        if not path and os.path.isfile(DEFAULT_ALARM_SOUND):
            path = DEFAULT_ALARM_SOUND
        return path

    def _alarm_button_click(self):
        st = self.alarm.state()
        if st.get("playing"):
            self.alarm.stop_playback()
            self._alarm_notified = False
            self._alarm_clear_tray_notification()
        elif st.get("scheduled"):
            self.alarm.cancel()
        else:
            self._alarm_apply()
        self._alarm_tick(force=True)

    def _alarm_apply(self):
        """Установить будильник по полям формы."""
        try:
            hour = int(self.alarm_hour_entry.get())
            minute = int(self.alarm_minute_entry.get())
        except ValueError:
            messagebox.showerror("Ошибка", "Введите время будильника числами (ЧЧ и ММ)")
            return
        try:
            interval = max(1, int(self.alarm_interval_spin.get()))
        except ValueError:
            messagebox.showerror("Ошибка", "Интервал повтора — целое число минут")
            return
        # Собираем выбранные дни недели
        days_of_week = [i for i, var in enumerate(self.alarm_days_vars) if var.get()]
        if not days_of_week:
            # Дни не выбраны — недельный будильник не ставим. Это нормально,
            # если в календаре заведены месячные будильники: тогда просто
            # сохраняем мелодию для них и не показываем ошибку.
            monthly_n = self.alarm.state().get("monthly_count", 0)
            if monthly_n > 0:
                sound = self._alarm_resolve_sound()
                if sound:
                    try:
                        self.alarm.set_sound(sound)
                        self.alarm_sound_entry.delete(0, "end")
                        self.alarm_sound_entry.insert(0, sound)
                    except AlarmError:
                        pass
                self._alarm_tick(force=True)
                messagebox.showinfo(
                    "Только месячные будильники",
                    "Дни недели не выбраны — повторяющийся будильник не установлен.\n"
                    f"Месячные будильники в календаре ({monthly_n}) продолжают работать.",
                )
                return
            messagebox.showerror(
                "Ошибка",
                "Выберите хотя бы один день недели или добавьте будильник в календаре",
            )
            return
        sound = self._alarm_resolve_sound()
        if not sound:
            messagebox.showerror("Ошибка", "Выберите мелодию будильника (кнопка «🎵 Мелодия»)")
            return
        try:
            self.alarm.set_alarm(hour, minute, sound,
                                 repeat=self.alarm_repeat_var.get(), interval=interval,
                                 days_of_week=days_of_week)
        except AlarmError as exc:
            messagebox.showerror("Ошибка", str(exc))
            return
        # Показываем выбранную мелодию в поле (могла прийти из сохранённой/системной)
        try:
            st = self.alarm.state()
            self.alarm_sound_entry.delete(0, "end")
            self.alarm_sound_entry.insert(0, st.get("sound_path", ""))
        except Exception:
            pass

    def _alarm_clear_tray_notification(self):
        try:
            if self.tray_icon is not None:
                self.tray_icon.remove_notification()
        except Exception:
            pass

    def _show_alarm_month_schedule(self):
        """Показывает интерактивный календарь на месяц для управления будильниками.

        Сетка строится один раз на месяц; включение/выключение будильника
        только перекрашивает нужную кнопку. Прежняя полная пересборка всех
        дней на каждое действие давала видимое мигание всего месяца.
        """
        now = datetime.now()
        current_year = now.year
        current_month = now.month

        dlg = tk.Toplevel(self.root)
        dlg.title("📅 Календарь будильников")
        # Фон самого окна — в цвет темы: иначе в отступах между фреймами
        # просвечивает системный белый (SystemButtonFace) — «белая окантовка».
        dlg.configure(bg=self.frame_bg)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        nav = {"year": current_year, "month": current_month}
        view = {"cells": {}, "alarms": {}}  # date_str -> кнопка дня / будильник

        month_names = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                       "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
        days_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

        cal_frame = tk.Frame(dlg, bg=self.frame_bg)
        cal_frame.pack(fill="both", expand=True, padx=10, pady=(6, 4))

        # Статичные части: название месяца и подписи дней недели
        header_lbl = tk.Label(cal_frame, font=("Segoe UI", 12, "bold"),
                              bg=self.frame_bg, fg=self.input_fg)
        header_lbl.grid(row=0, column=0, columnspan=7, pady=(0, 8))
        for i, d in enumerate(days_short):
            tk.Label(cal_frame, text=d, font=("Segoe UI", 9, "bold"),
                     bg=self.frame_bg, fg=self.label_fg, width=5).grid(row=1, column=i, padx=2, pady=2)

        grid_frame = tk.Frame(cal_frame, bg=self.frame_bg)
        grid_frame.grid(row=2, column=0, columnspan=7, sticky="nsew")
        for i in range(7):
            cal_frame.columnconfigure(i, weight=1)
            grid_frame.columnconfigure(i, weight=1)

        def style_cell(btn, date_str):
            """Перекрашивает существующую кнопку дня (без пересоздания)."""
            day = int(date_str[-2:])
            alarm = bool(view["alarms"].get(date_str))
            is_today = (date_str == now.date().isoformat())
            text = str(day)
            if alarm:
                bg = self.accent
                fg = "white"
                active = self._darken(self.accent, 0.2)
            else:
                bg = self.accent if is_today else self.entry_bg
                fg = "white" if is_today else self.input_fg
                active = self._lighten(bg, 0.2)
            try:
                btn.config(text=text, bg=bg, fg=fg, activebackground=active,
                           font=("Segoe UI", 9, "bold" if (is_today or alarm) else "normal"))
            except Exception:
                pass

        def refresh_cells():
            """Перечитывает будильники из движка и перекрашивает дни (без пересборки)."""
            view["alarms"] = self.alarm.get_monthly_alarms()
            for date_str, btn in view["cells"].items():
                style_cell(btn, date_str)

        def build_month():
            """Полная сборка сетки — только при смене месяца."""
            import calendar
            for widget in grid_frame.winfo_children():
                widget.destroy()
            view["cells"] = {}

            header_text = f"{month_names[nav['month'] - 1]} {nav['year']}"
            header_lbl.config(text=header_text)
            dlg.title(f"📅 {header_text} — Календарь будильников")

            cal = calendar.Calendar(firstweekday=0)  # Понедельник = 0
            month_days = cal.monthdayscalendar(nav["year"], nav["month"])
            for week_idx, week in enumerate(month_days):
                for day_idx, day in enumerate(week):
                    if day == 0:
                        continue  # Пустая ячейка
                    date_obj = datetime(nav["year"], nav["month"], day).date()
                    date_str = date_obj.isoformat()
                    btn = RoundedButton(grid_frame, text=str(day), width=5, height=2,
                                    bd=0, relief="flat", cursor="hand2", takefocus=0,
                                    activeforeground="white",
                                    highlightthickness=0, highlightbackground=self.frame_bg,
                                    highlightcolor=self.frame_bg,
                                    command=lambda d=date_obj, ds=date_str: self._toggle_monthly_alarm(
                                        d, ds, refresh_cells))
                    btn.grid(row=week_idx, column=day_idx, padx=2, pady=2, sticky="nsew")
                    view["cells"][date_str] = btn
            refresh_cells()

        # Навигация
        nav_frame = tk.Frame(dlg, bg=self.frame_bg)
        nav_frame.pack(fill="x", padx=10, pady=(0, 10))

        def prev_month():
            if nav["month"] == 1:
                nav["month"] = 12
                nav["year"] -= 1
            else:
                nav["month"] -= 1
            build_month()

        def next_month():
            if nav["month"] == 12:
                nav["month"] = 1
                nav["year"] += 1
            else:
                nav["month"] += 1
            build_month()

        RoundedButton(nav_frame, text="◀", command=prev_month, width=3,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=8, pady=4,
                  cursor="hand2", relief="flat", bg=self.neutral_btn, fg=self.input_fg,
                  takefocus=0, highlightthickness=0,
                  activebackground=self._darken(self.neutral_btn, 0.2), activeforeground=self.input_fg).pack(side="left")

        RoundedButton(nav_frame, text="Закрыть", command=dlg.destroy,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=4,
                  cursor="hand2", relief="flat", bg=self.accent, fg="white",
                  takefocus=0, highlightthickness=0,
                  activebackground=self._lighten(self.accent, 0.2), activeforeground="white").pack(side="left", padx=10)

        RoundedButton(nav_frame, text="▶", command=next_month, width=3,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=8, pady=4,
                  cursor="hand2", relief="flat", bg=self.neutral_btn, fg=self.input_fg,
                  takefocus=0, highlightthickness=0,
                  activebackground=self._darken(self.neutral_btn, 0.2), activeforeground=self.input_fg).pack(side="right")

        # Первая сборка
        build_month()

    def _show_time_picker(self, date_obj, date_str, render_callback):
        """Показывает расписание выбранной даты и добавляет отдельные времена."""
        time_dlg = tk.Toplevel(self.root)
        time_dlg.title(f"⏰ Будильники на {date_obj.strftime('%d.%m.%Y')}")
        time_dlg.configure(bg=self.frame_bg)
        time_dlg.resizable(False, False)
        time_dlg.transient(self.root)
        time_dlg.grab_set()

        tk.Label(time_dlg, text="Установленные будильники", font=("Segoe UI", 10, "bold"),
                 bg=self.frame_bg, fg=self.input_fg).pack(anchor="w", padx=15, pady=(14, 5))
        alarms_frame = tk.Frame(time_dlg, bg=self.frame_bg)
        alarms_frame.pack(fill="x", padx=15, pady=(0, 9))

        def render_alarms():
            for child in alarms_frame.winfo_children():
                child.destroy()
            alarms = self.alarm.get_monthly_alarms().get(date_str, [])
            if not alarms:
                tk.Label(alarms_frame, text="На эту дату будильников пока нет.",
                         font=("Segoe UI", 9), bg=self.frame_bg, fg=self.label_fg).pack(anchor="w")
                return
            for item in alarms:
                row = tk.Frame(alarms_frame, bg=self.entry_bg,
                               highlightthickness=1, highlightbackground=self.card_border)
                row.pack(fill="x", pady=2)
                tk.Label(row, text=f"{item['hour']:02d}:{item['minute']:02d}",
                         font=("Segoe UI", 10, "bold"), bg=self.entry_bg,
                         fg=self.input_fg).pack(side="left", padx=9, pady=5)
                def cancel_one(hour=item["hour"], minute=item["minute"]):
                    if self.alarm.cancel_monthly_alarm(date_obj, hour, minute):
                        render_alarms()
                        render_callback()
                        self._alarm_tick(force=True)
                RoundedButton(row, text="×", command=cancel_one, font=("Segoe UI", 10, "bold"),
                          bd=0, padx=8, pady=2, cursor="hand2", relief="flat",
                          bg=self.neutral_btn, fg=self.error,
                          activebackground=self._darken(self.neutral_btn, 0.15),
                          activeforeground=self.error, highlightthickness=0).pack(side="right", padx=4, pady=3)

        render_alarms()
        tk.Label(time_dlg, text="Добавить ещё одно время", font=("Segoe UI", 10, "bold"),
                 bg=self.frame_bg, fg=self.input_fg).pack(anchor="w", padx=15, pady=(4, 5))
        time_frame = tk.Frame(time_dlg, bg=self.frame_bg)
        time_frame.pack(anchor="w", padx=15, pady=(0, 8))

        hour_var = tk.StringVar(value="07")
        minute_var = tk.StringVar(value="30")
        existing = self.alarm.get_monthly_alarms().get(date_str, [])
        occupied = {(item["hour"], item["minute"]) for item in existing}
        try:
            preferred = (int(self.alarm_hour_entry.get()), int(self.alarm_minute_entry.get()))
        except (ValueError, AttributeError):
            preferred = (7, 30)
        if not (0 <= preferred[0] <= 23 and 0 <= preferred[1] <= 59) or preferred in occupied:
            preferred_minutes = ((preferred[0] * 60 + preferred[1]) if 0 <= preferred[0] <= 23 and 0 <= preferred[1] <= 59 else 450)
            preferred = next((((preferred_minutes + step * 5) // 60 % 24,
                               (preferred_minutes + step * 5) % 60)
                              for step in range(1, 289)
                              if (((preferred_minutes + step * 5) // 60 % 24,
                                   (preferred_minutes + step * 5) % 60) not in occupied)), (7, 30))
        hour_var.set(f"{preferred[0]:02d}")
        minute_var.set(f"{preferred[1]:02d}")

        hour_entry = RoundedEntry(time_frame, width=3, font=("Segoe UI", 14, "bold"),
                              bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                              insertbackground=self.accent, relief="flat",
                              highlightthickness=1, highlightbackground=self.card_border,
                              highlightcolor=self.accent, textvariable=hour_var)
        hour_entry.pack(side="left")
        tk.Label(time_frame, text=":", bg=self.frame_bg, fg=self.label_fg,
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        minute_entry = RoundedEntry(time_frame, width=3, font=("Segoe UI", 14, "bold"),
                                bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                                insertbackground=self.accent, relief="flat",
                                highlightthickness=1, highlightbackground=self.card_border,
                                highlightcolor=self.accent, textvariable=minute_var)
        minute_entry.pack(side="left", padx=(0, 10))

        sound = self._alarm_resolve_sound()
        tk.Label(time_dlg, text=f"Мелодия: {Path(sound).name if sound else 'не выбрана'}",
                 font=("Segoe UI", 9), bg=self.frame_bg, fg=self.label_fg).pack(anchor="w", padx=15)
        btn_frame = tk.Frame(time_dlg, bg=self.frame_bg)
        btn_frame.pack(fill="x", padx=15, pady=14)

        def on_ok():
            try:
                hour, minute = int(hour_var.get()), int(minute_var.get())
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Ошибка", "Введите время от 00:00 до 23:59")
                return
            sound_path = self._alarm_resolve_sound()
            if not sound_path:
                messagebox.showerror("Ошибка", "Сначала выберите мелодию в настройках")
                return
            try:
                added = self.alarm.set_monthly_alarm(date_obj, hour, minute, sound_path)
            except AlarmError as exc:
                messagebox.showerror("Ошибка", str(exc))
                return
            if not added:
                messagebox.showinfo("Будильник уже установлен", "На эту дату уже есть будильник на это время.")
                return
            render_alarms()
            render_callback()
            self._alarm_tick(force=True)
            hour_entry.focus_set()
            hour_entry.selection_range(0, "end")

        def on_close():
            time_dlg.destroy()

        RoundedButton(btn_frame, text="Добавить", command=on_ok,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=6,
                  cursor="hand2", relief="flat", bg=self.accent, fg="white",
                  activebackground=self._lighten(self.accent, 0.2), activeforeground="white",
                  highlightthickness=0).pack(side="left", padx=(0, 9))
        RoundedButton(btn_frame, text="Закрыть", command=on_close,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=6,
                  cursor="hand2", relief="flat", bg=self.neutral_btn, fg=self.input_fg,
                  activebackground=self._darken(self.neutral_btn, 0.2), activeforeground=self.input_fg,
                  highlightthickness=0).pack(side="left")

    def _toggle_monthly_alarm(self, date_obj, date_str, refresh_callback):
        """Открывает список времён выбранного дня и форму добавления."""
        self._show_time_picker(date_obj, date_str, refresh_callback)

    def _alarm_tick(self, force=False):
        """Раз в секунду: статус будильника, кнопка, трей и уведомление о звонке."""
        try:
            st = self.alarm.state()
        except Exception:
            st = {}
        signature = (st.get("status"), st.get("next_time"), st.get("outcome"),
                     st.get("info"), st.get("monthly_count"))
        if force or signature != self._alarm_last_signature:
            self._alarm_last_signature = signature
            status = st.get("status")
            monthly_n = st.get("monthly_count", 0)
            if status == "playing":
                text = f"🔔 Звонит! {st.get('sound_name', '')}".strip()
                color = self.error
                self.btn_alarm.config(text="⏹ Стоп звонка")
                self.button_roles[self.btn_alarm] = "error"
            elif status == "scheduled":
                when = st.get("next_time", "")
                days = st.get("days_of_week", [])
                days_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                if not st.get("repeat"):
                    mode = "один раз"
                elif len(days) >= 7:
                    mode = "ежедневно"
                else:
                    mode = "по дням: " + ", ".join(days_labels[d] for d in days if 0 <= d <= 6)
                text = f"⏰ Будильник на {when} ({mode})"
                if st.get("interval", 5) > 1:
                    text += f" · повтор каждые {st['interval']} мин"
                if monthly_n > 0:
                    text += f" · 📅 {monthly_n}"
                color = self.accent
                self.btn_alarm.config(text="⏹ Отменить")
                self.button_roles[self.btn_alarm] = "error"
            else:
                if monthly_n > 0:
                    text = f"📅 Месячных будильников: {monthly_n}"
                    color = self.accent
                else:
                    text = st.get("info", "") if st.get("outcome") == "error" else "Будильник не установлен"
                    color = self.error if st.get("outcome") == "error" else self.label_fg
                self.btn_alarm.config(text="⏰ Установить")
                self.button_roles[self.btn_alarm] = "accent"
            try:
                self.alarm_status.config(text=text, fg=color)
                self._apply_button_palette()
                self._set_button_state(self.btn_alarm, enabled=True)
            except Exception:
                pass
            # Уведомление из трея, когда звонок начался, а окно спрятано
            if status == "playing" and not self._alarm_notified:
                self._alarm_notified = True
                if self._is_window_hidden() and self.tray_icon is not None:
                    try:
                        self.tray_icon.notify(
                            f"Будильник звонит: {st.get('sound_name', '')}\n"
                            "Откройте приложение, чтобы остановить.",
                            "⏰ Sleep Pause Timer",
                        )
                    except Exception as e:
                        print(f"DEBUG: alarm tray notify error: {e}")
            if status != "playing" and self._alarm_notified:
                self._alarm_notified = False
                self._alarm_clear_tray_notification()
            try:
                self._update_tray()
            except Exception:
                pass
        # Ровно один запланированный тик (update_ui_colors зовёт принудительный)
        if self._alarm_after_id is not None:
            try:
                self.root.after_cancel(self._alarm_after_id)
            except Exception:
                pass
        try:
            self._alarm_after_id = self.root.after(1000, self._alarm_tick)
        except Exception:
            self._alarm_after_id = None

    # ---------- Window activation (improved, supports numbered selection) ----------
    def _resolve_target_title(self, entry_text: str):
        """
        Если entry_text начинается с '#' и далее число — возвращает соответствующий заголовок из detected_windows.
        Иначе возвращает сам entry_text.
        """
        if not entry_text:
            return ""
        entry_text = entry_text.strip()
        if entry_text.startswith("#"):
            try:
                idx = int(entry_text[1:])
                if 1 <= idx <= len(self.detected_windows):
                    return self.detected_windows[idx - 1]
            except Exception:
                return entry_text
        return entry_text

    def _activate_window(self, title_substr):
        """
        Попытка активировать окно по подстроке/точному заголовку.
        Возвращает (True, method_name) если удалось, иначе (False, diagnostics).
        """
        import ctypes
        from ctypes import wintypes

        if not title_substr:
            return False, "empty target"

        # Определяем константы WinAPI
        SW_RESTORE = 9
        SW_SHOW = 5
        SW_MAXIMIZE = 3
        SW_MINIMIZE = 6
        SW_SHOWDEFAULT = 10
        SW_SHOWNOACTIVATE = 4
        WM_ACTIVATE = 0x0006
        WA_ACTIVE = 1
        WA_CLICKACTIVE = 2
        HWND_TOP = 0
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        SWP_NOACTIVATE = 0x0010
        SWP_REFRESH = 0x0201
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        GWL_STYLE = -16
        WS_MINIMIZE = 0x20000000

        # Попробуем использовать ctypes напрямую
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except Exception:
            user32 = None

        try:
            resolved = self._resolve_target_title(title_substr)
            needle = resolved.strip().casefold()

            # Проверяем, это личное окно приложения - не активируем его
            if 'sleep pause' in needle or 'таймер паузы' in needle:
                return False, "skip self window"

            # Специальная обработка для Chromium-браузеров
            is_chromium = any(b in needle for b in ['yandex', 'chrome', 'яндекс', 'msedge'])

            # Функция для поиска окон
            def find_windows(search_needle):
                found_hwnds = []
                if gw is not None:
                    try:
                        # Точное совпадение
                        wins = gw.getWindowsWithTitle(resolved)
                        for w in wins:
                            found_hwnds.append((w._hwnd, 'pygetwindow_exact'))
                    except Exception:
                        pass
                    try:
                        # Частичное совпадение
                        all_w = gw.getAllWindows()
                        for w in all_w:
                            try:
                                if search_needle in (w.title or "").casefold():
                                    found_hwnds.append((w._hwnd, 'pygetwindow_partial'))
                            except Exception:
                                continue
                    except Exception:
                        pass
                # Также ищем через win32gui
                if win32gui is not None:
                    try:
                        def enum_cb(hwnd, _):
                            try:
                                title = win32gui.GetWindowText(hwnd) or ""
                                if search_needle in title.casefold():
                                    if hwnd not in [h for h, _ in found_hwnds]:
                                        found_hwnds.append((hwnd, 'win32gui'))
                            except Exception:
                                pass
                        win32gui.EnumWindows(enum_cb, None)
                    except Exception:
                        pass
                return found_hwnds

            # Ищем окна
            hwnds = find_windows(needle)
            if not hwnds:
                # Если не нашли по частичному совпадению, пробуем точное
                hwnds = find_windows(resolved.casefold())

            if not hwnds:
                # diagnostics
                try:
                    if gw is not None:
                        titles = [(w.title or "") for w in gw.getAllWindows() if w.title]
                        diag = "not found; sample titles:\n" + "\n".join(titles[:30])
                    else:
                        diag = "not found; no windows available"
                except Exception as e:
                    diag = f"not found: {e}"
                return False, diag

            # Пробуем активировать каждое найденное окно
            for hwnd, source in hwnds:
                try:
                    # === Методы как при демонстрации экрана ===

                    # Метод A: SetWindowPos с HWND_TOPMOST + флагами как при демонстрации
                    if user32 is not None:
                        try:
                            # Сначала восстанавливаем если минимизировано
                            if user32.IsIconic(hwnd):
                                user32.ShowWindow(hwnd, SW_RESTORE)
                                time.sleep(0.05)

                            # Используем SetWindowPos как в демонстрации экрана:
                            # 1. Сначала делаем окно topmost
                            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                            time.sleep(0.05)

                            # 2. Показываем и активируем
                            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                            time.sleep(0.05)

                            # 3. Возвращаем в нормальный стек (не topmost) но оставляем активным
                            user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                            time.sleep(0.05)

                            # 4. Финальная попытка активации
                            user32.SetForegroundWindow(hwnd)
                            time.sleep(0.1)

                            # Проверяем результат
                            foreground = user32.GetForegroundWindow()
                            if foreground == hwnd:
                                return True, "SetWindowPos demo-style"

                            # Если не получилось, пробуем ещё раз с другими флагами
                            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE)
                            time.sleep(0.1)
                            user32.SetForegroundWindow(hwnd)
                            time.sleep(0.1)

                            if user32.GetForegroundWindow() == hwnd:
                                return True, "SetWindowPos TOPMOST+foreground"
                        except Exception as e:
                            pass

                    # Метод B: Классический подход
                    if win32gui is not None:
                        try:
                            win32gui.ShowWindow(hwnd, SW_RESTORE)
                            win32gui.ShowWindow(hwnd, SW_SHOW)
                            win32gui.SetForegroundWindow(hwnd)
                            time.sleep(0.1)
                            if user32 and user32.GetForegroundWindow() == hwnd:
                                return True, "win32gui SW_RESTORE+SetForegroundWindow"
                        except Exception:
                            pass

                    # Метод C: ctypes базовый
                    if user32 is not None:
                        try:
                            user32.ShowWindow(hwnd, SW_RESTORE)
                            user32.ShowWindow(hwnd, SW_SHOWDEFAULT)
                            user32.SetForegroundWindow(hwnd)
                            time.sleep(0.1)
                            if user32.GetForegroundWindow() == hwnd:
                                return True, "ctypes ShowWindow+SetForegroundWindow"
                        except Exception:
                            pass

                    # Метод D: SendMessage для Chromium
                    if is_chromium:
                        try:
                            user32.SendMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
                            time.sleep(0.05)
                            user32.SetForegroundWindow(hwnd)
                            time.sleep(0.1)
                            if user32.GetForegroundWindow() == hwnd:
                                return True, "ctypes SendMessage WM_ACTIVATE"
                        except Exception:
                            pass

                        # Метод E: Дочерние окна Chromium
                        if win32gui is not None:
                            try:
                                def enum_child(hwnd_child, _):
                                    try:
                                        classname = win32gui.GetClassName(hwnd_child)
                                        if 'Chrome' in classname or 'Widget' in classname:
                                            try:
                                                win32gui.ShowWindow(hwnd_child, SW_RESTORE)
                                                win32gui.BringWindowToTop(hwnd_child)
                                                time.sleep(0.05)
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                    return True
                                win32gui.EnumChildWindows(hwnd, enum_child, None)
                                user32.SetForegroundWindow(hwnd)
                                time.sleep(0.1)
                                if user32.GetForegroundWindow() == hwnd:
                                    return True, "chromium child windows"
                            except Exception:
                                pass

                    # Метод F: Альтернативный - через PowerShell (как при демонстрации)
                    try:
                        import subprocess
                        # Получаем заголовок окна для PowerShell
                        if win32gui is not None:
                            win_title = win32gui.GetWindowText(hwnd)
                            if win_title:
                                # Используем [Microsoft.VisualBasic.Interaction]::AppActivate
                                ps_script = f'''
                                Add-Type -AssemblyName Microsoft.VisualBasic
                                [Microsoft.VisualBasic.Interaction]::AppActivate("{win_title.replace('"', '`"')}")
                                Start-Sleep -Milliseconds 200
                                '''
                                result = subprocess.run(
                                    ['powershell', '-Command', ps_script],
                                    capture_output=True, timeout=3
                                )
                                time.sleep(0.2)
                                if user32 and user32.GetForegroundWindow() == hwnd:
                                    return True, "PowerShell AppActivate"
                    except Exception:
                        pass

                    # Метод G: Финальная попытка - многоразовый вызов
                    if user32 is not None:
                        for _ in range(3):
                            try:
                                user32.ShowWindow(hwnd, SW_RESTORE)
                                time.sleep(0.05)
                                user32.BringWindowToTop(hwnd)
                                time.sleep(0.05)
                                user32.SetForegroundWindow(hwnd)
                                time.sleep(0.1)
                                if user32.GetForegroundWindow() == hwnd:
                                    return True, "multiple attempts"
                            except Exception:
                                pass

                except Exception as e:
                    continue

            return False, f"all methods failed for {len(hwnds)} windows"

        except Exception as e:
            return False, f"exception: {e}"

    # ---------- Detect windows (populates numbered list) ----------
    def detect_windows(self):
        if gw is None:
            messagebox.showinfo("Detect windows", "pygetwindow не установлен или недоступен.")
            return
        try:
            wins = gw.getAllWindows()
            titles = [w.title for w in wins if w.title]
            if not titles:
                messagebox.showinfo("Detect windows", "Окна не найдены или у них пустые заголовки.")
                return
            # Сохраняем список (индексация с 1)
            self.detected_windows = titles.copy()
            # Покажем диалог с нумерованным списком и возможностью выбрать номер
            self._show_detect_dialog(titles)
        except Exception as e:
            messagebox.showerror("Detect windows", f"Ошибка при получении окон: {e}")

    def _show_detect_dialog(self, titles):
        dlg = tk.Toplevel(self.root)
        dlg.title("Найденные окна")
        dlg.configure(bg=self.frame_bg)
        dlg.geometry("700x400")
        dlg.transient(self.root)
        dlg.grab_set()

        lbl = ttk.Label(dlg, text="Найдены окна. Выберите номер и нажмите Использовать.")
        lbl.pack(anchor="w", padx=8, pady=(8,4))

        frame = ttk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        scrollbar = ttk.Scrollbar(frame, orient="vertical")
        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set, selectmode="browse", font=("Segoe UI", 9),
                            bg=self.entry_bg, fg=self.input_fg, bd=0, highlightthickness=0,
                            selectbackground=self.accent, selectforeground="#101722")
        scrollbar.config(command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        # Заполним нумерованными строками: "#1 — <title>"
        for i, t in enumerate(titles, start=1):
            display = f"#{i} — {t}"
            listbox.insert("end", display)

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", pady=(6,8), padx=8)
        def use_selected():
            sel = listbox.curselection()
            if not sel:
                messagebox.showinfo("Use window", "Выберите строку в списке.")
                return
            idx = sel[0]  # 0-based
            # Присваиваем в поле ввода короткий идентификатор "#N"
            self.win_title_entry.delete(0, tk.END)
            self.win_title_entry.insert(0, f"#{idx+1}")
            dlg.destroy()
        def copy_selected():
            sel = listbox.curselection()
            if not sel:
                messagebox.showinfo("Copy", "Выберите строку в списке.")
                return
            idx = sel[0]
            title = titles[idx]
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(title)
                messagebox.showinfo("Copy", "Заголовок скопирован в буфер обмена.")
            except Exception:
                messagebox.showinfo("Copy", "Не удалось скопировать в буфер.")
        def close_dlg():
            dlg.destroy()

        btn_use = RoundedButton(btn_frame, text="Использовать", command=use_selected)
        btn_use.pack(side="left", padx=(0,6))
        btn_copy = RoundedButton(btn_frame, text="Копировать заголовок", command=copy_selected)
        btn_copy.pack(side="left", padx=(0,6))
        btn_close = RoundedButton(btn_frame, text="Закрыть", command=close_dlg)
        btn_close.pack(side="right")

    # ---------- Activation check (diagnostics) ----------
    def check_activation(self):
        """Проверяет активацию для значения в поле win_title_entry и показывает результат."""
        target = self.win_title_entry.get().strip()
        if not target:
            messagebox.showinfo("Проверить активацию", "Введите номер (#N) или подстроку заголовка окна.")
            return
        ok, info = self._activate_window(target)
        if ok:
            messagebox.showinfo("Проверить активацию", f"Активация успешна ({info}).")
        else:
            # покажем диагностическую информацию (усечённо)
            short = info if len(info) < 2000 else info[:2000] + "\n... (truncated)"
            messagebox.showwarning("Проверить активацию", f"Активация не удалась.\n{short}")
            print("DEBUG: activation diagnostics:\n", info)

    # ---------- Tray helpers (с цветовым индикатором) ----------
    def _is_tracking_active(self):
        """True, если работает основной таймер или отслеживание бездействия."""
        try:
            if self.worker is not None and self.worker.is_alive():
                return True
        except Exception:
            pass
        try:
            if self.idle_counter_thread is not None and self.idle_counter_thread.is_alive():
                return True
        except Exception:
            pass
        # Будильник: запланирован или звонит — приложение должно остаться жить в трее
        try:
            alarm_st = self.alarm.state()
            if alarm_st.get("scheduled") or alarm_st.get("playing") or alarm_st.get("monthly_count"):
                return True
        except Exception:
            pass
        return False

    def _is_window_hidden(self):
        """True, если главное окно спрятано (свернуто в трей)."""
        try:
            return self.root.state() == "withdrawn"
        except Exception:
            return False

    def _on_root_unmap(self, event):
        """Сворачивание окна (кнопка «–») при активном таймере/отслеживании — в трей."""
        if event.widget is not self.root:
            return
        # state() обновляется не мгновенно — проверяем отложенно
        try:
            self.root.after(200, self._minimize_to_tray_if_iconic)
        except Exception:
            pass

    def _minimize_to_tray_if_iconic(self):
        try:
            if self.root.state() == "iconic" and self._is_tracking_active():
                self.minimize_to_tray()
        except Exception:
            pass

    def _make_image(self, text):
        if Image is None or ImageDraw is None:
            return None
        size = (64, 64)
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except Exception:
            font = ImageFont.load_default()
        draw.rectangle((0, 0, size[0], size[1]), fill=(40, 40, 40, 255))
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        except Exception:
            w, h = (28, 28)
        draw.text(((size[0]-w)/2, (size[1]-h)/2), text, font=font, fill="white")
        return img

    def _compose_tray_icon_with_indicator(self, base_img, indicator_color=None):
        if Image is None:
            return None
        try:
            img = base_img.copy() if base_img is not None else Image.new("RGBA", (64,64), (40,40,40,255))
            img = ImageOps.contain(img, (64,64))
        except Exception:
            img = Image.new("RGBA", (64,64), (40,40,40,255))
        if indicator_color:
            draw = ImageDraw.Draw(img)
            r = 12
            padding = 6
            x0 = img.width - r - padding
            y0 = img.height - r - padding
            x1 = img.width - padding
            y1 = img.height - padding
            draw.ellipse((x0, y0, x1, y1), fill=indicator_color + (255,))
        return img

    def _update_tray(self):
        if self.tray_icon is None:
            return
        idle_alive = (self.idle_counter_thread is not None
                      and self.idle_counter_thread.is_alive())
        try:
            alarm_st = self.alarm.state()
        except Exception:
            alarm_st = {}
        alarm_active = bool(alarm_st.get("scheduled") or alarm_st.get("playing")
                            or alarm_st.get("monthly_count"))
        if self.current_remaining > 0:
            hours = self.current_remaining // 3600
            mins = (self.current_remaining % 3600) // 60
            secs = self.current_remaining % 60
            tooltip = f"Осталось {hours:02d}:{mins:02d}:{secs:02d}"
        elif idle_alive:
            tooltip = "⏳ Отслеживание бездействия..."
        elif alarm_st.get("playing"):
            tooltip = "⏰ Будильник звонит!"
        else:
            tooltip = "Sleep Pause Timer"
        # Будильник добавляется к тултипу
        if alarm_active and not alarm_st.get("playing"):
            when = alarm_st.get("next_time", "")
            if when:
                tooltip = f"{tooltip} · ⏰ {when}"
        try:
            self.tray_icon.title = tooltip
            running = (idle_alive
                       or alarm_active
                       or (self.worker is not None and self.worker.is_alive() and self.current_remaining > 0))
            indicator = None
            if not running:
                indicator = (200, 0, 0)  # красный — таймер не активен
            elif self.paused and self.current_remaining > 0:
                indicator = (0, 180, 0)  # зелёный — пауза
            if self.app_icon_img is not None and Image is not None:
                base = self.app_icon_img.copy()
            else:
                mins = self.current_remaining // 60
                secs = self.current_remaining % 60
                base = self._make_image(f"{mins}" if self.current_remaining >= 60 else f"{secs}")
            img = self._compose_tray_icon_with_indicator(base, indicator_color=indicator)
            if img is not None:
                self._icon_image = img
                self.tray_icon.icon = self._icon_image
        except Exception:
            pass

    def minimize_to_tray(self):
        if pystray is None or Image is None:
            messagebox.showerror("Ошибка", "Для работы трея требуются пакеты pystray и pillow. Установите: pip install pystray pillow")
            return
        try:
            self.root.withdraw()
        except Exception:
            pass
        if self.tray_icon is None:
            try:
                self.tray_icon = pystray.Icon("sleep_pause")
                self.tray_icon.menu = pystray.Menu(
                    pystray.MenuItem("Restore", lambda _: self._on_restore(), default=True),
                    pystray.MenuItem("Exit", lambda _: self._on_exit())
                )
                try:
                    if self.app_icon_img is not None:
                        default_img = ImageOps.contain(self.app_icon_img.copy(), (64, 64))
                    else:
                        default_img = self._make_image("0")
                    running = self._is_tracking_active()
                    indicator = None if running else (200,0,0)
                    composed = self._compose_tray_icon_with_indicator(default_img, indicator_color=indicator)
                    if composed is not None:
                        self._icon_image = composed
                        self.tray_icon.icon = self._icon_image
                except Exception as e:
                    print("DEBUG: failed to create default tray image:", e)
                try:
                    self._update_tray()
                except Exception:
                    pass
                try:
                    self.tray_icon.run_detached()
                    print("DEBUG: tray run_detached started")
                except Exception as e:
                    print("DEBUG: tray run_detached exception:", e)
            except Exception as e:
                print("DEBUG: failed to create tray icon:", e)
        else:
            self._update_tray()

    def _on_restore(self):
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        self.tray_icon = None
        try:
            self.root.after(0, self._show_window)
        except Exception:
            pass

    def _show_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

    def _on_exit(self):
        self.stop_event.set()
        self.idle_stop_event.set()
        # Будильник: глушим звонок и поток (расписание остаётся в alarm.json
        # и восстановится при следующем запуске)
        try:
            self.alarm.stop_thread()
        except Exception:
            pass
        self._alarm_clear_tray_notification()
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.after(0, self.root.quit)
        except Exception:
            pass

    # ---------- Close behavior ----------
    def on_close(self):
        # В трей уходим, если работает основной таймер ИЛИ отслеживание бездействия
        if self._is_tracking_active():
            self.minimize_to_tray()
        else:
            try:
                self.root.destroy()
            except Exception:
                self.root.quit()

    # ---------- Timer thread ----------
    def _timer_thread(self, seconds, key):
        remaining = seconds
        self.current_remaining = remaining
        while remaining > 0 and not self.stop_event.is_set():
            if self.paused:
                time.sleep(0.2)
                self.current_remaining = remaining
                try:
                    self._update_tray()
                except Exception:
                    pass
                continue
            hours, remainder = divmod(remaining, 3600)
            mins, secs = divmod(remainder, 60)
            try:
                self.root.after(0, lambda h=hours, m=mins, s=secs: self._set_status(f"Осталось {h:02d}:{m:02d}:{s:02d}", self.accent, pulse=True))
                self.root.after(0, lambda v=seconds-remaining: self._animate_progress_to(v, duration_ms=320))
            except Exception:
                pass
            self.current_remaining = remaining
            try:
                self._update_tray()
            except Exception:
                pass
            time.sleep(1)
            remaining -= 1

        if self.stop_event.is_set():
            try:
                self.root.after(0, lambda: self._set_status("⏹ Отменено", self.error, pulse=False))
                self.root.after(0, lambda: self._animate_progress_to(0, duration_ms=220))
                self.root.after(0, lambda: self._set_button_state(self.btn_start, enabled=True))
                self.root.after(0, lambda: self._set_button_state(self.btn_pause, enabled=False))
                self.root.after(0, lambda: self._set_button_state(self.btn_cancel, enabled=False))
            except Exception:
                pass
            self.current_remaining = 0
            try:
                self._update_tray()
            except Exception:
                pass
            return

        # Таймер завершён — обрабатываем действие
        title = self.win_title_entry.get().strip()
        
        # Обработка команд выключения и спящего режима
        if key == "shutdown":
            try:
                self.root.after(0, lambda: self._set_status("🔴 Выключение...", self.warning, pulse=False))
                self.root.after(0, lambda: self._animate_progress_to(self.progress["maximum"], duration_ms=180))
            except Exception:
                pass
            self.current_remaining = 0
            try:
                self._update_tray()
            except Exception:
                pass
            time.sleep(1)
            # Выключаем ПК: ExitWindowsEx живёт в user32, а не в kernel32
            import ctypes
            ctypes.windll.user32.ExitWindowsEx(0x00000001 | 0x00000004, 0xFFFFFFFF)
            return

        elif key == "sleep":
            try:
                self.root.after(0, lambda: self._set_status("💤 Спящий режим...", self.warning, pulse=False))
                self.root.after(0, lambda: self._animate_progress_to(self.progress["maximum"], duration_ms=180))
            except Exception:
                pass
            self.current_remaining = 0
            try:
                self._update_tray()
            except Exception:
                pass
            time.sleep(1)
            # Переходим в спящий режим: SetSuspendState живёт в PowrProf, а не в kernel32
            import ctypes
            # SetSuspendState(0, 1, 0) - гибернация, SetSuspendState(0, 0, 0) - спящий
            ctypes.windll.PowrProf.SetSuspendState(0, 0, 0)
            return
        
        # Стандартная отправка клавиши
        if title:
            self._activate_window(title)

        if pyautogui is None:
            try:
                self.root.after(0, lambda: messagebox.showerror("Ошибка", "Требуется пакет pyautogui"))
            except Exception:
                pass
            return

        try:
            pyautogui.press(key)
        except Exception as e:
            try:
                self.root.after(0, lambda err=e: messagebox.showerror("Ошибка", f"Не удалось отправить клавишу '{key}': {err}"))
            except Exception:
                pass
            try:
                self.root.after(0, lambda: self._set_button_state(self.btn_start, enabled=True))
                self.root.after(0, lambda: self._set_button_state(self.btn_pause, enabled=False))
                self.root.after(0, lambda: self._set_button_state(self.btn_cancel, enabled=False))
                self.root.after(0, lambda: self._set_status("Ошибка", self.error, pulse=False))
            except Exception:
                pass
            self.current_remaining = 0
            try:
                self._update_tray()
            except Exception:
                pass
            return

        try:
            self.root.after(0, lambda: self._set_status("✓ Клавиша отправлена!", self.success, pulse=False))
            self.root.after(0, lambda: self._animate_progress_to(self.progress["maximum"], duration_ms=180))
        except Exception:
            pass
        try:
            self.root.after(350, lambda: self._set_button_state(self.btn_start, enabled=True))
            self.root.after(350, lambda: self._set_button_state(self.btn_pause, enabled=False))
            self.root.after(350, lambda: self._set_button_state(self.btn_cancel, enabled=False))
            self.root.after(350, lambda: self._set_status("✓ Готово", self.success, pulse=False))
            self.root.after(350, lambda: self._animate_progress_to(0, duration_ms=260))
        except Exception:
            pass
        self.current_remaining = 0
        try:
            self._update_tray()
        except Exception:
            pass

# ----------------- Run -----------------
if __name__ == "__main__":
    root = tk.Tk()
    app = SleepPauseApp(root)
    root.mainloop()

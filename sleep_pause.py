#!/usr/bin/env python3
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

# ----------------- Theme helpers -----------------
def apply_dark_theme(style: ttk.Style):
    bg = "#0b1220"
    fg = "#e2e8f0"
    accent = "#7c83ff"
    btn_bg = "#1e293b"
    btn_hover = "#273449"
    entry_bg = "#111827"
    success = "#22c55e"
    warning = "#f59e0b"
    error = "#ef4444"
    
    try:
        style.theme_use("clam")
    except Exception:
        pass
    
    style.configure(".", background=bg, foreground=fg, font=("Segoe UI", 10))
    style.configure("TLabel", background=bg, foreground=fg, font=("Segoe UI", 10))
    style.configure("TFrame", background=bg)
    style.configure("TEntry", fieldbackground=entry_bg, foreground=fg, borderwidth=1)
    style.configure("TButton", background=btn_bg, foreground=fg, borderwidth=0, font=("Segoe UI", 10, "bold"))
    style.map("TButton",
              background=[("active", btn_hover), ("pressed", accent)],
              foreground=[("disabled", "#64748b")])
    style.configure("TProgressbar", troughcolor="#1f2937", background=accent, borderwidth=0, thickness=10)
    style.configure("TCombobox", 
                    fieldbackground=entry_bg, 
                    background=btn_bg, 
                    foreground=fg,
                    selectbackground=entry_bg,
                    selectforeground=fg)
    style.map("TCombobox", 
              fieldbackground=[("readonly", entry_bg)],
              background=[("readonly", btn_bg)],
              foreground=[("readonly", fg)])

    style._bg = bg
    style._fg = fg
    style._accent = accent
    style._btn_bg = btn_bg
    style._entry_bg = entry_bg
    style._success = success

def apply_light_theme(style: ttk.Style):
    bg = "#f3f6fb"
    fg = "#0f172a"
    accent = "#5b6cff"
    btn_bg = "#ffffff"
    btn_hover = "#eef2ff"
    entry_bg = "#ffffff"
    success = "#22c55e"
    warning = "#f59e0b"
    error = "#ef4444"
    
    try:
        style.theme_use("clam")
    except Exception:
        pass
    
    style.configure(".", background=bg, foreground=fg, font=("Segoe UI", 10))
    style.configure("TLabel", background=bg, foreground=fg, font=("Segoe UI", 10))
    style.configure("TFrame", background=bg)
    style.configure("TEntry", fieldbackground=entry_bg, foreground=fg, borderwidth=1)
    style.configure("TButton", background=btn_bg, foreground=fg, borderwidth=1, font=("Segoe UI", 10, "bold"))
    style.map("TButton",
              background=[("active", btn_hover), ("pressed", accent)],
              foreground=[("disabled", "#94a3b8")])
    style.configure("TProgressbar", troughcolor="#dbe4f3", background=accent, borderwidth=0, thickness=10)
    style.configure("TCombobox", 
                    fieldbackground=entry_bg, 
                    background=btn_bg, 
                    foreground=fg,
                    selectbackground=entry_bg,
                    selectforeground=fg)
    style.map("TCombobox", 
              fieldbackground=[("readonly", entry_bg)],
              background=[("readonly", btn_bg)],
              foreground=[("readonly", fg)])

    style._bg = bg
    style._fg = fg
    style._accent = accent
    style._btn_bg = btn_bg
    style._entry_bg = entry_bg
    style._success = success

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
        self._monthly_alarms = {}  # date_str (YYYY-MM-DD) -> {"hour": h, "minute": m}
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
        if isinstance(monthly, dict):
            restored_monthly = {}
            for date_str, alarm in monthly.items():
                if not isinstance(alarm, dict):
                    continue
                try:
                    hour = int(alarm["hour"])
                    minute = int(alarm["minute"])
                    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                        continue
                    datetime.strptime(str(date_str), "%Y-%m-%d")
                    restored_monthly[str(date_str)] = {"hour": hour, "minute": minute}
                except (KeyError, TypeError, ValueError):
                    continue
            self._monthly_alarms = restored_monthly
        else:
            self._monthly_alarms = {}

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

    def _next_monthly_trigger_locked(self, now):
        trigger = None
        trigger_date = None
        expired_dates = []
        for date_str, alarm in list(self._monthly_alarms.items()):
            try:
                date_value = datetime.strptime(str(date_str), "%Y-%m-%d")
                hour = int(alarm["hour"])
                minute = int(alarm["minute"])
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise ValueError
                candidate = datetime(date_value.year, date_value.month, date_value.day, hour, minute)
            except (KeyError, TypeError, ValueError):
                expired_dates.append(date_str)
                continue
            if date_value.date() < now.date():
                expired_dates.append(date_str)
                continue
            if trigger is None or candidate < trigger:
                trigger = candidate
                trigger_date = date_str
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
            # Если candidate не в списке дней недели, ищем следующий подходящий
            while candidate.weekday() not in self._days_of_week:
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
        for d in days_of_week:
            if not 0 <= d <= 6:
                raise AlarmError("Дни недели должны быть в диапазоне 0-6 (0=Пн, 6=Вс)")
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
                    monthly_alarm = self._monthly_alarms.pop(trigger_date, None)
                    if monthly_alarm is None:
                        self._playing = False
                        self._refresh_next_trigger_locked(self._now())
                        continue
                    hour = monthly_alarm["hour"]
                    minute = monthly_alarm["minute"]
                    repeat = False
                    interval = 5
                    sound_path = self._sound_path
                    self._save_locked()

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
                    len(self._monthly_alarms),
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
            "monthly_count": len(self._monthly_alarms),
        }

    def state(self):
        with self._condition:
            return self._state_locked()

    def get_monthly_alarms(self):
        """Возвращает копию словаря месячных будильников."""
        with self._condition:
            return dict(self._monthly_alarms)

    def set_monthly_alarm(self, date_obj, hour, minute, sound_path=None):
        """Устанавливает или удаляет будильник на конкретную дату.

        Если hour/minute = None, удаляет будильник на эту дату.
        """
        normalized_path = normalize_sound_path(sound_path) if sound_path else self._sound_path
        date_str = date_obj.isoformat()
        with self._condition:
            if hour is None or minute is None:
                # Удаляем будильник
                existed = date_str in self._monthly_alarms
                if existed:
                    del self._monthly_alarms[date_str]
                    self._save_locked()
                # Будим поток, чтобы он пересчитал ближайший триггер
                self._condition.notify_all()
                return existed
            else:
                # Устанавливаем будильник
                hour = int(hour)
                minute = int(minute)
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise AlarmError("Время будильника должно быть в диапазоне 00:00–23:59")
                self._monthly_alarms[date_str] = {"hour": hour, "minute": minute}
                self._save_locked()
                # Поток мог «уснуть» на старом триггере или уже завершиться —
                # перезапускаем гарантированно (generation-гейт убьёт старый),
                # как это делает set_alarm.
                self._start_thread_locked()
                self._condition.notify_all()
                return True

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
        apply_dark_theme(self.style)
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

        # UI (modern card style)
        self.current_theme = "dark"
        self._pulse_after_id = None
        self._status_pulse_phase = 0.0
        self._button_anim_after_ids = {}
        self.button_roles = {}

        self._apply_theme_palette()
        root.configure(bg=self.main_bg)
        
        # Главный контейнер
        frm = tk.Frame(root, bg=self.main_bg, padx=20, pady=20)
        frm.pack(fill="both", expand=True)
        self.main_container = frm
        
        # Заголовок
        self.title_label = tk.Label(
            frm,
            text="⏰ Sleep Pause Timer",
            font=("Segoe UI", 17, "bold"),
            bg=self.main_bg,
            fg=self.accent,
        )
        self.title_label.pack(pady=(0, 14))

        # Карточка времени
        self.time_card = tk.Frame(frm, bg=self.frame_bg, padx=16, pady=14, highlightthickness=1)
        self.time_card.configure(highlightbackground=self.card_border, highlightcolor=self.card_border)
        self.time_card.pack(fill="x", pady=(0, 8))

        self.time_shadow = tk.Frame(frm, bg=self.divider_color, height=1)
        self.time_shadow.pack(fill="x", padx=6, pady=(0, 12))

        time_labels_frame = tk.Frame(self.time_card, bg=self.frame_bg)
        time_labels_frame.pack(fill="x")
        time_labels_frame.columnconfigure(0, weight=1)
        time_labels_frame.columnconfigure(1, weight=1)
        time_labels_frame.columnconfigure(2, weight=1)
        
        tk.Label(time_labels_frame, text="Часы", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9, "bold")).grid(row=0, column=0, padx=10)
        tk.Label(time_labels_frame, text="Минуты", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9, "bold")).grid(row=0, column=1, padx=10)
        tk.Label(time_labels_frame, text="Секунды", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9, "bold")).grid(row=0, column=2, padx=10)
        
        entry_frame = tk.Frame(self.time_card, bg=self.frame_bg)
        entry_frame.pack(fill="x", pady=(6, 0))
        entry_frame.columnconfigure(0, weight=1)
        entry_frame.columnconfigure(1, weight=1)
        entry_frame.columnconfigure(2, weight=1)
        
        self.entry_hour = tk.Entry(entry_frame, width=5, font=("Segoe UI", 14, "bold"),
                                   bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                                   insertbackground=self.accent, relief="flat")
        self.entry_hour.insert(0, DEFAULT_HOUR)
        self.entry_hour.grid(row=0, column=0, padx=10, sticky="ew", ipady=6)

        self.entry_min = tk.Entry(entry_frame, width=5, font=("Segoe UI", 14, "bold"),
                                  bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                                  insertbackground=self.accent, relief="flat")
        self.entry_min.insert(0, DEFAULT_MIN)
        self.entry_min.grid(row=0, column=1, padx=10, sticky="ew", ipady=6)

        self.entry_sec = tk.Entry(entry_frame, width=5, font=("Segoe UI", 14, "bold"),
                                  bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                                  insertbackground=self.accent, relief="flat")
        self.entry_sec.insert(0, DEFAULT_SEC)
        self.entry_sec.grid(row=0, column=2, padx=10, sticky="ew", ipady=6)

        # Карточка настроек
        self.settings_card = tk.Frame(frm, bg=self.frame_bg, padx=16, pady=14, highlightthickness=1)
        self.settings_card.configure(highlightbackground=self.card_border_accent, highlightcolor=self.card_border_accent)
        self.settings_card.pack(fill="x", pady=(0, 8))

        self.settings_shadow = tk.Frame(frm, bg=self.divider_color, height=1)
        self.settings_shadow.pack(fill="x", padx=6, pady=(0, 12))

        tk.Label(self.settings_card, text="⌨️ Клавиша для отправки", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9)).pack(anchor="w")
        
        self.key_var = tk.StringVar(value=KEY_OPTIONS[0])
        self.combo = ttk.Combobox(self.settings_card, values=KEY_OPTIONS, textvariable=self.key_var,
                                  state="readonly", width=25, font=("Segoe UI", 10, "bold"),
                                  takefocus=0)
        self.combo.pack(fill="x", pady=(5, 10))

        # Отменяем выделение текста
        self.combo.bind("<FocusIn>", lambda e: self.combo.after(1, lambda: self.combo.selection_clear()))

        tk.Label(self.settings_card, text="🪟 Активировать окно", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9)).pack(anchor="w")
        
        self.win_title_entry = tk.Entry(self.settings_card, font=("Segoe UI", 10),
                                         bg=self.entry_bg, fg=self.input_fg, bd=0, relief="flat")
        self.win_title_entry.pack(fill="x", pady=(5, 0), ipady=5)

        # --- Бездействие ---
        tk.Label(self.settings_card, text="⏳ Бездействие (минуты, 0 = выкл)", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 0))
        
        idle_frame = tk.Frame(self.settings_card, bg=self.frame_bg)
        idle_frame.pack(fill="x", pady=(5, 0))
        
        self.idle_spin = tk.Spinbox(idle_frame, from_=1, to=120, width=5,
                                     font=("Segoe UI", 10, "bold"), justify="center",
                                     bg=self.entry_bg, fg=self.input_fg, bd=0,
                                     relief="flat", takefocus=0)
        self.idle_spin.insert(0, "0")
        self.idle_spin.pack(side="left")
        
        self.idle_label = tk.Label(idle_frame, text="мин", bg=self.frame_bg,
                                    fg=self.label_fg, font=("Segoe UI", 9))
        self.idle_label.pack(side="left", padx=(4, 0))

        # --- Будильник ---
        tk.Label(self.settings_card, text="⏰ Будильник", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 0))

        alarm_time_frame = tk.Frame(self.settings_card, bg=self.frame_bg)
        alarm_time_frame.pack(fill="x", pady=(5, 0))

        self.alarm_hour_entry = tk.Entry(alarm_time_frame, width=3, font=("Segoe UI", 11, "bold"),
                                          bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                                          insertbackground=self.accent, relief="flat")
        self.alarm_hour_entry.insert(0, "07")
        self.alarm_hour_entry.pack(side="left")

        tk.Label(alarm_time_frame, text=":", bg=self.frame_bg, fg=self.label_fg,
                 font=("Segoe UI", 11, "bold")).pack(side="left")

        self.alarm_minute_entry = tk.Entry(alarm_time_frame, width=3, font=("Segoe UI", 11, "bold"),
                                            bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                                            insertbackground=self.accent, relief="flat")
        self.alarm_minute_entry.insert(0, "30")
        self.alarm_minute_entry.pack(side="left", padx=(0, 10))

        self.alarm_repeat_var = tk.BooleanVar(value=True)
        self.alarm_repeat_check = tk.Checkbutton(
            alarm_time_frame, text="Ежедневно", variable=self.alarm_repeat_var,
            bg=self.frame_bg, fg=self.label_fg, font=("Segoe UI", 9),
            activebackground=self.frame_bg, activeforeground=self.label_fg,
            selectcolor=self.entry_bg, bd=0, takefocus=0, relief="flat",
            highlightthickness=0, highlightbackground=self.frame_bg, highlightcolor=self.frame_bg)
        self.alarm_repeat_check.pack(side="left", padx=(0, 10))

        tk.Label(alarm_time_frame, text="Интервал", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9)).pack(side="left")
        self.alarm_interval_spin = tk.Spinbox(alarm_time_frame, from_=1, to=60, width=3,
                                               font=("Segoe UI", 10, "bold"), justify="center",
                                               bg=self.entry_bg, fg=self.input_fg, bd=0,
                                               relief="flat", takefocus=0)
        self.alarm_interval_spin.delete(0, "end")
        self.alarm_interval_spin.insert(0, "5")
        self.alarm_interval_spin.pack(side="left", padx=(4, 0))
        tk.Label(alarm_time_frame, text="мин", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9)).pack(side="left", padx=(4, 0))

        # Дни недели
        alarm_days_frame = tk.Frame(self.settings_card, bg=self.frame_bg)
        alarm_days_frame.pack(fill="x", pady=(5, 0))

        self.alarm_days_vars = []
        days_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        tk.Label(alarm_days_frame, text="Дни:", bg=self.frame_bg,
                 fg=self.label_fg, font=("Segoe UI", 9)).pack(side="left", padx=(0, 5))
        for i, label in enumerate(days_labels):
            var = tk.BooleanVar(value=True)
            self.alarm_days_vars.append(var)
            cb = tk.Checkbutton(
                alarm_days_frame, text=label, variable=var,
                bg=self.frame_bg, fg=self.label_fg, font=("Segoe UI", 8),
                activebackground=self.frame_bg, activeforeground=self.label_fg,
                selectcolor=self.entry_bg, bd=0, takefocus=0, relief="flat",
                highlightthickness=0, highlightbackground=self.frame_bg, highlightcolor=self.frame_bg)
            cb.pack(side="left", padx=2)

        # Кнопка просмотра расписания на месяц
        alarm_month_frame = tk.Frame(self.settings_card, bg=self.frame_bg)
        alarm_month_frame.pack(fill="x", pady=(5, 0))
        self.btn_alarm_month = tk.Button(alarm_month_frame, text="📅 Расписание на месяц", command=self._show_alarm_month_schedule,
                                          font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=4,
                                          cursor="hand2", relief="flat", activeforeground="white", takefocus=0,
                                          highlightthickness=0, highlightbackground=self.frame_bg, highlightcolor=self.frame_bg)
        self.btn_alarm_month.pack(side="left")

        # Мелодия будильника
        alarm_sound_frame = tk.Frame(self.settings_card, bg=self.frame_bg)
        alarm_sound_frame.pack(fill="x", pady=(5, 0))

        self.alarm_sound_entry = tk.Entry(alarm_sound_frame, font=("Segoe UI", 9),
                                           bg=self.entry_bg, fg=self.input_fg, bd=0, relief="flat")
        self.alarm_sound_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))

        self.btn_alarm_sound = tk.Button(alarm_sound_frame, text="🎵 Мелодия", command=self._alarm_pick_sound,
                                          font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=4,
                                          cursor="hand2", relief="flat", activeforeground="white", takefocus=0,
                                          highlightthickness=0, highlightbackground=self.frame_bg, highlightcolor=self.frame_bg)
        self.btn_alarm_sound.pack(side="left")

        # Кнопка будильника + статус
        alarm_ctl_frame = tk.Frame(self.settings_card, bg=self.frame_bg)
        alarm_ctl_frame.pack(fill="x", pady=(5, 0))

        self.btn_alarm = tk.Button(alarm_ctl_frame, text="⏰ Установить", command=self._alarm_button_click,
                                    font=("Segoe UI", 10, "bold"), bd=0, padx=12, pady=5,
                                    cursor="hand2", relief="flat", activeforeground="white", takefocus=0,
                                    highlightthickness=0, highlightbackground=self.frame_bg, highlightcolor=self.frame_bg)
        self.btn_alarm.pack(side="left")

        self.alarm_status = tk.Label(alarm_ctl_frame, text="Будильник не установлен", bg=self.frame_bg,
                                      fg=self.label_fg, font=("Segoe UI", 9))
        self.alarm_status.pack(side="left", padx=(8, 0), fill="x", expand=True)

        # Кнопки
        self.btn_frame = tk.Frame(frm, bg=self.main_bg)
        self.btn_frame.pack(fill="x", pady=(0, 10))
        
        self.btn_start = tk.Button(self.btn_frame, text="▶ Старт", command=self.start,
                                   font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=9, width=12, height=1,
                                   cursor="hand2", relief="flat", activeforeground="white", takefocus=0,
                                   highlightthickness=0, highlightbackground=self.main_bg, highlightcolor=self.main_bg)
        self.btn_start.pack(side="left", padx=(0, 8), fill="both", expand=True)
        
        self.btn_idle = tk.Button(self.btn_frame, text="⏳ Бездействие", command=self.start_idle,
                                  font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=9, width=12, height=1,
                                  cursor="hand2", relief="flat", activeforeground="white", takefocus=0,
                                  highlightthickness=0, highlightbackground=self.main_bg, highlightcolor=self.main_bg)
        self.btn_idle.pack(side="left", padx=(0, 8), fill="both", expand=True)
        
        self.btn_pause = tk.Button(self.btn_frame, text="⏸ Пауза", command=self.toggle_pause,
                                   font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=9, width=12, height=1,
                                   cursor="hand2", relief="flat", activeforeground="white", state="disabled", takefocus=0,
                                   highlightthickness=0, highlightbackground=self.main_bg, highlightcolor=self.main_bg)
        self.btn_pause.pack(side="left", padx=(0, 8), fill="both", expand=True)
        
        self.btn_cancel = tk.Button(self.btn_frame, text="⏹ Отмена", command=self.cancel,
                                    font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=9, width=12, height=1,
                                    cursor="hand2", relief="flat", activeforeground="white", state="disabled", takefocus=0,
                                    highlightthickness=0, highlightbackground=self.main_bg, highlightcolor=self.main_bg)
        self.btn_cancel.pack(side="left", padx=(0, 8), fill="both", expand=True)
        
        self.btn_cancel_idle = tk.Button(self.btn_frame, text="⏹ Отмена", command=self.cancel_idle,
                                         font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=9, width=12, height=1,
                                         cursor="hand2", relief="flat", activeforeground="white", state="disabled", takefocus=0,
                                         highlightthickness=0, highlightbackground=self.main_bg, highlightcolor=self.main_bg)
        self.btn_cancel_idle.pack(side="left", padx=(0, 8), fill="both", expand=True)
        
        self.btn_detect = tk.Button(self.btn_frame, text="🔍 Найти", command=self.detect_windows,
                                    font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=9, width=12, height=1,
                                    cursor="hand2", relief="flat", activeforeground="white", takefocus=0,
                                    highlightthickness=0, highlightbackground=self.main_bg, highlightcolor=self.main_bg)
        self.btn_detect.pack(side="left", padx=(0, 8), fill="both", expand=True)
        
        self.btn_theme = tk.Button(self.btn_frame, text="🎨 Тема", command=self.toggle_theme,
                                   font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=9, width=12, height=1,
                                   cursor="hand2", relief="flat", activeforeground="white", takefocus=0,
                                   highlightthickness=0, highlightbackground=self.main_bg, highlightcolor=self.main_bg)
        self.btn_theme.pack(side="right", fill="both", expand=True)

        self.button_roles = {
            self.btn_start: "success",
            self.btn_pause: "warning",
            self.btn_cancel: "error",
            self.btn_detect: "accent",
            self.btn_theme: "neutral",
            self.btn_idle: "warning",
            self.btn_cancel_idle: "error",
            self.btn_alarm: "accent",
            self.btn_alarm_sound: "neutral",
            self.btn_alarm_month: "neutral",
        }
        self._apply_button_palette()
        self._bind_button_animations()

        # Скрываем кнопку отмены бездействия по умолчанию
        self.btn_cancel_idle.pack_forget()

        # Статус + прогресс
        self.status = tk.Label(frm, text="✓ Готово", bg=self.main_bg,
                               fg=self.success, font=("Segoe UI", 10, "bold"))
        self.status.pack(fill="x", pady=(4, 4))

        self.progress = ttk.Progressbar(frm, orient="horizontal", length=100, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 0))

        self.update_ui_colors()
        self._set_status("✓ Готово", self.success, pulse=False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Кнопка «–» при активном таймере/отслеживании прячет окно в трей
        root.bind("<Unmap>", self._on_root_unmap)

        # Сброс кнопок при потере фокуса окном (клик на рабочий стол)
        root.bind("<FocusOut>", lambda e: self._reset_all_buttons())
        
        # Сброс таймера бездействия при активности
        root.bind("<Motion>", self._reset_idle_counter)
        root.bind("<Key>", self._reset_idle_counter)
        root.bind("<Button>", self._reset_idle_counter)

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
        if self.current_theme == "dark":
            self.main_bg = "#040812"
            self.frame_bg = "#0e1625"
            self.entry_bg = "#0a1322"
            self.label_fg = "#9fb0c8"
            self.input_fg = "#eef4ff"
            self.card_border = "#2c3b55"
            self.card_border_accent = "#394b6b"
            self.accent = "#8aa2ff"
            self.success = "#22c55e"
            self.warning = "#f59e0b"
            self.error = "#ef4444"
            self.neutral_btn = "#1b2435"
            self.divider_color = "#20304a"
            self.title_subtle = "#b8c5da"
            self.glow_color = "#6d8dff"
            self.title_glow = "#9fb4ff"
        else:
            self.main_bg = "#edf3ff"
            self.frame_bg = "#ffffff"
            self.entry_bg = "#f7faff"
            self.label_fg = "#56657c"
            self.input_fg = "#0f172a"
            self.card_border = "#ccdbf2"
            self.card_border_accent = "#bfd2ef"
            self.accent = "#3f61ff"
            self.success = "#16a34a"
            self.warning = "#d97706"
            self.error = "#dc2626"
            self.neutral_btn = "#dfe7f5"
            self.divider_color = "#cdd9eb"
            self.title_subtle = "#6f8099"
            self.glow_color = "#7ea0ff"
            self.title_glow = "#5f7fff"

        self.button_palette = {
            "accent": self.accent,
            "success": self.success,
            "warning": self.warning,
            "error": self.error,
            "neutral": self.neutral_btn,
            "disabled": "#3b475d" if self.current_theme == "dark" else "#c7d3e3",
        }

    def _apply_button_palette(self):
        for button, role in self.button_roles.items():
            base_color = self.button_palette.get(role, self.accent)
            fg = "#0f172a" if role == "neutral" and self.current_theme == "light" else "white"
            if role == "neutral" and self.current_theme == "dark":
                fg = "#e2e8f0"

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

    def _bind_button_animations(self):
        for btn in self.button_roles.keys():
            btn.bind("<Enter>", lambda e, b=btn: self._on_button_enter(b))
            btn.bind("<Leave>", lambda e, b=btn: self._on_button_leave(b))
            btn.bind("<ButtonPress-1>", lambda e, b=btn: self._on_button_press(b))
            btn.bind("<ButtonRelease-1>", lambda e, b=btn: self._on_button_release(b))
            # Сброс состояния при потере фокуса (клик на рабочий стол)
            btn.bind("<FocusOut>", lambda e, b=btn: self._on_button_leave(b))

    def _cancel_button_animation(self, button):
        after_id = self._button_anim_after_ids.pop(button, None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass

    def _animate_button_to(self, button, target_color, steps=6, delay=18):
        if str(button.cget("state")) == "disabled":
            return
        self._cancel_button_animation(button)
        start_color = button.cget("bg")

        def step(i=1):
            t = i / steps
            cur = self._mix_color(start_color, target_color, t)
            try:
                button.config(bg=cur, activebackground=target_color)
            except Exception:
                return
            if i < steps:
                self._button_anim_after_ids[button] = self.root.after(delay, lambda: step(i + 1))
            else:
                self._button_anim_after_ids.pop(button, None)

        step()

    def _on_button_enter(self, button):
        if str(button.cget("state")) != "disabled":
            # Эффект поднятия кнопки при наведении
            button.config(relief="raised")
            self._animate_button_to(button, button._hover_bg)

    def _on_button_leave(self, button):
        if str(button.cget("state")) != "disabled":
            # Возвращаем плоский вид
            button.config(relief="flat")
            self._animate_button_to(button, button._base_bg)

    def _on_button_press(self, button):
        if str(button.cget("state")) != "disabled":
            # Эффект нажатия
            button.config(relief="sunken")
            self._animate_button_to(button, button._press_bg, steps=4, delay=14)

    def _on_button_release(self, button):
        if str(button.cget("state")) != "disabled":
            # Возвращаем к состоянию "над кнопкой"
            button.config(relief="raised")
            self._animate_button_to(button, button._hover_bg, steps=4, delay=14)

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

    def _reset_idle_counter(self, event=None):
        """Сброс таймера бездействия при любом действии мыши/клавиатуры."""
        if self.idle_alert_window is not None:
            return  # уже показываем предупреждение — игнорируем
        self.idle_stop_event.clear()

    def _start_idle_counter(self):
        """Запуск потока отслеживания бездействия."""
        idle_minutes = 0
        try:
            idle_minutes = int(self.idle_spin.get())
        except ValueError:
            pass
        if idle_minutes <= 0:
            return  # функция отключена
        
        self.idle_stop_event.clear()
        
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
            
            while not self.idle_stop_event.is_set():
                # Ждём 1 секунду
                if self.idle_stop_event.wait(1.0):
                    break  # остановка
                
                # Получаем время последнего ввода
                try:
                    lli = LASTINPUTINFO()
                    lli.cbSize = ctypes.sizeof(LASTINPUTINFO)
                    user32.GetLastInputInfo(ctypes.byref(lli))
                    now = kernel32.GetTickCount64()
                    idle_ms = now - lli.dwTime
                    idle_secs = idle_ms / 1000.0
                except Exception:
                    continue
                
                in_cooldown = time.time() < action_cooldown_until
                
                # Если пользователь вернулся (реальная активность, не наш ввод) — сбрасываем флаг
                if idle_secs < 2.0 and self.idle_action_executed and not in_cooldown:
                    self.idle_action_executed = False
                    self._ui_call(lambda: self._set_status("✓ Вы вернулись — отслеживание продолжено", self.success, pulse=False))
                
                # Если предупреждение открыто и пользователь активен - закрываем его
                if self.idle_alert_window is not None and idle_secs < 2.0 and not in_cooldown:
                    alert_ref = self.idle_alert_window
                    self._ui_call(lambda: self._cancel_idle_alert(alert_ref))
                
                # Проверяем порог
                if idle_secs >= idle_minutes * 60 and not self.idle_action_executed:
                    # Время бездействия истекло — выполняем действие
                    self.idle_action_executed = True  # Устанавливаем флаг ДО нажатия
                    action_cooldown_until = time.time() + 15  # игнорируем ввод 15 сек после действия
                    self._ui_call(self._on_idle_timeout)
                    # Закрываем предупреждение если есть
                    if self.idle_alert_window is not None:
                        alert_ref = self.idle_alert_window
                        self._ui_call(lambda: self._cancel_idle_alert(alert_ref))
                    continue
                
                # Показываем предупреждение за 5 минут до истечения
                # Но только если действие ещё не выполнялось
                if not self.idle_action_executed:
                    time_left = (idle_minutes * 60) - idle_secs
                    if 0 < time_left <= 300:  # 5 минут = 300 сек
                        if self.idle_alert_window is None:
                            self._ui_call(lambda: self._show_idle_alert(idle_minutes))
        
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
                    f"Через 5 минут будет выполнено действие:\n{self.key_var.get()}\n\n"
                    "Подвигайте мышь, чтобы отменить.",
                    "⏳ Sleep Pause Timer",
                )
            except Exception as e:
                print(f"DEBUG: tray notify error: {e}")
            return
        try:
            # Звуковой сигнал
            try:
                ctypes.windll.user32.MessageBeep(ctypes.c_uint(-1))
            except Exception:
                pass
            
            # Создаём окно предупреждения
            alert = tk.Toplevel(self.root)
            alert.title("⏳ Предупреждение: бездействие")
            alert.geometry("420x180")
            alert.resizable(False, False)
            alert.transient(self.root)
            alert.grab_set()
            alert.lift()
            alert.focus_force()
            
            # Цвета
            bg = self.frame_bg
            fg = self.input_fg
            accent = self.accent
            warning = self.warning
            
            alert.configure(bg=bg)
            
            # Текст
            tk.Label(alert, text="⚠️ Предупреждение", font=("Segoe UI", 14, "bold"),
                     bg=bg, fg=warning).pack(pady=(15, 5))
            
            tk.Label(alert, text=f"Через 5 минут будет выполнено действие:\n{self.key_var.get()}",
                     font=("Segoe UI", 10), bg=bg, fg=fg).pack(pady=5)
            
            tk.Label(alert, text="Подвигайте мышь или нажмите любую клавишу, чтобы отменить.",
                     font=("Segoe UI", 9, "italic"), bg=bg, fg=self.label_fg).pack(pady=(5, 15))
            
            # Кнопки
            btn_frame = tk.Frame(alert, bg=bg)
            btn_frame.pack(pady=(0, 15))
            
            def on_cancel():
                self.idle_alert_window = None
                alert.destroy()
            
            def on_confirm():
                self.idle_alert_window = None
                alert.destroy()
                # Сразу выполняем действие
                self.root.after(0, self._on_idle_timeout)
            
            tk.Button(btn_frame, text="Отменить", command=on_cancel,
                     font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=8,
                     cursor="hand2", relief="flat", bg="#1e293b", fg="white",
                     activebackground=accent, activeforeground="white",
                     highlightthickness=0, highlightbackground=bg, highlightcolor=bg).pack(side="left", padx=5)
            
            tk.Button(btn_frame, text="Выполнить сейчас", command=on_confirm,
                     font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=8,
                     cursor="hand2", relief="flat", bg=warning, fg="white",
                     activebackground=self._darken(warning, 0.2), activeforeground="white",
                     highlightthickness=0, highlightbackground=bg, highlightcolor=bg).pack(side="left", padx=5)
            
            self.idle_alert_window = alert
            
            # Закрываем окно по активности (привязываем к окну предупреждения)
            alert.bind("<Motion>", lambda e: self._cancel_idle_alert(alert))
            alert.bind("<Key>", lambda e: self._cancel_idle_alert(alert))
            
        except Exception as e:
            print(f"DEBUG: idle alert error: {e}")

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
        self._set_status("⏳ Бездействие: действие выполняется...", self.warning, pulse=False)
        
        # Вызываем то же действие, что и при завершении таймера
        key = self.key_var.get()
        title = self.win_title_entry.get().strip()
        
        # Выполняем действие
        if key == "shutdown":
            self.root.after(0, lambda: self._set_status("🔴 Выключение...", self.warning, pulse=False))
            self.root.after(1000, lambda: ctypes.windll.user32.ExitWindowsEx(0x00000001 | 0x00000004, 0xFFFFFFFF))
            return
        
        elif key == "sleep":
            self.root.after(0, lambda: self._set_status("💤 Спящий режим...", self.warning, pulse=False))
            self.root.after(1000, lambda: ctypes.windll.PowrProf.SetSuspendState(0, 0, 0))
            return
        
        if pyautogui is None:
            self.root.after(0, lambda: messagebox.showerror("Ошибка", "pyautogui не установлен"))
            return
        
        # Активируем окно и отправляем клавишу
        def send_key():
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
        if self.current_theme == "dark":
            apply_light_theme(self.style)
            self.current_theme = "light"
        else:
            apply_dark_theme(self.style)
            self.current_theme = "dark"
        self._apply_theme_palette()
        self.update_ui_colors()

    def update_ui_colors(self):
        self.root.configure(bg=self.main_bg)
        self.main_container.configure(bg=self.main_bg)
        self.title_label.configure(bg=self.main_bg, fg=self.accent)
        self.btn_frame.configure(bg=self.main_bg)

        self.time_card.configure(bg=self.frame_bg, highlightbackground=self.card_border, highlightcolor=self.card_border)
        self.settings_card.configure(bg=self.frame_bg, highlightbackground=self.card_border_accent, highlightcolor=self.card_border_accent)
        self.time_shadow.configure(bg=self.divider_color)
        self.settings_shadow.configure(bg=self.divider_color)

        for card in (self.time_card, self.settings_card):
            for child in card.winfo_children():
                try:
                    if child.winfo_class() == "Frame":
                        child.configure(bg=self.frame_bg)
                        for nested in child.winfo_children():
                            if nested.winfo_class() == "Label":
                                nested.configure(bg=self.frame_bg, fg=self.label_fg)
                    elif child.winfo_class() == "Label":
                        child.configure(bg=self.frame_bg, fg=self.label_fg)
                except Exception:
                    pass

        for entry in (self.entry_hour, self.entry_min, self.entry_sec, self.win_title_entry,
                      self.alarm_hour_entry, self.alarm_minute_entry, self.alarm_sound_entry):
            try:
                entry.configure(
                    bg=self.entry_bg,
                    fg=self.input_fg if str(entry.cget("state")) != "disabled" else self.label_fg,
                    insertbackground=self.accent,
                    highlightthickness=1,
                    highlightbackground=self.card_border,
                    highlightcolor=self.accent,
                )
            except Exception:
                pass

        # Спинбоксы и чекбокс будильника под тему
        for spin in (self.idle_spin, self.alarm_interval_spin):
            try:
                spin.configure(bg=self.entry_bg, fg=self.input_fg, buttonbackground=self.entry_bg,
                               insertbackground=self.accent, highlightthickness=1,
                               highlightbackground=self.card_border, highlightcolor=self.accent)
            except Exception:
                pass
        try:
            self.alarm_repeat_check.configure(
                bg=self.frame_bg, fg=self.label_fg,
                activebackground=self.frame_bg, activeforeground=self.label_fg,
                selectcolor=self.entry_bg,
                highlightbackground=self.frame_bg, highlightcolor=self.frame_bg)
        except Exception:
            pass
        # Дни недели чекбоксы под тему
        try:
            for cb in self.settings_card.winfo_children():
                if isinstance(cb, tk.Frame):
                    for child in cb.winfo_children():
                        if isinstance(child, tk.Checkbutton) and child.cget("text") in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]:
                            child.configure(
                                bg=self.frame_bg, fg=self.label_fg,
                                activebackground=self.frame_bg, activeforeground=self.label_fg,
                                selectcolor=self.entry_bg,
                                highlightbackground=self.frame_bg, highlightcolor=self.frame_bg)
        except Exception:
            pass

        self._apply_button_palette()
        self._set_button_state(self.btn_start, enabled=str(self.btn_start.cget("state")) != "disabled")
        self._set_button_state(self.btn_pause, enabled=str(self.btn_pause.cget("state")) != "disabled")
        self._set_button_state(self.btn_cancel, enabled=str(self.btn_cancel.cget("state")) != "disabled")

        self.status.configure(bg=self.main_bg)
        current_text = self.status.cget("text")
        if "Осталось" in current_text or "Таймер" in current_text:
            self._set_status(current_text, self.accent, pulse=True)
        elif "Пауза" in current_text or "Отмена" in current_text:
            self._set_status(current_text, self.warning, pulse=False)
        elif "Ошибка" in current_text:
            self._set_status(current_text, self.error, pulse=False)
        else:
            self._set_status(current_text, self.success, pulse=False)

        # Статус будильника перекрашивается по своему состоянию (после общих Label)
        self._alarm_tick(force=True)

    # ---------- Start / Cancel / Pause ----------
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
        
        # Запускаем отслеживание бездействия
        self._start_idle_counter()
        
        self.worker = threading.Thread(target=self._timer_thread, args=(total, key), daemon=True)
        self.worker.start()

    def start_idle(self):
        """Запуск только отслеживания бездействия (без основного таймера)."""
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
        self.idle_stop_event.clear()
        self._set_button_state(self.btn_idle, enabled=False)
        self.btn_idle.config(text="⏳ Отслеживание...")
        
        # Показываем кнопку отмены бездействия, скрываем кнопку отмены таймера
        self.btn_cancel.pack_forget()
        self.btn_cancel_idle.pack(side="left", padx=(0, 8), fill="both", expand=True)
        self._set_button_state(self.btn_cancel_idle, enabled=True)
        
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
        
        # Останавливаем отслеживание бездействия
        self._stop_idle_counter()
        
        # Скрываем кнопку отмены бездействия, показываем кнопку отмены таймера
        self.btn_cancel_idle.pack_forget()
        self.btn_cancel.pack(side="left", padx=(0, 8), fill="both", expand=True)

    def cancel_idle(self):
        """Остановка отслеживания бездействия."""
        self._stop_idle_counter()
        self._set_status("⏹ Бездействие отменено", self.warning, pulse=False)
        self._set_button_state(self.btn_idle, enabled=True)
        self.btn_idle.config(text="⏳ Бездействие")
        
        # Скрываем кнопку отмены бездействия, показываем кнопку отмены таймера
        self.btn_cancel_idle.pack_forget()
        self.btn_cancel.pack(side="left", padx=(0, 8), fill="both", expand=True)
        self._set_button_state(self.btn_cancel, enabled=False)

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
            alarm = view["alarms"].get(date_str)
            is_today = (date_str == now.date().isoformat())
            if alarm:
                text = f"{day}\n{alarm['hour']:02d}:{alarm['minute']:02d}"
                bg = self.accent
                fg = "white"
                active = self._darken(self.accent, 0.2)
            else:
                text = str(day)
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
                    btn = tk.Button(grid_frame, text=str(day), width=5, height=2,
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

        tk.Button(nav_frame, text="◀", command=prev_month, width=3,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=8, pady=4,
                  cursor="hand2", relief="flat", bg=self.neutral_btn, fg=self.input_fg,
                  takefocus=0, highlightthickness=0,
                  activebackground=self._darken(self.neutral_btn, 0.2), activeforeground=self.input_fg).pack(side="left")

        tk.Button(nav_frame, text="Закрыть", command=dlg.destroy,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=4,
                  cursor="hand2", relief="flat", bg=self.accent, fg="white",
                  takefocus=0, highlightthickness=0,
                  activebackground=self._lighten(self.accent, 0.2), activeforeground="white").pack(side="left", padx=10)

        tk.Button(nav_frame, text="▶", command=next_month, width=3,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=8, pady=4,
                  cursor="hand2", relief="flat", bg=self.neutral_btn, fg=self.input_fg,
                  takefocus=0, highlightthickness=0,
                  activebackground=self._darken(self.neutral_btn, 0.2), activeforeground=self.input_fg).pack(side="right")

        # Первая сборка
        build_month()

    def _show_time_picker(self, date_obj, date_str, render_callback):
        """Показывает диалог выбора времени для месячного будильника."""
        time_dlg = tk.Toplevel(self.root)
        time_dlg.title(f"⏰ Будильник на {date_obj.strftime('%d.%m.%Y')}")
        time_dlg.configure(bg=self.frame_bg)
        time_dlg.resizable(False, False)
        time_dlg.transient(self.root)
        time_dlg.grab_set()

        # Время
        tk.Label(time_dlg, text="Время:", font=("Segoe UI", 10),
                 bg=self.frame_bg, fg=self.label_fg).pack(anchor="w", padx=15, pady=(15, 5))
        
        time_frame = tk.Frame(time_dlg, bg=self.frame_bg)
        time_frame.pack(padx=15, pady=(0, 10))
        
        hour_var = tk.StringVar(value="07")
        minute_var = tk.StringVar(value="30")
        # Подставляем время из основной формы будильника, если оно валидно
        try:
            ph = int(self.alarm_hour_entry.get())
            pm = int(self.alarm_minute_entry.get())
            if 0 <= ph <= 23 and 0 <= pm <= 59:
                hour_var.set(f"{ph:02d}")
                minute_var.set(f"{pm:02d}")
        except ValueError:
            pass
        
        hour_entry = tk.Entry(time_frame, width=3, font=("Segoe UI", 14, "bold"),
                              bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                              insertbackground=self.accent, relief="flat",
                              highlightthickness=1, highlightbackground=self.card_border,
                              highlightcolor=self.accent,
                              textvariable=hour_var)
        hour_entry.pack(side="left")
        
        tk.Label(time_frame, text=":", bg=self.frame_bg, fg=self.label_fg,
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        
        minute_entry = tk.Entry(time_frame, width=3, font=("Segoe UI", 14, "bold"),
                                bg=self.entry_bg, fg=self.input_fg, bd=0, justify="center",
                                insertbackground=self.accent, relief="flat",
                                highlightthickness=1, highlightbackground=self.card_border,
                                highlightcolor=self.accent,
                                textvariable=minute_var)
        minute_entry.pack(side="left", padx=(0, 10))

        # Мелодия (используем текущую)
        sound = self._alarm_resolve_sound()
        tk.Label(time_dlg, text=f"Мелодия: {Path(sound).name if sound else 'не выбрана'}",
                 font=("Segoe UI", 9), bg=self.frame_bg, fg=self.label_fg).pack(anchor="w", padx=15)

        # Кнопки
        btn_frame = tk.Frame(time_dlg, bg=self.frame_bg)
        btn_frame.pack(fill="x", padx=15, pady=15)
        
        def on_ok():
            try:
                hour = int(hour_var.get())
                minute = int(minute_var.get())
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    messagebox.showerror("Ошибка", "Неверное время")
                    return
            except ValueError:
                messagebox.showerror("Ошибка", "Введите числа")
                return
            sound_path = self._alarm_resolve_sound()
            if not sound_path:
                messagebox.showerror("Ошибка", "Сначала выберите мелодию в настройках")
                return
            try:
                self.alarm.set_monthly_alarm(date_obj, hour, minute, sound_path)
            except AlarmError as exc:
                messagebox.showerror("Ошибка", str(exc))
                return
            time_dlg.destroy()
            render_callback()
            self._alarm_tick(force=True)
        
        def on_cancel():
            time_dlg.destroy()

        tk.Button(btn_frame, text="OK", command=on_ok,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=6,
                  cursor="hand2", relief="flat", bg=self.accent, fg="white",
                  activebackground=self._lighten(self.accent, 0.2), activeforeground="white",
                  highlightthickness=0).pack(side="left", padx=(0, 10))
        tk.Button(btn_frame, text="Отмена", command=on_cancel,
                  font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=6,
                  cursor="hand2", relief="flat", bg=self.neutral_btn, fg=self.input_fg,
                  activebackground=self._darken(self.neutral_btn, 0.2), activeforeground=self.input_fg,
                  highlightthickness=0).pack(side="left")

    def _toggle_monthly_alarm(self, date_obj, date_str, refresh_callback):
        """Клик по дню календаря: выключить будильник этого дня или включить.

        Выключает — сразу удаляет; включает — открывает выбор времени.
        Дни обновляются перекраской существующих кнопок (refresh_callback),
        без пересборки сетки — иначе месяц мигает на каждое действие.
        """
        if date_str in self.alarm.get_monthly_alarms():
            try:
                self.alarm.set_monthly_alarm(date_obj, None, None)
            except Exception as exc:
                messagebox.showerror("Ошибка", f"Не удалось отменить будильник: {exc}")
                return
            refresh_callback()
        else:
            self._show_time_picker(date_obj, date_str, refresh_callback)
        self._alarm_tick(force=True)

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
        dlg.title("Detected windows")
        dlg.geometry("700x400")
        dlg.transient(self.root)
        dlg.grab_set()

        lbl = ttk.Label(dlg, text="Найдены окна. Выберите номер и нажмите Использовать.")
        lbl.pack(anchor="w", padx=8, pady=(8,4))

        frame = ttk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        scrollbar = ttk.Scrollbar(frame, orient="vertical")
        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set, selectmode="browse", font=("Segoe UI", 9))
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

        btn_use = ttk.Button(btn_frame, text="Использовать", command=use_selected)
        btn_use.pack(side="left", padx=(0,6))
        btn_copy = ttk.Button(btn_frame, text="Копировать заголовок", command=copy_selected)
        btn_copy.pack(side="left", padx=(0,6))
        btn_close = ttk.Button(btn_frame, text="Закрыть", command=close_dlg)
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
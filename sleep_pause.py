#!/usr/bin/env python3
import math
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox
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

        # Кнопки
        self.btn_frame = tk.Frame(frm, bg=self.main_bg)
        self.btn_frame.pack(fill="x", pady=(0, 10))
        
        self.btn_start = tk.Button(self.btn_frame, text="▶ Старт", command=self.start,
                                   font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=9, width=12, height=1,
                                   cursor="hand2", relief="flat", activeforeground="white", takefocus=0,
                                   highlightthickness=0, highlightbackground=self.main_bg, highlightcolor=self.main_bg)
        self.btn_start.pack(side="left", padx=(0, 8), fill="both", expand=True)
        
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
        }
        self._apply_button_palette()
        self._bind_button_animations()

        # Статус + прогресс
        self.status = tk.Label(frm, text="✓ Готово", bg=self.main_bg,
                               fg=self.success, font=("Segoe UI", 10, "bold"))
        self.status.pack(fill="x", pady=(4, 4))

        self.progress = ttk.Progressbar(frm, orient="horizontal", length=100, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 0))

        self.update_ui_colors()
        self._set_status("✓ Готово", self.success, pulse=False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Сброс кнопок при потере фокуса окном (клик на рабочий стол)
        root.bind("<FocusOut>", lambda e: self._reset_all_buttons())
        
        # Сброс таймера бездействия при активности
        root.bind("<Motion>", self._reset_idle_counter)
        root.bind("<Key>", self._reset_idle_counter)
        root.bind("<Button>", self._reset_idle_counter)

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
        
        def thread():
            user32 = ctypes.windll.user32
            LAST_INPUT_SIZE = ctypes.sizeof(ctypes.c_uint64)
            
            while not self.idle_stop_event.is_set():
                # Ждём 1 секунду
                if self.idle_stop_event.wait(1.0):
                    break  # остановка
                
                # Получаем время последнего ввода
                last_input = ctypes.c_uint64(0)
                try:
                    user32.GetLastInputInfo(ctypes.byref(last_input))
                    now = ctypes.windll.kernel32.GetTickCount64()
                    idle_ms = now - last_input.value
                    idle_secs = idle_ms / 1000.0
                except Exception:
                    continue
                
                # Проверяем порог
                if idle_secs >= idle_minutes * 60:
                    # Время бездействия истекло — выполняем действие
                    self.root.after(0, self._on_idle_timeout)
                    break
                
                # Показываем предупреждение за 5 минут до истечения
                time_left = (idle_minutes * 60) - idle_secs
                if 0 < time_left <= 300:  # 5 минут = 300 сек
                    if self.idle_alert_window is None:
                        self.root.after(0, self._show_idle_alert, idle_minutes)
                        break
        
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

    def _show_idle_alert(self, idle_minutes):
        """Показывает предупреждение за 5 минут до срабатывания бездействия."""
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
            
            # Закрываем окно по активности
            self.root.bind("<Motion>", lambda e: self._cancel_idle_alert(alert))
            self.root.bind("<Key>", lambda e: self._cancel_idle_alert(alert))
            
        except Exception as e:
            print(f"DEBUG: idle alert error: {e}")

    def _cancel_idle_alert(self, alert):
        """Отменяет предупреждение при активности."""
        if self.idle_alert_window is not None:
            self.idle_alert_window = None
            try:
                alert.destroy()
            except Exception:
                pass

    def _on_idle_timeout(self):
        """Выполняет действие при истечении времени бездействия."""
        self._set_status("⏳ Бездействие: действие выполняется...", self.warning, pulse=False)
        
        # Скрываем главное окно
        try:
            self.root.withdraw()
        except Exception:
            pass
        
        # Вызываем то же действие, что и при завершении таймера
        key = self.key_var.get()
        title = self.win_title_entry.get().strip()
        
        # Выполняем действие
        if key == "shutdown":
            self.root.after(0, lambda: self._set_status("🔴 Выключение...", self.warning, pulse=False))
            time.sleep(1)
            ctypes.windll.user32.ExitWindowsEx(0x00000001 | 0x00000004, 0xFFFFFFFF)
            return
        
        elif key == "sleep":
            self.root.after(0, lambda: self._set_status("💤 Спящий режим...", self.warning, pulse=False))
            time.sleep(1)
            ctypes.windll.PowrProf.SetSuspendState(0, 0, 0)
            return
        
        if title:
            self._activate_window(title)
        
        if pyautogui is None:
            return
        
        try:
            pyautogui.press(key)
            self.root.after(0, lambda: self._set_status("✓ Клавиша отправлена!", self.success, pulse=False))
        except Exception as e:
            self.root.after(0, lambda err=e: messagebox.showerror("Ошибка", f"Не удалось отправить клавишу '{key}': {err}"))
        
        self.root.after(0, lambda: self._set_button_state(self.btn_start, enabled=True))
        self.root.after(0, lambda: self._set_button_state(self.btn_pause, enabled=False))
        self.root.after(0, lambda: self._set_button_state(self.btn_cancel, enabled=False))
        self.root.after(0, lambda: self._set_status("✓ Готово", self.success, pulse=False))

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

        for entry in (self.entry_hour, self.entry_min, self.entry_sec, self.win_title_entry):
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

    def cancel(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self._set_status("⏹ Отмена...", self.warning, pulse=False)
            self._set_button_state(self.btn_cancel, enabled=False)
        
        # Останавливаем отслеживание бездействия
        self._stop_idle_counter()

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
            self._set_status("▶ Возобновлено", self.success, pulse=False)
            self.button_roles[self.btn_pause] = "warning"
            self._apply_button_palette()
            self.btn_pause.config(text="⏸ Пауза")
            self._set_button_state(self.btn_pause, enabled=True)

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
        hours = self.current_remaining // 3600
        mins = (self.current_remaining % 3600) // 60
        secs = self.current_remaining % 60
        tooltip = f"Осталось {hours:02d}:{mins:02d}:{secs:02d}"
        try:
            self.tray_icon.title = tooltip
            running = (self.worker is not None and self.worker.is_alive() and self.current_remaining > 0)
            indicator = None
            if not running:
                indicator = (200, 0, 0)  # красный — таймер не активен
            elif self.paused:
                indicator = (0, 180, 0)  # зелёный — пауза
            if self.app_icon_img is not None and Image is not None:
                base = self.app_icon_img.copy()
            else:
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
                    pystray.MenuItem("Restore", lambda _: self._on_restore()),
                    pystray.MenuItem("Exit", lambda _: self._on_exit())
                )
                try:
                    if self.app_icon_img is not None:
                        default_img = ImageOps.contain(self.app_icon_img.copy(), (64, 64))
                    else:
                        default_img = self._make_image("0")
                    running = (self.worker is not None and self.worker.is_alive() and self.current_remaining > 0)
                    indicator = None if running else (200,0,0)
                    composed = self._compose_tray_icon_with_indicator(default_img, indicator_color=indicator)
                    if composed is not None:
                        self._icon_image = composed
                        self.tray_icon.icon = self._icon_image
                except Exception as e:
                    print("DEBUG: failed to create default tray image:", e)
                self.current_remaining = 0
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
        if self.worker and self.worker.is_alive():
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
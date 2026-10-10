"""Layout and saved appearance settings, independent of timer/alarm logic."""
import json
import os
import tkinter as tk
import time
import math
import webbrowser
from pathlib import Path
from tkinter import ttk
from timer_widgets import RoundedButton, RoundedCheck, RoundedChoice, RoundedEntry, RoundedPanel, RoundedSpin


ACTION_TITLES = {'playpause': 'Поставить воспроизведение на паузу',
                 'sleep': 'Перевести компьютер в сон', 'shutdown': 'Выключить компьютер'}


def apply_window_chrome(app, window):
    if os.name != 'nt': return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32')
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        hwnd = user32.GetParent(window.winfo_id())
        dwm = ctypes.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        attributes = {20: int(app.current_theme == 'dark'), 33: 2}
        for number, color in ((34, app.card_border), (35, app.main_bg), (36, app.input_fg)):
            red, green, blue = (int(color[i:i+2], 16) for i in (1, 3, 5))
            attributes[number] = red | (green << 8) | (blue << 16)
        for number, value in attributes.items():
            data = wintypes.DWORD(value)
            dwm.DwmSetWindowAttribute(hwnd, number, ctypes.byref(data), ctypes.sizeof(data))
    except (OSError, tk.TclError):
        pass  # Earlier Windows versions use their regular window frame.


def show_idle_warning(app, idle_minutes):
    """Warning uses the application's palette and icon, including its title bar."""
    alert = tk.Toplevel(app.root)
    alert.title('Предупреждение о бездействии')
    alert.configure(bg=app.main_bg)
    apply_window_chrome(app, alert)
    alert.bind('<Map>', lambda event: apply_window_chrome(app, alert) if event.widget == alert else None, add='+')
    alert.resizable(False, False)
    alert.transient(app.root)
    icon = Path(__file__).parent / 'app_icon.ico'
    if icon.is_file(): alert.iconbitmap(str(icon))
    elif app.app_icon_img is not None:
        from PIL import ImageTk
        alert._icon_ref = ImageTk.PhotoImage(app.app_icon_img)
        alert.iconphoto(False, alert._icon_ref)
    width, height = 610, 370
    x = max(0, app.root.winfo_rootx() + (app.root.winfo_width() - width) // 2)
    y = max(0, app.root.winfo_rooty() + (app.root.winfo_height() - height) // 2)
    alert.geometry(f'{width}x{height}+{x}+{y}')
    content = tk.Frame(alert, bg=app.main_bg, padx=22, pady=20)
    content.pack(fill='both', expand=True)
    header = tk.Frame(content, bg=app.main_bg)
    header.pack(fill='x', pady=(0, 16))
    if app.app_icon_img is not None:
        from PIL import ImageTk, Image
        alert._header_icon = ImageTk.PhotoImage(app.app_icon_img.resize((48, 48), Image.Resampling.LANCZOS))
        tk.Label(header, image=alert._header_icon, bg=app.main_bg).pack(side='left', padx=(0, 12))
    titles = tk.Frame(header, bg=app.main_bg)
    titles.pack(side='left', fill='x', expand=True)
    tk.Label(titles, text='Предупреждение', font=('Segoe UI', 18, 'bold'),
             bg=app.main_bg, fg=app.warning, anchor='w').pack(fill='x')
    tk.Label(titles, text='Мышь и клавиатура не используются', font=('Segoe UI', 10),
             bg=app.main_bg, fg=app.label_fg, anchor='w').pack(fill='x', pady=(3, 0))
    panel = RoundedPanel(content, padding=16)
    panel.pack(fill='x', pady=(0, 12))
    panel.set_theme(app.frame_bg, app.card_border)
    remaining = max(0, int(getattr(app, '_idle_warning_remaining_secs', min(idle_minutes * 60, 300))))
    countdown = tk.Label(panel.content, font=('Segoe UI', 20, 'bold'),
                         bg=app.frame_bg, fg=app.input_fg, anchor='w')
    countdown.pack(fill='x')
    deadline = time.monotonic() + remaining
    def tick():
        minutes, seconds = divmod(max(0, math.ceil(deadline - time.monotonic())), 60)
        countdown.configure(text=f'Через {minutes:02d}:{seconds:02d}')
        alert._countdown_after = alert.after(500, tick)
    def cancel_tick(event):
        if event.widget == alert:
            alert.after_cancel(alert._countdown_after)
    tick()
    alert.bind('<Destroy>', cancel_tick, add='+')
    tk.Label(panel.content, text=ACTION_TITLES.get(app.key_var.get(), 'Выполнить выбранное действие'),
             font=('Segoe UI', 11), bg=app.frame_bg, fg=app.input_fg, anchor='w',
             wraplength=520, justify='left').pack(fill='x', pady=(5, 0))
    tk.Label(content, text='Вернитесь к компьютеру — ожидание начнётся заново.',
             font=('Segoe UI', 10), bg=app.main_bg, fg=app.label_fg, anchor='w').pack(fill='x', pady=(0, 14))
    controls = tk.Frame(content, bg=app.main_bg)
    controls.pack(fill='x')
    cancel = RoundedButton(controls, text='Остановить отслеживание', command=app.cancel_idle,
                           bg=app.neutral_btn, fg=app.input_fg)
    cancel.set_theme(app.card_border, app.accent)
    cancel.pack(side='left', fill='x', expand=True, padx=(0, 8))
    def confirm():
        app.idle_action_executed = True
        app._cancel_idle_alert(alert)
        app._ui_call(app._on_idle_timeout)
    execute = RoundedButton(controls, text='Выполнить сейчас', command=confirm,
                            bg=app.accent, fg='#101722')
    execute.set_theme(app.card_border, app.accent)
    execute.pack(side='left', fill='x', expand=True)
    app.idle_alert_window = alert
    alert.protocol('WM_DELETE_WINDOW', app.cancel_idle)
    alert.bind('<Escape>', lambda e: app.cancel_idle())
    alert.grab_set()
    alert.lift()
    alert.focus_force()
    return alert


def load_preferences(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return {'theme': 'light' if data.get('theme') == 'light' else 'dark',
                'check_updates': data.get('check_updates') is True}
    except (OSError, ValueError, AttributeError):
        return {'theme': 'dark', 'check_updates': False}


def save_preferences(app):
    path = app.preferences_path
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'theme': app.current_theme,
                                'check_updates': app.auto_update_var.get()}, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, path)


def apply_palette(app):
    dark = app.current_theme == 'dark'
    values = {
        'main_bg': '#101722' if dark else '#ebf0f5',
        'frame_bg': '#19222f' if dark else '#f4f7fa',
        'entry_bg': '#222e3e' if dark else '#ffffff',
        'input_fg': '#e5edf6' if dark else '#1d293a',
        'label_fg': '#9fb2c6' if dark else '#4e6379',
        'card_border': '#36485e' if dark else '#c5d3e0',
        'accent': '#4be0c7' if dark else '#21bea7',
        'success': '#4be0c7' if dark else '#138a76',
        'warning': '#efc577' if dark else '#a66c12',
        'error': '#f28c97' if dark else '#c14759',
        'neutral_btn': '#222e3e' if dark else '#ffffff',
    }
    for key, value in values.items(): setattr(app, key, value)
    app.card_border_accent = app.card_border
    app.divider_color = app.card_border
    app.title_subtle = app.label_fg
    app.glow_color = app.accent
    app.title_glow = app.accent
    app.button_palette = {key: app.neutral_btn for key in ('neutral', 'warning', 'error')}
    app.button_palette.update(accent=app.accent, success=app.accent, disabled=app.entry_bg)
    app.style.theme_use('clam')
    app.style.configure('.', background=app.frame_bg, foreground=app.input_fg, font=('Segoe UI', 10))
    app.style.configure('TFrame', background=app.frame_bg)
    app.style.configure('TLabel', background=app.frame_bg, foreground=app.input_fg)
    app.style.configure('TProgressbar', background=app.accent, troughcolor=app.entry_bg,
                        bordercolor=app.entry_bg, lightcolor=app.accent, darkcolor=app.accent)
    app.style.configure('TScrollbar', background=app.card_border, troughcolor=app.entry_bg,
                        bordercolor=app.entry_bg, arrowcolor=app.label_fg)
    app.style.map('TScrollbar', background=[('active', app.label_fg)])


def apply_colors(app):
    def walk(widget, background):
        if isinstance(widget, RoundedPanel):
            widget.set_theme(app.frame_bg, app.card_border)
            walk(widget.content, app.frame_bg)
            return
        if isinstance(widget, RoundedEntry):
            widget.set_theme(app.entry_bg, app.input_fg, app.card_border, app.accent)
            return
        if isinstance(widget, RoundedCheck):
            widget.set_theme(app.entry_bg, app.input_fg, app.card_border, app.accent)
            return
        if isinstance(widget, RoundedButton):
            if isinstance(widget, RoundedChoice):
                widget._close()
                widget.configure(bg=app.entry_bg, fg=app.input_fg)
            widget.set_theme(app.card_border, app.accent)
            return
        if isinstance(widget, (tk.Frame, tk.Label)):
            widget.configure(bg=background)
            if isinstance(widget, tk.Label):
                widget.configure(fg=app.label_fg if getattr(widget, '_muted', False) else app.input_fg)
        for child in widget.winfo_children(): walk(child, background)
    app.root.configure(bg=app.main_bg)
    apply_window_chrome(app, app.root)
    walk(app.main_container, app.main_bg)
    app.btn_theme.configure(text='Светлая тема' if app.current_theme == 'dark' else 'Тёмная тема')
    app._apply_button_palette()
    for key, button in app.tab_buttons.items():
        button.configure(bg=app.accent if key == app.active_tab else app.neutral_btn,
                         fg='#101722' if key == app.active_tab else app.input_fg)
    app._alarm_tick(force=True)


def show_tab(app, key):
    app.active_tab = key
    for name, page in app.pages.items():
        if name == key: page.pack(fill='both', expand=True)
        else: page.pack_forget()
    apply_colors(app)


def build_interface(app):
    root = app.root
    root.title('Таймер паузы')
    root.bind('<Map>', lambda event: apply_window_chrome(app, root) if event.widget == root else None, add='+')
    root.resizable(True, True)
    root.geometry('920x860')
    root.minsize(860, 840)
    frm = app.main_container = tk.Frame(root, bg=app.main_bg, padx=24, pady=20)
    frm.pack(fill='both', expand=True)

    def frame(parent): return tk.Frame(parent, bg=parent.cget('bg'))
    def label(parent, text, muted=False, size=10, bold=False):
        item = tk.Label(parent, text=text, bg=parent.cget('bg'), fg=app.label_fg if muted else app.input_fg,
                        font=('Segoe UI', size, 'bold' if bold else 'normal'), anchor='w', justify='left')
        item._muted = muted
        return item
    def button(parent, text, command, role='neutral', disabled=False):
        item = RoundedButton(parent, text=text, command=command, state='disabled' if disabled else 'normal')
        app.button_roles[item] = role
        return item
    def card(parent, title, hint=None):
        panel = RoundedPanel(parent, padding=14)
        panel.pack(fill='x', pady=(0, 12))
        label(panel.content, title, size=12, bold=True).pack(fill='x', pady=(0, 8))
        if hint: label(panel.content, hint, muted=True).pack(fill='x', pady=(0, 10))
        return panel.content
    def entry(parent, text='', width=8, size=11, spin=None):
        cls = RoundedSpin if spin else RoundedEntry
        kwargs = dict(from_=spin[0], to=spin[1]) if spin else {}
        item = cls(parent, width=width, font=('Segoe UI', size), fg=app.input_fg,
                   insertbackground=app.accent, **kwargs)
        item.insert(0, text)
        return item

    header = frame(frm)
    header.pack(fill='x', pady=(0, 18))
    if app.app_icon_img is not None:
        from PIL import ImageTk, Image
        app._header_icon = ImageTk.PhotoImage(app.app_icon_img.resize((52, 52), Image.Resampling.LANCZOS))
        tk.Label(header, image=app._header_icon, bg=app.main_bg).pack(side='left', padx=(0, 14))
    titles = frame(header)
    titles.pack(side='left', fill='x', expand=True)
    app.title_label = label(titles, 'Таймер паузы', size=23, bold=True)
    app.title_label.pack(anchor='w')
    label(titles, 'Пауза, сон и будильники — в удобное время', muted=True).pack(anchor='w', pady=(3, 0))
    app.btn_theme = button(header, 'Светлая тема', app.toggle_theme)
    app.btn_theme.pack(side='right')

    tabs = frame(frm)
    tabs.pack(fill='x', pady=(0, 18))
    app.tab_buttons = {}
    app.active_tab = 'timer'
    for key, title in [('timer', 'Таймер'), ('alarm', 'Будильник'), ('updates', 'Обновления')]:
        item = RoundedButton(tabs, text=title, command=lambda k=key: show_tab(app, k), width=14)
        item.pack(side='left', padx=(0, 8))
        app.tab_buttons[key] = item
    body = frame(frm)
    body.pack(fill='both', expand=True)
    app.pages = {key: frame(body) for key in app.tab_buttons}
    timer, alarm, updates = (app.pages[key] for key in ('timer', 'alarm', 'updates'))

    clock = card(timer, 'Через сколько выполнить действие')
    row = frame(clock)
    row.pack(fill='x')
    for i, (attr, title) in enumerate([('entry_hour', 'Часы'), ('entry_min', 'Минуты'), ('entry_sec', 'Секунды')]):
        row.columnconfigure(i, weight=1)
        column = frame(row)
        column.grid(row=0, column=i, sticky='ew', padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
        label(column, title, muted=True).pack(fill='x', pady=(0, 4))
        field = entry(column, '0', size=23)
        field.entry.configure(justify='center')
        field.pack(fill='x')
        setattr(app, attr, field)
    app.btn_frame = frame(clock)
    app.btn_frame.pack(fill='x', pady=(14, 0))
    app.btn_start = button(app.btn_frame, 'Запустить таймер', app.start, 'success')
    app.btn_start.pack(side='left', fill='x', expand=True, padx=(0, 8))
    app.btn_pause = button(app.btn_frame, 'Пауза', app.toggle_pause, disabled=True)
    app.btn_pause.pack(side='left', fill='x', expand=True, padx=(0, 8))
    cancel_slot = frame(app.btn_frame)
    cancel_slot.pack(side='left', fill='x', expand=True)
    app.btn_cancel = button(cancel_slot, 'Отменить', app.cancel, 'error', True)
    app.btn_cancel.pack(side='left', fill='x', expand=True)

    action = card(timer, 'Что сделать')
    app.key_var = tk.StringVar(value='playpause')
    app.combo = RoundedChoice(action, app.key_var, ACTION_TITLES)
    app.combo.pack(fill='x', pady=(0, 8))
    label(action, 'Окно для активации · необязательно', muted=True).pack(fill='x', pady=(0, 4))
    row = frame(action)
    row.pack(fill='x')
    app.win_title_entry = entry(row)
    app.win_title_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
    app.btn_detect = button(row, 'Найти окно', app.detect_windows)
    app.btn_detect.pack(side='left')

    idle = card(timer, 'Бездействие', 'Запускается отдельно своей кнопкой. Использует действие, выбранное выше.')
    row = frame(idle)
    row.pack(fill='x')
    app.idle_spin = entry(row, '0', spin=(0, 120))
    app.idle_spin.pack(side='left')
    app.idle_label = label(row, 'минут', muted=True)
    app.idle_label.pack(side='left', padx=10)
    idle_buttons = frame(row)
    idle_buttons.pack(side='right')
    app.btn_cancel_idle = button(idle_buttons, 'Остановить', app.cancel_idle, 'error', True)
    app.btn_idle = button(idle_buttons, 'Отслеживать бездействие', app.start_idle)
    app.btn_idle.pack(side='left')

    clock = card(alarm, 'Время будильника')
    row = frame(clock)
    row.pack(fill='x')
    app.alarm_hour_entry = entry(row, '07', width=5, size=23)
    app.alarm_hour_entry.pack(side='left')
    label(row, ':', size=23).pack(side='left', padx=8)
    app.alarm_minute_entry = entry(row, '30', width=5, size=23)
    app.alarm_minute_entry.pack(side='left')
    app.alarm_repeat_var = tk.BooleanVar(value=True)
    app.alarm_repeat_check = RoundedCheck(row, 'Ежедневно', app.alarm_repeat_var)
    app.alarm_repeat_check.pack(side='left', padx=20)
    row = frame(clock)
    row.pack(fill='x', pady=(12, 0))
    label(row, 'Дни недели', muted=True).pack(side='left', padx=(0, 10))
    app.alarm_days_vars = []
    for title in ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']:
        var = tk.BooleanVar(value=True)
        app.alarm_days_vars.append(var)
        RoundedCheck(row, title, var).pack(side='left', padx=2)
    row = frame(clock)
    row.pack(fill='x', pady=(10, 0))
    label(row, 'Повтор звонка через', muted=True).pack(side='left', padx=(0, 10))
    app.alarm_interval_spin = entry(row, '5', width=5, spin=(1, 60))
    app.alarm_interval_spin.pack(side='left')
    label(row, 'минут', muted=True).pack(side='left', padx=10)
    app.btn_alarm_month = button(row, 'Расписание на месяц', app._show_alarm_month_schedule)
    app.btn_alarm_month.pack(side='right')

    sound = card(alarm, 'Мелодия', 'Выберите аудиофайл для звонка будильника.')
    row = frame(sound)
    row.pack(fill='x')
    app.alarm_sound_entry = entry(row)
    app.alarm_sound_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
    app.btn_alarm_sound = button(row, 'Выбрать файл', app._alarm_pick_sound)
    app.btn_alarm_sound.pack(side='left')
    app.btn_alarm = button(sound, 'Установить будильник', app._alarm_button_click, 'accent')
    app.btn_alarm.pack(anchor='w', pady=(14, 8))
    app.alarm_status = label(sound, 'Будильник не установлен', muted=True)
    app.alarm_status.configure(wraplength=760)
    app.alarm_status.pack(fill='x')

    update = card(updates, 'Обновление приложения', 'Новые версии загружаются из официальных релизов GitHub.')
    app.update_version = label(update, f'Установленная версия: {app.app_version}', size=14, bold=True)
    app.update_version.pack(fill='x', pady=(0, 12))
    app.auto_update_var = tk.BooleanVar(value=app.preferences['check_updates'])
    RoundedCheck(update, 'Проверять обновления при запуске', app.auto_update_var,
                 command=lambda: save_preferences(app)).pack(anchor='w', pady=(0, 12))
    row = frame(update)
    row.pack(fill='x', pady=(0, 12))
    app.btn_update_check = button(row, 'Проверить обновления', lambda: app.updater.check(), 'accent')
    app.btn_update_check.pack(side='left', padx=(0, 8))
    app.btn_update_install = button(row, 'Скачать и установить', lambda: app.updater.install(), 'accent', True)
    app.btn_update_install.pack(side='left', padx=(0, 8))
    app.btn_update_cancel = button(row, 'Отменить загрузку', lambda: app.updater.cancel(), disabled=True)
    app.btn_update_cancel.pack(side='left')
    app.update_status = label(update, 'Нажмите «Проверить обновления», чтобы узнать о новой версии.', muted=True)
    app.update_status.configure(wraplength=760)
    app.update_status.pack(fill='x', pady=(0, 8))
    app.update_progress = ttk.Progressbar(update, mode='determinate', maximum=100)
    app.update_progress.pack(fill='x', pady=(0, 12))
    button(update, 'Открыть страницу релизов', lambda: webbrowser.open('https://github.com/Voltur792/sleep-pause-timer/releases')).pack(anchor='w')
    label(update, 'После установки программа перезапустится.\nРасписание будильников и выбранная тема сохраняются.', muted=True).pack(fill='x', pady=(14, 0))

    footer = frame(frm)
    footer.pack(side='bottom', fill='x', pady=(12, 0), before=body)
    app.status = label(footer, 'Готово', size=11, bold=True)
    app.status.pack(fill='x', pady=(0, 8))
    app.progress = ttk.Progressbar(footer, mode='determinate')
    app.progress.pack(fill='x')
    label(footer, f'Sleep Pause Timer · {app.app_version}', muted=True, size=9).pack(anchor='w', pady=(8, 0))
    show_tab(app, 'timer')

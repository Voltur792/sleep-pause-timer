"""Painted Tk controls shared by the timer's light and dark themes."""
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageDraw, ImageFilter, ImageTk


def parent_color(widget):
    try:
        return widget.cget('bg')
    except tk.TclError:
        from tkinter import ttk
        return ttk.Style(widget).lookup(widget.cget('style') or widget.winfo_class(), 'background') or '#19222f'


def blend(a, b, amount):
    aa = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    bb = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return '#' + ''.join(f'{round(x + (y - x) * amount):02x}' for x, y in zip(aa, bb))


def plate(widget, fill, border, radius=12, glow=0):
    w, h = max(2, widget.winfo_width()), max(2, widget.winfo_height())
    if w < 12 or h < 12:
        return
    scale = 3
    image = Image.new('RGB', (w * scale, h * scale), parent_color(widget.master))
    inset = 4 if glow else 2
    box = (inset * scale, inset * scale, (w - inset - 1) * scale, (h - inset - 1) * scale)
    if glow:
        halo = Image.new('RGBA', image.size)
        ImageDraw.Draw(halo).rounded_rectangle(box, radius=radius * scale,
                                               outline=border, width=3 * scale)
        halo.putalpha(halo.getchannel('A').point(lambda x: int(x * glow * .55)))
        image = Image.alpha_composite(image.convert('RGBA'), halo.filter(ImageFilter.GaussianBlur(3 * scale)))
    ImageDraw.Draw(image).rounded_rectangle(box, radius=radius * scale, fill=fill,
                                            outline=border, width=scale)
    widget._plate_image = ImageTk.PhotoImage(image.resize((w, h), Image.Resampling.LANCZOS))
    widget.create_image(0, 0, image=widget._plate_image, anchor='nw', tags='plate')
    widget.tag_lower('plate')


class RoundedButton(tk.Canvas):
    """Button-compatible canvas, including keyboard focus and disabled state."""
    def __init__(self, master, text='', command=None, **kw):
        self._opts = dict(text=text, command=command, bg=kw.pop('bg', '#222e3e'),
                          fg=kw.pop('fg', '#e5edf6'), state=kw.pop('state', 'normal'),
                          font=kw.pop('font', ('Segoe UI', 10, 'bold')),
                          disabledforeground=kw.pop('disabledforeground', '#8190a3'))
        self._hover = False
        self._pressed = False
        self._glow = 0.0
        self._animation = None
        self._border = '#36485e'
        self._accent = '#4be0c7'
        f = tkfont.Font(font=self._opts['font'])
        width = max(54, f.measure('0') * kw.pop('width', 0), f.measure(text) + 30)
        height = max(38, f.metrics('linespace') * kw.pop('height', 1) + 20)
        super().__init__(master, width=width, height=height, bg=parent_color(master),
                         bd=0, highlightthickness=0, takefocus=1, cursor='hand2')
        self.bind('<Configure>', self._draw)
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<FocusIn>', self._draw)
        self.bind('<FocusOut>', self._draw)
        self.bind('<space>', lambda e: self.invoke())
        self.bind('<Return>', lambda e: self.invoke())
        self.bind('<Destroy>', self._destroyed, add='+')

    def _destroyed(self, event):
        if event.widget == self and self._animation:
            self.after_cancel(self._animation)
            self._animation = None

    def configure(self, cnf=None, **kw):
        if not hasattr(self, '_opts'):
            return super().configure(cnf, **kw)
        if cnf:
            kw.update(cnf)
        for k in ('text', 'command', 'bg', 'fg', 'state', 'font', 'disabledforeground', 'activebackground'):
            if k in kw:
                self._opts[k] = kw.pop(k)
        for k in ('relief', 'overrelief', 'activeforeground', 'highlightbackground', 'highlightcolor', 'padx', 'pady', 'bd', 'takefocus', 'cursor'):
            kw.pop(k, None)
        if kw:
            super().configure(**kw)
        self._draw()
    config = configure

    def cget(self, key):
        if key in self._opts:
            return self._opts[key]
        return super().cget(key)

    def set_theme(self, border, accent):
        self._border, self._accent = border, accent
        super().configure(bg=parent_color(self.master))
        self._draw()

    def _draw(self, event=None):
        if not self.winfo_exists():
            return
        self.delete('all')
        disabled = self._opts['state'] == 'disabled'
        fill = self._opts['bg']
        if disabled:
            fill = blend(fill, parent_color(self.master), .55)
        elif self._pressed:
            fill = blend(fill, '#000000', .12)
        elif self._hover:
            fill = blend(fill, self._accent, .13)
        border = self._accent if (self._hover or self.focus_get() == self) and not disabled else self._border
        plate(self, fill, border, glow=0 if disabled else self._glow)
        self.create_text(self.winfo_width() / 2, self.winfo_height() / 2,
                         text=self._opts['text'], font=self._opts['font'],
                         fill=self._opts['disabledforeground'] if disabled else self._opts['fg'])

    def _set_hover(self, value):
        self._hover = value
        self._pressed = False
        if self._animation:
            self.after_cancel(self._animation)
        self._animate()

    def _animate(self):
        target = 1.0 if self._hover else 0.0
        self._glow += (target - self._glow) * .35
        self._draw()
        self._animation = self.after(16, self._animate) if abs(target - self._glow) > .02 else None

    def _press(self, event):
        if self._opts['state'] != 'disabled':
            self.focus_set()
            self._pressed = True
            self._draw()

    def _release(self, event):
        pressed = self._pressed
        self._pressed = False
        self._draw()
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if self._opts['state'] != 'disabled' and self._opts['command']:
            return self._opts['command']()


class RoundedEntry(tk.Canvas):
    def __init__(self, master, width=20, font=('Segoe UI', 11), **kw):
        self.fill = kw.pop('bg', '#222e3e')
        self.border = '#36485e'
        self.accent = '#4be0c7'
        f = tkfont.Font(font=font)
        super().__init__(master, width=f.measure('0') * width + 26,
                         height=f.metrics('linespace') + 26, bg=parent_color(master),
                         bd=0, highlightthickness=0)
        for k in ('bd', 'relief', 'takefocus', 'highlightthickness', 'highlightbackground', 'highlightcolor'):
            kw.pop(k, None)
        self.entry = tk.Entry(self, font=font, bg=self.fill, bd=0, relief='flat',
                              highlightthickness=0, **kw)
        self._window = self.create_window(12, 0, window=self.entry, anchor='w')
        self.bind('<Configure>', self._draw)
        self.entry.bind('<FocusIn>', self._draw)
        self.entry.bind('<FocusOut>', self._draw)
        self.bind('<Button-1>', lambda e: self.entry.focus_set())

    def _draw(self, event=None):
        tk.Canvas.delete(self, 'plate')
        plate(self, self.fill, self.accent if self.entry.focus_get() == self.entry else self.border)
        self.coords(self._window, 12, self.winfo_height() / 2)
        self.itemconfigure(self._window, width=max(10, self.winfo_width() - 26))

    def set_theme(self, fill, fg, border, accent):
        self.fill, self.border, self.accent = fill, border, accent
        super().configure(bg=parent_color(self.master))
        self.entry.configure(bg=fill, fg=fg, insertbackground=accent,
                              selectbackground=accent, selectforeground='#101722',
                              disabledbackground=fill, disabledforeground=fg)
        self._draw()

    def get(self): return self.entry.get()
    def delete(self, *args): return self.entry.delete(*args)
    def insert(self, *args): return self.entry.insert(*args)
    def focus_set(self): return self.entry.focus_set()
    def selection_range(self, *args): return self.entry.selection_range(*args)


class RoundedSpin(RoundedEntry):
    def __init__(self, master, from_=0, to=120, **kw):
        self.minimum, self.maximum = from_, to
        super().__init__(master, **kw)
        self.entry.bind('<Up>', lambda e: self._step(1))
        self.entry.bind('<Down>', lambda e: self._step(-1))
        self.bind('<Button-1>', self._click)

    def _draw(self, event=None):
        super()._draw(event)
        self.itemconfigure(self._window, width=max(10, self.winfo_width() - 45))
        tk.Canvas.delete(self, 'arrows')
        x, y = self.winfo_width() - 16, self.winfo_height() / 2
        self.create_text(x, y - 7, text='▴', fill=self.accent, tags='arrows', font=('Segoe UI', 10))
        self.create_text(x, y + 7, text='▾', fill=self.accent, tags='arrows', font=('Segoe UI', 10))

    def _click(self, event):
        if event.x > self.winfo_width() - 32:
            self._step(1 if event.y < self.winfo_height() / 2 else -1)
        else:
            self.entry.focus_set()

    def _step(self, direction):
        try: value = int(self.get())
        except ValueError: value = self.minimum
        self.delete(0, 'end')
        self.insert(0, str(min(self.maximum, max(self.minimum, value + direction))))
        return 'break'


class RoundedCheck(tk.Canvas):
    def __init__(self, master, text, variable, command=None):
        self.variable, self.text, self.command = variable, text, command
        self.fg, self.border, self.accent, self.fill = '#e5edf6', '#36485e', '#4be0c7', '#222e3e'
        f = tkfont.Font(font=('Segoe UI', 10))
        super().__init__(master, width=f.measure(text) + 34, height=32, bg=parent_color(master),
                         bd=0, highlightthickness=0, takefocus=1, cursor='hand2')
        self._trace = variable.trace_add('write', self._draw)
        self.bind('<Configure>', self._draw)
        self.bind('<Button-1>', self._toggle)
        self.bind('<space>', self._toggle)
        self.bind('<FocusIn>', self._draw)
        self.bind('<FocusOut>', self._draw)
        self.bind('<Destroy>', self._destroyed)

    def _destroyed(self, event):
        if event.widget == self:
            self.variable.trace_remove('write', self._trace)

    def _toggle(self, event=None):
        self.focus_set()
        self.variable.set(not self.variable.get())
        if self.command: self.command()
        return 'break'

    def set_theme(self, fill, fg, border, accent):
        self.fill, self.fg, self.border, self.accent = fill, fg, border, accent
        self.configure(bg=parent_color(self.master))
        self._draw()

    def _draw(self, *args):
        self.delete('all')
        active = self.variable.get()
        self.create_rectangle(3, 8, 19, 24, fill=self.accent if active else self.fill,
                               outline=self.accent if self.focus_get() == self else self.border)
        if active:
            self.create_line(6, 16, 10, 20, 16, 12, fill='#101722', width=2)
        self.create_text(28, 16, text=self.text, anchor='w', fill=self.fg, font=('Segoe UI', 10))


class RoundedChoice(RoundedButton):
    def __init__(self, master, variable, choices):
        self.variable, self.choices, self.popup = variable, choices, None
        super().__init__(master, command=self._open, text=choices[variable.get()] + '   ▾')
        self._trace = variable.trace_add('write', self._changed)
        self.bind('<Down>', lambda e: self._open())
        self.bind('<F4>', lambda e: self._open())
        self.bind('<Destroy>', self._cleanup, add='+')

    def _changed(self, *args):
        self.configure(text=self.choices[self.variable.get()] + '   ▾')

    def _cleanup(self, event):
        if event.widget == self:
            self.variable.trace_remove('write', self._trace)
            self._close()

    def _close(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None

    def _open(self):
        if self.popup:
            self._close()
            return
        pop = self.popup = tk.Toplevel(self)
        pop.overrideredirect(True)
        pop.configure(bg=self._border)
        frame = tk.Frame(pop, bg=self._opts['bg'], padx=4, pady=4)
        frame.pack(fill='both', expand=True, padx=1, pady=1)
        for key, title in self.choices.items():
            def select(value=key):
                self.variable.set(value)
                self._close()
                self.focus_set()
            btn = RoundedButton(frame, text=title, command=select, bg=self._opts['bg'], fg=self._opts['fg'])
            btn.set_theme(self._border, self._accent)
            btn.pack(fill='x')
        pop.geometry(f'{self.winfo_width()}x{len(self.choices) * 38 + 10}+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height()}')
        pop.bind('<Escape>', lambda e: self._close())
        pop.bind('<FocusOut>', lambda e: self.after(80, self._close_if_outside))
        pop.focus_force()

    def _close_if_outside(self):
        focus = self.focus_get()
        if self.popup and (focus is None or not str(focus).startswith(str(self.popup))):
            self._close()


class RoundedPanel(tk.Canvas):
    def __init__(self, master, padding=18):
        self.fill, self.border, self.padding = '#19222f', '#36485e', padding
        super().__init__(master, bd=0, highlightthickness=0, bg=parent_color(master), height=100)
        self.content = tk.Frame(self, bg=self.fill)
        self._window = self.create_window(padding, padding, window=self.content, anchor='nw')
        self.bind('<Configure>', self._draw)
        self.content.bind('<Configure>', self._resize)

    def _resize(self, event=None):
        height = self.content.winfo_reqheight() + 2 * self.padding
        if height != self.winfo_reqheight():
            self.configure(height=height)
        self._draw()

    def _draw(self, event=None):
        self.delete('plate')
        plate(self, self.fill, self.border, radius=16)
        self.itemconfigure(self._window, width=max(10, self.winfo_width() - 2 * self.padding))

    def set_theme(self, fill, border):
        self.fill, self.border = fill, border
        self.configure(bg=parent_color(self.master))
        self.content.configure(bg=fill)
        self._draw()

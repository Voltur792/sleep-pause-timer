"""GitHub release checks and an independent, atomic Windows EXE updater."""
import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

REPOSITORY = 'Voltur792/sleep-pause-timer'
API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
LIMIT = 160 * 1024 * 1024


class UpdateError(Exception): pass
class Cancelled(UpdateError): pass


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    if not match: raise UpdateError('Не удалось прочитать номер версии.')
    return tuple(int(part) for part in match.groups())


def installed_version():
    try:
        return json.loads((Path(__file__).parent / 'build_version.json').read_text(encoding='utf-8'))['version']
    except (OSError, ValueError, KeyError):
        return 'исходники'


def request(url):
    return urllib.request.Request(url, headers={'User-Agent': 'SleepPauseTimer-Updater',
                                                'Accept': 'application/vnd.github+json'})


def latest_release():
    with urllib.request.urlopen(request(API), timeout=15) as response:
        release = json.loads(response.read(1024 * 1024))
    version = release.get('tag_name', '')
    version_tuple(version)
    if release.get('draft') or release.get('prerelease'):
        raise UpdateError('Стабильный релиз пока недоступен.')
    expected = f'SleepPauseTimer-v{version.lstrip("v")}-windows-x64.zip'
    assets = [asset for asset in release.get('assets', []) if asset.get('name') == expected]
    if len(assets) != 1: raise UpdateError('В релизе пока нет готовой Windows-сборки.')
    asset = assets[0]
    digest = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise UpdateError('GitHub не предоставил контрольную сумму пакета. Попробуйте позже.')
    expected_url = f'https://github.com/{REPOSITORY}/releases/download/{version}/{expected}'
    if asset.get('browser_download_url') != expected_url:
        raise UpdateError('Неожиданный адрес пакета обновления.')
    size = asset.get('size')
    if not isinstance(size, int) or not 0 < size <= LIMIT:
        raise UpdateError('Некорректный размер пакета обновления.')
    return dict(version=version.lstrip('v'), url=expected_url, size=size, sha256=digest[7:])


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(256 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def safe_directory(path):
    """Refuse redirected update directories, including Windows junctions."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists():
            info = part.lstat()
            if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise UpdateError('Папка обновления перенаправлена. Выберите обычную папку приложения.')
    return path


def exe_version(path):
    if os.name != 'nt': raise UpdateError('Обновление EXE доступно только в Windows.')
    from ctypes import wintypes
    dll = ctypes.WinDLL('version', use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    dll.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    handle = wintypes.DWORD()
    size = dll.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
    if not size: raise UpdateError('В файле обновления отсутствует версия приложения.')
    buffer = ctypes.create_string_buffer(size)
    if not dll.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise UpdateError('Не удалось прочитать версию EXE.')
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    if not dll.VerQueryValueW(buffer, '\\', ctypes.byref(ptr), ctypes.byref(length)) or length.value < 52:
        raise UpdateError('Повреждены сведения о версии EXE.')
    fixed = ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD))
    if fixed[0] != 0xFEEF04BD: raise UpdateError('Некорректные сведения о версии EXE.')
    ms, ls = fixed[2], fixed[3]
    return f'{ms >> 16}.{ms & 65535}.{ls >> 16}'


def unpack_package(package, destination, release, verifier=exe_version):
    if package.stat().st_size != release['size'] or sha256(package) != release['sha256']:
        raise UpdateError('Проверка пакета не пройдена: размер или SHA-256 не совпадает.')
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != 'sleep_pause.exe':
            raise UpdateError('Пакет должен содержать только sleep_pause.exe.')
        info = entries[0]
        if info.is_dir() or not 0 < info.file_size <= LIMIT or info.flag_bits & 1:
            raise UpdateError('Некорректный файл внутри пакета обновления.')
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise UpdateError('Ссылки внутри пакета обновления запрещены.')
        with archive.open(info) as source, open(destination, 'xb') as output:
            shutil.copyfileobj(source, output, 256 * 1024)
    if verifier(destination) != release['version']:
        raise UpdateError('Версия EXE не совпадает с версией релиза.')
    return sha256(destination)


def download_package(release, destination, cancel, progress):
    count = 0
    with urllib.request.urlopen(request(release['url']), timeout=15) as source, open(destination, 'xb') as output:
        while True:
            if cancel.is_set(): raise Cancelled('Загрузка отменена.')
            chunk = source.read(256 * 1024)
            if not chunk: break
            count += len(chunk)
            if count > release['size']: raise UpdateError('Размер загрузки превышает размер пакета.')
            output.write(chunk)
            progress(count, release['size'])
    if cancel.is_set(): raise Cancelled('Загрузка отменена.')


def independent_environment():
    env = os.environ.copy()
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def wait_for_process(pid, target, timeout=60000):
    """Wait only; never terminate the running application."""
    from ctypes import wintypes
    dll = ctypes.WinDLL('kernel32', use_last_error=True)
    dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.OpenProcess.restype = wintypes.HANDLE
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    dll.WaitForSingleObject.restype = wintypes.DWORD
    dll.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    handle = dll.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87: return  # already exited
        raise UpdateError('Не удалось дождаться закрытия приложения.')
    try:
        name, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not dll.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
            raise UpdateError('Не удалось проверить процесс приложения.')
        if os.path.normcase(os.path.abspath(name.value)) != os.path.normcase(str(target)):
            raise UpdateError('Процесс установки не соответствует приложению.')
        if dll.WaitForSingleObject(handle, timeout) != 0:
            raise UpdateError('Приложение не закрылось. Повторите обновление.')
    finally:
        dll.CloseHandle(handle)


def replace_executable(target, staged, expected_sha, launch=None):
    target, staged = Path(target).absolute(), Path(staged).absolute()
    safe_directory(target.parent)
    if target.is_symlink() or staged.is_symlink(): raise UpdateError('Обновление ссылок запрещено.')
    if sha256(staged) != expected_sha: raise UpdateError('Файл обновления был изменён.')
    backup = staged.parent / 'previous.exe'
    temporary = target.with_name(f'.spt-update-{uuid.uuid4().hex}.exe')
    def replace(source, destination):
        # The onefile bootloader can briefly keep the EXE locked while it
        # removes its extraction directory after the GUI process exits.
        deadline = time.monotonic() + 15
        while True:
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                if os.name != 'nt' or time.monotonic() >= deadline: raise
                time.sleep(.15)
    shutil.copy2(target, backup)
    try:
        shutil.copy2(staged, temporary)
        if sha256(temporary) != expected_sha: raise UpdateError('Ошибка копирования обновления.')
        replace(temporary, target)
        if launch:
            try: launch(target)
            except Exception:
                shutil.copy2(backup, temporary)
                replace(temporary, target)
                launch(target)
                raise UpdateError('Не удалось запустить новую версию. Предыдущая версия восстановлена.')
    finally:
        temporary.unlink(missing_ok=True)


def run_helper(receipt_path):
    receipt_path = Path(receipt_path).absolute()
    try:
        directory = safe_directory(receipt_path.parent)
        if directory.name[:7] != 'update-' or receipt_path.name != 'receipt.json':
            raise UpdateError('Некорректное задание обновления.')
        if Path(sys.executable).absolute() != directory / 'helper.exe':
            raise UpdateError('Установка запущена из неверной папки.')
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        target = Path(receipt['target']).absolute()
        staged = directory / 'new.exe'
        if target.name != 'sleep_pause.exe' or not target.is_file() or target == staged:
            raise UpdateError('Некорректный путь приложения.')
        safe_directory(directory)
        if sha256(staged) != receipt['sha256'] or exe_version(staged) != receipt['version']:
            raise UpdateError('Файл обновления не прошёл повторную проверку.')
        wait_for_process(int(receipt['pid']), target)
        def launch(path):
            return subprocess.Popen([str(path)], cwd=str(path.parent), env=independent_environment(),
                                    creationflags=0x08000000)
        replace_executable(target, staged, receipt['sha256'], launch)
        (directory.parent / 'last-update.json').write_text(json.dumps({'version': receipt['version'], 'success': True}), encoding='utf-8')
        return 0
    except Exception as exc:
        try: (receipt_path.parent / 'error.txt').write_text(str(exc), encoding='utf-8')
        except OSError: pass
        if os.name == 'nt': ctypes.windll.user32.MessageBoxW(None, str(exc), 'Не удалось обновить Таймер паузы', 0x10)
        return 1


class UpdateController:
    def __init__(self, app):
        self.app, self.events, self.cancel_event = app, queue.Queue(), threading.Event()
        self.release = None
        self.busy = False
        self.closed = False
        self.after_id = app.root.after(120, self._poll)
        app.root.bind('<Destroy>', self._destroyed, add='+')
        if app.auto_update_var.get(): app.root.after(1800, self.check)

    def _destroyed(self, event):
        if event.widget == self.app.root:
            self.closed = True
            self.cancel_event.set()
            if self.after_id:
                try: self.app.root.after_cancel(self.after_id)
                except Exception: pass

    def _poll(self):
        if self.closed: return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    count, total = value
                    self.app.update_progress['value'] = count * 100 / total
                    self.app.update_status.configure(text=f'Загрузка: {count / 1048576:.1f} из {total / 1048576:.1f} МБ')
                elif kind == 'release':
                    self.release = value
                    current = self.app.app_version
                    try: newer = version_tuple(value['version']) > version_tuple(current)
                    except UpdateError: newer = False
                    text = f'Доступна версия {value["version"]}.' if newer else f'Новых обновлений нет. Последний опубликованный релиз: {value["version"]}.'
                    if current == 'исходники': text = f'Последний релиз: {value["version"]}. Установка доступна в EXE-сборке.'
                    self._finish(text)
                    self.app.btn_update_install.configure(state='normal' if newer and getattr(sys, 'frozen', False) else 'disabled')
                elif kind == 'error': self._finish(value)
                elif kind == 'staged': self._handoff(*value)
        except queue.Empty: pass
        if not self.closed: self.after_id = self.app.root.after(120, self._poll)

    def _finish(self, text):
        self.busy = False
        self.app.update_status.configure(text=text)
        self.app.btn_update_check.configure(state='normal')
        self.app.btn_update_cancel.configure(state='disabled')
        if self.release:
            try: newer = version_tuple(self.release['version']) > version_tuple(self.app.app_version)
            except UpdateError: newer = False
            self.app.btn_update_install.configure(state='normal' if newer and getattr(sys, 'frozen', False) else 'disabled')

    def check(self):
        if self.busy or self.closed: return
        self.busy = True
        self.release = None
        self.app.btn_update_check.configure(state='disabled')
        self.app.btn_update_install.configure(state='disabled')
        self.app.update_progress['value'] = 0
        self.app.update_status.configure(text='Проверяем новые версии…')
        def work():
            try: self.events.put(('release', latest_release()))
            except Exception as exc: self.events.put(('error', f'Не удалось проверить обновления: {exc}'))
        threading.Thread(target=work, daemon=True).start()

    def _active(self):
        app = self.app
        return ((app.worker is not None and app.worker.is_alive()) or
                (app.idle_counter_thread is not None and app.idle_counter_thread.is_alive()) or
                app.alarm.state().get('playing'))

    def install(self):
        from tkinter import messagebox
        if self.busy or not self.release or not getattr(sys, 'frozen', False): return
        if self._active():
            messagebox.showinfo('Обновление', 'Остановите таймер, отслеживание бездействия и текущий звонок перед установкой.', parent=self.app.root)
            return
        if not messagebox.askyesno('Установить обновление', f'Установить версию {self.release["version"]}?\nПрограмма перезапустится. Расписание и тема сохранятся.', parent=self.app.root): return
        self.busy = True
        self.cancel_event.clear()
        self.app.btn_update_install.configure(state='disabled')
        self.app.btn_update_check.configure(state='disabled')
        self.app.btn_update_cancel.configure(state='normal')
        self.app.update_status.configure(text='Начинаем загрузку…')
        release = self.release.copy()
        def work():
            directory = None
            try:
                base = safe_directory(self.app.preferences_path.parent / 'updates')
                directory = base / ('update-' + uuid.uuid4().hex)
                directory.mkdir(parents=True)
                package = directory / 'package.zip'
                download_package(release, package, self.cancel_event, lambda n, t: self.events.put(('progress', (n, t))))
                digest = unpack_package(package, directory / 'new.exe', release)
                if self.cancel_event.is_set(): raise Cancelled('Загрузка отменена.')
                self.events.put(('staged', (directory, digest, release['version'])))
            except Exception as exc:
                if directory: shutil.rmtree(directory, ignore_errors=True)
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        self.cancel_event.set()
        self.app.update_status.configure(text='Отменяем загрузку…')

    def _handoff(self, directory, digest, version):
        if self.cancel_event.is_set() or self._active():
            shutil.rmtree(directory, ignore_errors=True)
            self._finish('Установка отменена: приложение занято или загрузка отменена.')
            return
        try:
            target = Path(sys.executable).absolute()
            if target.name != 'sleep_pause.exe': raise UpdateError('Для обновления EXE должен называться sleep_pause.exe.')
            safe_directory(target.parent)
            # Check write access before closing the application.
            probe = target.parent / ('.spt-write-' + uuid.uuid4().hex)
            with open(probe, 'xb'): pass
            probe.unlink()
            helper = directory / 'helper.exe'
            shutil.copy2(target, helper)
            receipt = directory / 'receipt.json'
            receipt.write_text(json.dumps(dict(target=str(target), sha256=digest, version=version, pid=os.getpid())), encoding='utf-8')
            subprocess.Popen([str(helper), '--apply-update', str(receipt)], cwd=str(directory),
                             env=independent_environment(), creationflags=0x08000000)
            self.app.update_status.configure(text='Устанавливаем обновление и перезапускаем программу…')
            self.closed = True
            self.app.stop_event.set()
            self.app.idle_stop_event.set()
            self.app.alarm.stop_thread()
            if self.app.tray_icon: self.app.tray_icon.stop()
            self.app.root.destroy()
        except Exception as exc:
            self._finish(f'Не удалось начать установку: {exc}')

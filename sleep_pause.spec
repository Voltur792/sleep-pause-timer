# -*- mode: python ; coding: utf-8 -*-

import os
import glob

# Ищем иконки в текущей директории
icon_ico = None
icon_png = None

# Ищем все файлы .ico и .png в текущей директории
for f in glob.glob("*.ico"):
    icon_ico = os.path.abspath(f)
    break
for f in glob.glob("*.png"):
    icon_png = os.path.abspath(f)
    break

# Собираем дополнительные файлы (иконки)
extra_files = []
if icon_png and os.path.exists(icon_png):
    extra_files.append((icon_png, '.'))
if icon_ico and os.path.exists(icon_ico):
    extra_files.append((icon_ico, '.'))

a = Analysis(
    ['sleep_pause.py'],
    pathex=[],
    binaries=[],
    datas=extra_files,
    hiddenimports=['PIL', 'pystray', 'pyautogui', 'pygetwindow', 'win32gui', 'win32con', 'psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='sleep_pause',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_ico,
)

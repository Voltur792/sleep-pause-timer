# -*- mode: python ; coding: utf-8 -*-

import os
import glob
import json
import re
import subprocess
from pathlib import Path
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
)

# CI gets the version from its release tag; local builds can set the same tag value.
release_tag = os.environ.get('SPT_BUILD_VERSION') or os.environ.get('GITHUB_REF_NAME')
if not release_tag:
    release_tag = subprocess.check_output(['git', 'describe', '--tags', '--abbrev=0'], text=True).strip()
if not re.fullmatch(r'v?\d+\.\d+\.\d+', release_tag):
    raise ValueError('Build requires a stable release tag, such as vX.Y.Z')
release_version = release_tag.lstrip('v')
version_parts = tuple(map(int, release_version.split('.'))) + (0,)
metadata_dir = Path('build') / 'metadata'
metadata_dir.mkdir(parents=True, exist_ok=True)
metadata_path = metadata_dir / 'build_version.json'
metadata_path.write_text(json.dumps({'version': release_version}), encoding='utf-8')
version_resource = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_parts, prodvers=version_parts, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'Voltur'),
        StringStruct('FileDescription', 'Sleep Pause Timer'),
        StringStruct('FileVersion', release_version),
        StringStruct('ProductName', 'Sleep Pause Timer'),
        StringStruct('ProductVersion', release_version),
        StringStruct('OriginalFilename', 'sleep_pause.exe'),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])],
)

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
extra_files = [(str(metadata_path.resolve()), '.')]
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
    version=version_resource,
)

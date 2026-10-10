import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from timer_updates import (UpdateError, Cancelled, latest_release, version_tuple,
                           unpack_package, sha256, replace_executable, download_package,
                           independent_environment)


class UpdateTests(unittest.TestCase):
    def package(self, folder, entries):
        path = Path(folder) / 'package.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in entries: archive.writestr(name, data)
        release = dict(version='2.0.0', size=path.stat().st_size, sha256=sha256(path))
        return path, release

    def test_version_order_is_numeric_and_rejects_preview(self):
        self.assertGreater(version_tuple('v1.10.0'), version_tuple('1.9.0'))
        for version in ('v1.2.3-beta', '1.2', '2.0.0/../', ''):
            with self.assertRaises(UpdateError): version_tuple(version)

    def test_release_requires_exact_asset_url_and_digest(self):
        url = 'https://github.com/Voltur792/sleep-pause-timer/releases/download/v2.0.0/SleepPauseTimer-v2.0.0-windows-x64.zip'
        asset = dict(name='SleepPauseTimer-v2.0.0-windows-x64.zip', browser_download_url=url, size=42, digest='sha256:' + 'a' * 64)
        payload = dict(tag_name='v2.0.0', draft=False, prerelease=False, assets=[asset])
        def read_release():
            with patch('timer_updates.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
                return latest_release()
        self.assertEqual(read_release()['version'], '2.0.0')
        asset['browser_download_url'] = 'https://example.com/application.zip'
        with self.assertRaises(UpdateError): read_release()
        asset['browser_download_url'] = url
        asset.pop('digest')
        with self.assertRaises(UpdateError): read_release()

    def test_zip_traversal_extra_file_and_duplicate_rejected(self):
        for entries in ([('../sleep_pause.exe', b'exe')], [('sleep_pause.exe', b'exe'), ('settings.json', b'{}')],
                        [('sleep_pause.exe', b'a'), ('sleep_pause.exe', b'b')]):
            with tempfile.TemporaryDirectory() as folder:
                path, release = self.package(folder, entries)
                with self.assertRaises(UpdateError):
                    unpack_package(path, Path(folder) / 'new.exe', release, verifier=lambda p: '2.0.0')

    def test_hash_size_and_exe_version_are_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            path, release = self.package(folder, [('sleep_pause.exe', b'executable')])
            target = Path(folder) / 'new.exe'
            wrong = dict(release, sha256='0' * 64)
            with self.assertRaises(UpdateError): unpack_package(path, target, wrong)
            self.assertFalse(target.exists())
            with self.assertRaises(UpdateError): unpack_package(path, target, release, verifier=lambda p: '1.0.0')
            target.unlink()
            result = unpack_package(path, target, release, verifier=lambda p: '2.0.0')
            self.assertEqual(result, sha256(target))

    def test_atomic_replace_preserves_schedule_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target, staged = root / 'sleep_pause.exe', root / 'new.exe'
            target.write_bytes(b'old')
            staged.write_bytes(b'new')
            schedule, prefs = root / 'alarm.json', root / 'ui-settings.json'
            schedule.write_bytes(b'schedule')
            prefs.write_bytes(b'preferences')
            launched = []
            replace_executable(target, staged, sha256(staged), launch=launched.append)
            self.assertEqual(target.read_bytes(), b'new')
            self.assertEqual((root / 'previous.exe').read_bytes(), b'old')
            self.assertEqual(schedule.read_bytes(), b'schedule')
            self.assertEqual(prefs.read_bytes(), b'preferences')
            self.assertEqual(launched, [target])

    def test_failed_launch_restores_old_exe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target, staged = root / 'sleep_pause.exe', root / 'new.exe'
            target.write_bytes(b'old')
            staged.write_bytes(b'new')
            def launch(path):
                if path.read_bytes() == b'new': raise OSError('launch failed')
            with self.assertRaises(UpdateError): replace_executable(target, staged, sha256(staged), launch)
            self.assertEqual(target.read_bytes(), b'old')
            self.assertFalse(list(root.glob('.spt-update-*')))

    def test_cancelled_download_never_installs(self):
        with tempfile.TemporaryDirectory() as folder:
            cancel = threading.Event()
            cancel.set()
            with patch('timer_updates.urllib.request.urlopen', return_value=io.BytesIO(b'package')):
                with self.assertRaises(Cancelled):
                    download_package(dict(url='https://github.com/package', size=7), Path(folder) / 'package.zip', cancel, lambda n,t: None)

    def test_independent_onefile_helper_resets_environment(self):
        self.assertEqual(independent_environment()['PYINSTALLER_RESET_ENVIRONMENT'], '1')


if __name__ == '__main__': unittest.main()

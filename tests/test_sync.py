"""Tests for tools/sync.py — the unified push/clean/status tool.

Exercises the pure helpers (regex guard, device resolution,
zip-name lookup, find_*) end-to-end, plus the destructive verbs
under temp-dir DOWNLOADS / remote_root so we can prove they touch
exactly what they claim.
"""
from __future__ import annotations

import importlib.util
import io
import pathlib
import shutil
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SYNC_PATH = REPO_ROOT / 'tools' / 'sync.py'


def _load_sync():
    """Load tools/sync.py as a module without polluting sys.path."""
    spec = importlib.util.spec_from_file_location('sync', SYNC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sync = _load_sync()


def _make_zip(path: pathlib.Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        for arcname, data in files.items():
            zf.writestr(arcname, data)


class WorkDir:
    """Sandbox the module's DOWNLOADS path and any per-device
    `volume` / `remote_root` so tests can stage files without
    touching the real ones. Restores everything on exit."""

    def __init__(self):
        self._tmp = None
        self._module_patches = []  # (attr_name, original_value)
        self._device_patches = []  # (device, key, original_value)

    def __enter__(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix='sync-test-'))
        self.downloads = self._tmp / 'Downloads'
        self.downloads.mkdir()
        self._module_patches.append(('DOWNLOADS', sync.DOWNLOADS))
        sync.DOWNLOADS = self.downloads
        return self

    def __exit__(self, *exc):
        # Restore in reverse: device-spec dict patches first (so the
        # spec is back to its real shape), then module attrs.
        for device, key, original in reversed(self._device_patches):
            sync.DEVICES[device][key] = original
        for attr, original in reversed(self._module_patches):
            setattr(sync, attr, original)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def patch_device_paths(self, device: str, volume: pathlib.Path,
                           remote_subpath: str, *,
                           mount: bool = True) -> tuple[pathlib.Path, pathlib.Path]:
        """Repoint a device's volume + remote_root at temp paths.

        `mount=True` (default) creates the volume dir so the device
        looks plugged in; `mount=False` leaves it absent so
        `detect_device` / `resolve_device` see no device. Returns
        (volume, remote_root). Originals restored on exit."""
        spec = sync.DEVICES[device]
        self._device_patches.append((device, 'volume', spec['volume']))
        self._device_patches.append((device, 'remote_root', spec['remote_root']))
        spec['volume'] = volume
        spec['remote_root'] = volume / remote_subpath
        if mount:
            volume.mkdir(parents=True, exist_ok=True)
        return volume, spec['remote_root']


class AdjNounPatternTest(unittest.TestCase):
    def test_strict_match(self):
        rx = sync.adj_noun_pattern('.ot.zip')
        self.assertTrue(rx.match('foo-bar.ot.zip'))
        self.assertTrue(rx.match('alpha-beta.ot.zip'))
        # Disallowed: digits, custom names, missing hyphen, wrong suffix.
        self.assertFalse(rx.match('foo-bar.s4.zip'))
        self.assertFalse(rx.match('FOO-BAR.ot.zip'))   # uppercase
        self.assertFalse(rx.match('foobar.ot.zip'))     # no hyphen
        self.assertFalse(rx.match('foo-bar-baz.ot.zip'))  # 3 segments
        self.assertFalse(rx.match('foo-bar.ot.zip.bak'))


class DetectDeviceTest(unittest.TestCase):
    def test_no_device_returns_none(self):
        with WorkDir() as wd:
            wd.patch_device_paths('octatrack', wd._tmp / 'fake-ot', 'strudelbeats', mount=False)
            wd.patch_device_paths('torso-s4',  wd._tmp / 'fake-s4', 'samples/strudelbeats', mount=False)
            self.assertIsNone(sync.detect_device())

    def test_one_device_returns_its_key(self):
        with WorkDir() as wd:
            wd.patch_device_paths('octatrack', wd._tmp / 'fake-ot', 'strudelbeats', mount=True)
            wd.patch_device_paths('torso-s4',  wd._tmp / 'fake-s4', 'samples/strudelbeats', mount=False)
            self.assertEqual(sync.detect_device(), 'octatrack')

    def test_both_mounted_returns_none(self):
        with WorkDir() as wd:
            wd.patch_device_paths('octatrack', wd._tmp / 'fake-ot', 'strudelbeats', mount=True)
            wd.patch_device_paths('torso-s4',  wd._tmp / 'fake-s4', 'samples/strudelbeats', mount=True)
            self.assertIsNone(sync.detect_device())


class ResolveDeviceTest(unittest.TestCase):
    def test_alias_expansion(self):
        with WorkDir():
            key, _ = sync.resolve_device('ot')
            self.assertEqual(key, 'octatrack')
            key, _ = sync.resolve_device('s4')
            self.assertEqual(key, 'torso-s4')

    def test_unknown_exits(self):
        with WorkDir():
            with self.assertRaises(SystemExit):
                sync.resolve_device('nope')

    def test_no_arg_no_device_exits(self):
        with WorkDir() as wd:
            wd.patch_device_paths('octatrack', wd._tmp / 'fake-ot', 'strudelbeats', mount=False)
            wd.patch_device_paths('torso-s4',  wd._tmp / 'fake-s4', 'samples/strudelbeats', mount=False)
            with self.assertRaises(SystemExit) as ctx:
                sync.resolve_device(None)
            self.assertIn('no device', str(ctx.exception))

    def test_no_arg_both_mounted_exits(self):
        with WorkDir() as wd:
            wd.patch_device_paths('octatrack', wd._tmp / 'fake-ot', 'strudelbeats', mount=True)
            wd.patch_device_paths('torso-s4',  wd._tmp / 'fake-s4', 'samples/strudelbeats', mount=True)
            with self.assertRaises(SystemExit) as ctx:
                sync.resolve_device(None)
            self.assertIn('multiple', str(ctx.exception))


class FindLocalTest(unittest.TestCase):
    def test_pattern_filter_and_sort(self):
        with WorkDir() as wd:
            (wd.downloads / 'alpha-beta.ot.zip').touch()
            (wd.downloads / 'gamma-delta.ot.zip').touch()
            (wd.downloads / 'unrelated.ot.zip').touch()  # disallowed by regex
            (wd.downloads / 'gamma-delta.s4.zip').touch()
            files = sync.find_local('.ot.zip')
            self.assertEqual([p.name for p in files],
                             ['alpha-beta.ot.zip', 'gamma-delta.ot.zip'])
            files = sync.find_local('.ot.zip', pattern='gam')
            self.assertEqual([p.name for p in files], ['gamma-delta.ot.zip'])


class FindRemoteTest(unittest.TestCase):
    def test_ot_skips_audio_dir_and_stubs(self):
        with WorkDir() as wd:
            volume, root = wd.patch_device_paths(
                'octatrack', wd._tmp / 'OCTATRACK', 'strudelbeats',
            )
            root.mkdir(parents=True)
            (root / 'AUDIO').mkdir()                                  # shared, skip
            valid = root / 'PROJ_OK'
            valid.mkdir()
            (valid / 'project.work').touch()
            (root / 'STUB_NOWORK').mkdir()                            # stub
            spec = sync.DEVICES['octatrack']
            self.assertEqual(
                [p.name for p in sync.find_remote_projects(spec)],
                ['PROJ_OK'],
            )
            self.assertEqual(
                [p.name for p in sync.find_remote_stubs(spec)],
                ['STUB_NOWORK'],
            )

    def test_s4_returns_all_dirs_no_marker(self):
        with WorkDir() as wd:
            volume, root = wd.patch_device_paths(
                'torso-s4', wd._tmp / 'S4', 'samples/strudelbeats',
            )
            root.mkdir(parents=True)
            (root / 'foo-bar').mkdir()
            (root / 'baz-qux').mkdir()
            spec = sync.DEVICES['torso-s4']
            self.assertEqual(
                [p.name for p in sync.find_remote_projects(spec)],
                ['baz-qux', 'foo-bar'],
            )
            # No marker → no concept of stubs.
            self.assertEqual(sync.find_remote_stubs(spec), [])


class ProjectNameFromZipTest(unittest.TestCase):
    def test_ot_zip_uses_project_work_marker(self):
        with WorkDir() as wd:
            zp = wd._tmp / 'foo-bar.ot.zip'
            _make_zip(zp, {
                'FOO-BAR/project.work': b'',
                'FOO-BAR/markers.work': b'',
                'AUDIO/projects/FOO-BAR/sample.wav': b'\x00',
            })
            self.assertEqual(
                sync.project_name_from_zip(zp, 'project.work', '.ot.zip'),
                'FOO-BAR',
            )

    def test_s4_zip_uses_first_top_level_dir(self):
        with WorkDir() as wd:
            zp = wd._tmp / 'foo-bar.s4.zip'
            _make_zip(zp, {
                'foo-bar/track1.wav': b'\x00',
                'foo-bar/track2.wav': b'\x00',
            })
            self.assertEqual(
                sync.project_name_from_zip(zp, None, '.s4.zip'),
                'foo-bar',
            )


class CleanLocalTest(unittest.TestCase):
    def test_force_removes_only_matching_suffix(self):
        with WorkDir() as wd:
            (wd.downloads / 'alpha-beta.ot.zip').touch()
            (wd.downloads / 'gamma-delta.ot.zip').touch()
            (wd.downloads / 'foo-bar.s4.zip').touch()  # different device
            sync.clean_local(sync.DEVICES['octatrack'], pattern=None, force=True)
            remaining = sorted(p.name for p in wd.downloads.iterdir())
            self.assertEqual(remaining, ['foo-bar.s4.zip'])


class CleanRemoteOctatrackTest(unittest.TestCase):
    def test_force_removes_project_and_paired_audio(self):
        with WorkDir() as wd:
            volume, root = wd.patch_device_paths(
                'octatrack', wd._tmp / 'OCTATRACK', 'strudelbeats',
            )
            root.mkdir(parents=True)
            (root / 'PROJ_A').mkdir()
            (root / 'PROJ_A' / 'project.work').touch()
            audio = root / 'AUDIO' / 'projects' / 'PROJ_A'
            audio.mkdir(parents=True)
            (audio / 'sample.wav').write_bytes(b'\x00')
            (root / 'PROJ_B').mkdir()
            (root / 'PROJ_B' / 'project.work').touch()

            sync.clean_remote(sync.DEVICES['octatrack'], pattern='proj_a', force=True)

            self.assertFalse((root / 'PROJ_A').exists())
            self.assertFalse(audio.exists())
            self.assertTrue((root / 'PROJ_B').exists())  # untouched
            self.assertTrue((root / 'AUDIO' / 'projects').exists())  # parent dir kept


class CleanStubsTest(unittest.TestCase):
    def test_only_stubs_removed_audio_kept(self):
        with WorkDir() as wd:
            volume, root = wd.patch_device_paths(
                'octatrack', wd._tmp / 'OCTATRACK', 'strudelbeats',
            )
            root.mkdir(parents=True)
            (root / 'AUDIO').mkdir()
            (root / 'PROJ_OK').mkdir()
            (root / 'PROJ_OK' / 'project.work').touch()
            (root / 'STUB_X').mkdir()
            (root / 'STUB_Y').mkdir()

            sync.clean_stubs(sync.DEVICES['octatrack'], pattern=None, force=True)

            self.assertTrue((root / 'AUDIO').exists())
            self.assertTrue((root / 'PROJ_OK').exists())
            self.assertFalse((root / 'STUB_X').exists())
            self.assertFalse((root / 'STUB_Y').exists())

    def test_unsupported_for_s4(self):
        # S4 has no project marker, so stubs aren't a meaningful
        # concept — fail loudly rather than silently no-op.
        with WorkDir() as wd:
            wd.patch_device_paths('torso-s4', wd._tmp / 'S4', 'samples/strudelbeats')
            with self.assertRaises(SystemExit):
                sync.clean_stubs(sync.DEVICES['torso-s4'], pattern=None, force=True)


class PushTest(unittest.TestCase):
    def test_force_extracts_and_skips_existing(self):
        with WorkDir() as wd:
            volume, root = wd.patch_device_paths(
                'octatrack', wd._tmp / 'OCTATRACK', 'strudelbeats',
            )
            zp = wd.downloads / 'foo-bar.ot.zip'
            _make_zip(zp, {
                'FOO-BAR/project.work': b'work-bytes',
                'AUDIO/projects/FOO-BAR/s.wav': b'\x00\x00',
            })
            already = wd.downloads / 'baz-qux.ot.zip'
            _make_zip(already, {'BAZ-QUX/project.work': b''})
            (root / 'BAZ-QUX').mkdir(parents=True)  # already present on device

            sync.push(sync.DEVICES['octatrack'], pattern=None, force=True)

            self.assertTrue((root / 'FOO-BAR' / 'project.work').exists())
            self.assertTrue((root / 'AUDIO' / 'projects' / 'FOO-BAR' / 's.wav').exists())
            # BAZ-QUX was not extracted on top of itself (its dir is empty).
            self.assertEqual(list((root / 'BAZ-QUX').iterdir()), [])

    def test_unsupported_for_strudel(self):
        # strudel has no remote — push must fail loudly.
        with WorkDir():
            with self.assertRaises(SystemExit):
                sync.push(sync.DEVICES['strudel'], pattern=None, force=True)


class StatusTest(unittest.TestCase):
    def test_strudel_local_only(self):
        with WorkDir() as wd:
            (wd.downloads / 'alpha-beta.strudel.js').touch()
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                sync.status(sync.DEVICES['strudel'])
            finally:
                sys.stdout = old
            out = buf.getvalue()
            self.assertIn('alpha-beta.strudel.js', out)
            self.assertNotIn('remote', out)

    def test_ot_local_remote_split(self):
        with WorkDir() as wd:
            volume, root = wd.patch_device_paths(
                'octatrack', wd._tmp / 'OCTATRACK', 'strudelbeats',
            )
            zp = wd.downloads / 'foo-bar.ot.zip'
            _make_zip(zp, {'FOO-BAR/project.work': b''})
            (root / 'FOO-BAR').mkdir(parents=True)
            (root / 'FOO-BAR' / 'project.work').touch()
            (root / 'BAZ-QUX').mkdir()
            (root / 'BAZ-QUX' / 'project.work').touch()

            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                sync.status(sync.DEVICES['octatrack'])
            finally:
                sys.stdout = old
            out = buf.getvalue()
            self.assertIn('foo-bar.ot.zip', out)
            self.assertIn('FOO-BAR', out)
            self.assertIn('BAZ-QUX', out)


class MainNoArgsTest(unittest.TestCase):
    """Bare `sync.py` (no subcommand) should run `status` rather than
    error out — the destructive verbs stay explicit, but the safe
    default is always available."""

    def test_no_subcommand_runs_status(self):
        with WorkDir() as wd:
            wd.patch_device_paths('octatrack', wd._tmp / 'OCTATRACK',
                                  'strudelbeats', mount=True)
            wd.patch_device_paths('torso-s4', wd._tmp / 'S4',
                                  'samples/strudelbeats', mount=False)
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                sync.main([])
            finally:
                sys.stdout = old
            self.assertIn('local', buf.getvalue())
            self.assertIn('remote', buf.getvalue())


if __name__ == '__main__':
    unittest.main()

"""Tests for the torso-s4 renderer (per-row WAV bundle target)."""
from __future__ import annotations

import unittest
import wave
import zipfile

from ._fixtures import (
    WorkDir,
    load_render_module,
    make_break_wavs,
    make_capture_cell,
    make_export,
    stub_sample_fetch,
)


class TorsoS4HelpersTest(unittest.TestCase):
    def setUp(self):
        self.render = load_render_module('torso-s4')

    def test_event_ms_at_120bpm(self):
        # 120 BPM, 4 beats/cycle, 8 events/cycle → 250 ms/event exact.
        self.assertEqual(self.render.event_ms(120, 4, 8), 250.0)

    def test_event_ms_at_90bpm(self):
        # 90 BPM, 4 beats/cycle, 8 events/cycle → 333.333... ms float.
        # Returned as float so cumulative rounding in render_cell can
        # keep the bar length exact across long rows.
        self.assertAlmostEqual(self.render.event_ms(90, 4, 8), 1000 / 3)

    def test_event_ms_at_128bpm(self):
        # 128 BPM, 4 beats/cycle, 8 events/cycle → 234.375 ms — the
        # tempera default. Pre-fix this rounded down to 234, dropping
        # 12 ms across a 4-cell × 8-event row.
        self.assertAlmostEqual(self.render.event_ms(128, 4, 8), 234.375)

    def test_unique_row_names_are_distinct(self):
        import random
        rng = random.Random(42)
        names = self.render.unique_row_names(rng, 5)
        self.assertEqual(len(names), 5)
        self.assertEqual(len(set(names)), 5)

    def test_unique_row_names_deterministic(self):
        import random
        a = self.render.unique_row_names(random.Random(99), 3)
        b = self.render.unique_row_names(random.Random(99), 3)
        self.assertEqual(a, b)


class TorsoS4RoundtripTest(unittest.TestCase):
    def test_render_emits_zip_with_one_wav_per_row(self):
        render = load_render_module('torso-s4')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['kk', 'sn'], bpm=120, steps=32)
            stub_sample_fetch(render, paths)

            payload = make_export([
                # row 1: 1 cell
                [make_capture_cell(['kk', 'sn', 'kk', 'sn'],
                                   [0, 4, 8, None, 1, 5, 9, 13])],
                # row 2: 2 cells (so this row's wav is 2x the length)
                [make_capture_cell(['kk'], [0, 1, 2, 3, 4, 5, 6, 7]),
                 make_capture_cell(['sn'], [0, 1, 2, 3, 4, 5, 6, 7])],
            ])
            wd.write_export(payload)

            render.OUTPUT_DIR = wd.root / 'out'
            render.RENDER_DIR = wd.root / 'render'
            zip_path = render.render(wd.export_path, 'TORSOSMOKE', seed=1234)
            self.assertTrue(zip_path.exists())

            with zipfile.ZipFile(zip_path, 'r') as zf:
                names = sorted(zf.namelist())
            # Two wavs, both under TORSOSMOKE/.
            self.assertEqual(len(names), 2)
            for n in names:
                self.assertTrue(n.startswith('TORSOSMOKE/'))
                self.assertTrue(n.endswith('.wav'))

    def test_row_wav_length_matches_cells_x_cycle(self):
        render = load_render_module('torso-s4')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['a'], bpm=120, steps=32)
            stub_sample_fetch(render, paths)

            # Row 1: 1 cell. Row 2: 2 cells. At 120 BPM, 4 beats/cycle,
            # 8 events/cycle, each event is 250 ms → 1 cell = 2000 ms.
            payload = make_export([
                [make_capture_cell(['a'], [0, 1, 2, 3, 4, 5, 6, 7])],
                [make_capture_cell(['a'], [0, 1, 2, 3, 4, 5, 6, 7]),
                 make_capture_cell(['a'], [0, 1, 2, 3, 4, 5, 6, 7])],
            ])
            wd.write_export(payload)

            render.OUTPUT_DIR = wd.root / 'out'
            render.RENDER_DIR = wd.root / 'render'
            zip_path = render.render(wd.export_path, 'TORSOLEN', seed=42)

            extract = wd.root / 'extract'
            extract.mkdir()
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract)
            wavs = sorted((extract / 'TORSOLEN').glob('*.wav'))
            self.assertEqual(len(wavs), 2)

            durations = []
            for w in wavs:
                with wave.open(str(w), 'rb') as r:
                    durations.append(r.getnframes() / r.getframerate())

            # Order in zip is unsorted by row, so check the *set* of
            # rounded durations covers a 2 s row and a 4 s row.
            rounded = sorted(round(d, 1) for d in durations)
            self.assertEqual(rounded, [2.0, 4.0])

    def test_row_length_is_exact_at_fractional_event_ms(self):
        # 128 BPM × 8 events/cycle = 234.375 ms/event. Pre-fix this
        # truncated to 234, so 4 cells × 8 events = 7488 ms instead of
        # the true 7500 ms. Cumulative-rounding event boundaries fix
        # the drift.
        render = load_render_module('torso-s4')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['a'], bpm=128, steps=32)
            stub_sample_fetch(render, paths)
            payload = make_export([
                [make_capture_cell(['a'], [0, 1, 2, 3, 4, 5, 6, 7])
                 for _ in range(4)],
            ], bpm=128)
            wd.write_export(payload)

            render.OUTPUT_DIR = wd.root / 'out'
            render.RENDER_DIR = wd.root / 'render'
            zip_path = render.render(wd.export_path, 'TORSODRIFT', seed=1)

            extract = wd.root / 'extract'
            extract.mkdir()
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract)
            wav = next((extract / 'TORSODRIFT').glob('*.wav'))
            with wave.open(str(wav), 'rb') as r:
                duration_ms = r.getnframes() * 1000 / r.getframerate()
            # 4 cells × 8 events × 234.375 ms = 7500 ms, exact.
            # Allow ±1 ms slack for the final-event-rounding tail.
            self.assertAlmostEqual(duration_ms, 7500, delta=1)

    def test_output_wavs_are_at_s4_sample_rate(self):
        # Source breaks come from a mixed-rate gist; the renderer must
        # ship at the S-4 ceiling (96 kHz) per TORSO-S4.md.
        render = load_render_module('torso-s4')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['a'], bpm=120, steps=32)
            stub_sample_fetch(render, paths)
            payload = make_export([
                [make_capture_cell(['a'], [0, 1, 2, 3, 4, 5, 6, 7])],
            ])
            wd.write_export(payload)

            render.OUTPUT_DIR = wd.root / 'out'
            render.RENDER_DIR = wd.root / 'render'
            zip_path = render.render(wd.export_path, 'TORSORATE', seed=1)

            extract = wd.root / 'extract'
            extract.mkdir()
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract)
            wav = next((extract / 'TORSORATE').glob('*.wav'))
            with wave.open(str(wav), 'rb') as r:
                self.assertEqual(r.getframerate(), 96000)

    def test_filenames_are_deterministic_under_same_seed(self):
        render = load_render_module('torso-s4')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['a'], bpm=120, steps=32)
            stub_sample_fetch(render, paths)
            payload = make_export([
                [make_capture_cell(['a'], [0, 1, 2, 3, 4, 5, 6, 7])],
            ])
            wd.write_export(payload)

            render.OUTPUT_DIR = wd.root / 'out'
            render.RENDER_DIR = wd.root / 'render'

            zip_a = render.render(wd.export_path, 'DETONE', seed=7)
            with zipfile.ZipFile(zip_a, 'r') as zf:
                names_a = sorted(zf.namelist())

            zip_b = render.render(wd.export_path, 'DETONE', seed=7)
            with zipfile.ZipFile(zip_b, 'r') as zf:
                names_b = sorted(zf.namelist())

            self.assertEqual(names_a, names_b)


if __name__ == '__main__':
    unittest.main()

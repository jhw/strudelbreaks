"""Tests for the ot-doom (megabreak of doom) renderer — cell-input variant.

The renderer turns each tempera row into one OT bank/pattern, treating
the cells of the row as the doom inputs. Each cell renders to a bar of
audio in Python; chain[k] = segment_k(input_0) ++ … ++ segment_k(input_{N-1}).
N flex slots, N trigs at intervals of 16/N; scenes lock track 1's
slice_index to 0 / N-1; no per-trig slice_index p-lock. See
docs/planning/ot-doom.md for the full design.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
import wave

from octapy import Project

from ._fixtures import (
    EXPORT_ROOT,
    WorkDir,
    load_render_module,
    make_break_wavs,
    make_capture_cell,
    make_export,
)


def _load_audio_module():
    """Load `scripts/export/ot-doom/audio.py` as a fresh module so tests
    don't share monkey-patch state with the renderer's own import."""
    target_dir = EXPORT_ROOT / 'ot-doom'
    audio_path = target_dir / 'audio.py'
    for path in (str(EXPORT_ROOT), str(target_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)
    mod_name = '_test_audio_ot_doom'
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, audio_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OtDoomAudioHelpersTest(unittest.TestCase):
    def setUp(self):
        self.audio = _load_audio_module()

    def test_load_break_resamples_to_ot_native(self):
        # Source wav at 48 kHz; load_break must hand back a 44.1 kHz
        # AudioSegment. See OCTATRACK.md for why this matters.
        with WorkDir() as wd:
            wav = wd.samples / 'src48k.wav'
            with wave.open(str(wav), 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes(b'\x00\x00' * 48000)  # 1 s of silence
            seg = self.audio.load_break(wav)
            self.assertEqual(seg.frame_rate, self.audio.OT_SAMPLE_RATE)
            self.assertEqual(seg.frame_rate, 44100)

    def test_render_cell_audio_polymetric_stretch(self):
        # Build a synthetic cache: two breaks, 16 distinct slices each.
        # The render walks break_names polymetrically (i*M//N) and plays
        # the captured slice index per event.
        from pydub import AudioSegment

        slice_ms = 100
        rate = 44100

        def make_slices():
            return [
                AudioSegment.silent(duration=slice_ms, frame_rate=rate)
                for _ in range(16)
            ]

        cache = {'a': make_slices(), 'b': make_slices()}
        cell = {
            'break': ['a', 'b', 'a', 'b'],
            'pattern': [0, 1, 2, 3, 4, 5, 6, 7],
        }
        bar = self.audio.render_cell_audio(cell, cache, events_per_cycle=8)
        # 8 events × slice_ms = 1 bar of synthetic audio.
        self.assertEqual(len(bar), 8 * slice_ms)
        self.assertEqual(bar.frame_rate, rate)

    def test_build_matrix_chain_length_equals_one_bar(self):
        from pydub import AudioSegment

        rate = 44100
        bar_ms = 1600
        # 4 inputs, each 1600 ms long. The chain (segment k from each
        # input) has length n × (bar_ms / n) = bar_ms = 1 bar.
        inputs = [AudioSegment.silent(duration=bar_ms, frame_rate=rate) for _ in range(4)]
        chain0 = self.audio.build_matrix_chain(inputs, k=0, n=4)
        self.assertEqual(len(chain0), bar_ms)
        self.assertEqual(chain0.frame_rate, rate)


class OtDoomCellCountValidationTest(unittest.TestCase):
    """|C| (cells per row) must be in {4, 8, 16}."""

    def _build_render(self, n_cells, wd):
        render = load_render_module('ot-doom')
        paths = make_break_wavs(wd.samples, ['a', 'b'], bpm=120)
        wd.stub_sources(paths)
        cells = [
            make_capture_cell(['a', 'b', 'a', 'b'],
                              [0, 1, 2, 3, 4, 5, 6, 7])
            for _ in range(n_cells)
        ]
        payload = make_export([cells])
        wd.write_export(payload)
        render.OUTPUT_DIR = wd.root / 'out'
        render.RENDER_DIR = wd.root / 'render'
        return render

    def _assert_rejects(self, n_cells):
        with WorkDir() as wd:
            render = self._build_render(n_cells, wd)
            with self.assertRaises(SystemExit) as ctx:
                render.render(wd.export_path, 'CCHECK')
            self.assertIn(f'|C|={n_cells}', str(ctx.exception))

    def test_c_1_is_rejected(self):
        self._assert_rejects(1)

    def test_c_2_is_rejected(self):
        self._assert_rejects(2)

    def test_c_3_is_rejected(self):
        self._assert_rejects(3)

    def test_c_5_is_rejected(self):
        self._assert_rejects(5)

    def test_c_4_is_accepted(self):
        with WorkDir() as wd:
            render = self._build_render(4, wd)
            zip_path = render.render(wd.export_path, 'CCHECK')
            self.assertTrue(zip_path.exists())

    def test_c_8_is_accepted(self):
        with WorkDir() as wd:
            render = self._build_render(8, wd)
            zip_path = render.render(wd.export_path, 'CCHECK')
            self.assertTrue(zip_path.exists())


class OtDoomRoundtripTest(unittest.TestCase):
    """End-to-end smoke test against the new cell-input architecture."""

    def test_4_cell_row_produces_expected_layout(self):
        render = load_render_module('ot-doom')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['kk', 'sn'], bpm=120, steps=32)
            wd.stub_sources(paths)

            # One row with 4 cells — varied patterns so the input renders
            # are non-identical.
            cells = [
                make_capture_cell(['kk', 'sn', 'kk', 'sn'], [0, 1, 2, 3, 4, 5, 6, 7]),
                make_capture_cell(['kk', 'sn', 'kk', 'sn'], [1, 2, 3, 4, 5, 6, 7, 8]),
                make_capture_cell(['kk', 'sn', 'kk', 'sn'], [2, 3, 4, 5, 6, 7, 8, 9]),
                make_capture_cell(['kk', 'sn', 'kk', 'sn'], [3, 4, 5, 6, 7, 8, 9, 10]),
            ]
            payload = make_export([cells])
            wd.write_export(payload)

            render.OUTPUT_DIR = wd.root / 'out'
            render.RENDER_DIR = wd.root / 'render'
            zip_path = render.render(wd.export_path, 'OTDOOMRT')
            self.assertTrue(zip_path.exists())

            # Chain wavs land in the per-row render dir at 44.1 kHz —
            # the resample-on-load contract from OCTATRACK.md.
            chain_wavs = sorted((wd.root / 'render' / 'OTDOOMRT' / 'bank01').glob('*.wav'))
            self.assertEqual(len(chain_wavs), 4)  # |C| = 4 chains
            for wav_path in chain_wavs:
                with wave.open(str(wav_path), 'rb') as w:
                    self.assertEqual(w.getframerate(), 44100)

            project = Project.from_zip(zip_path)

            # Each chain → one flex slot with |C|=4 slice markers.
            for k in range(4):
                slot = project.get_slot(f'b01_chain{k:02d}.wav')
                self.assertIsNotNone(slot, f'missing chain {k} slot')
                sm = project.markers.get_slot(slot, is_static=False)
                self.assertEqual(sm.slice_count, 4)
            self.assertIsNone(project.get_slot('b01_chain04.wav'))

            bank = project.bank(1)
            part = bank.part(1)
            t1 = part.audio_track(1)

            # Track 1: flex + slice mode ON.
            self.assertEqual(int(t1.setup.slice), 1)

            # Scenes drive the input axis via slice_index (octapy 0.1.23
            # API). No raw playback_param2 STRT manipulation.
            self.assertEqual(part.scene(1).track(1).slice_index, 0)
            self.assertEqual(part.scene(2).track(1).slice_index, 3)
            self.assertEqual(part.active_scene_a, 0)
            self.assertEqual(part.active_scene_b, 1)

            # Pattern: 16-step grid, N=4 trigs at intervals 16/N = 4 →
            # steps 1, 5, 9, 13. Each trig sample-locked to its chain.
            # No per-trig slice_index lock — that would override the
            # scene's slice_index drive.
            pattern = bank.pattern(1)
            self.assertEqual(pattern.scale_length, 16)
            track = pattern.audio_track(1)
            self.assertEqual(track.active_steps, [1, 5, 9, 13])

            chain_slots = [
                project.get_slot(f'b01_chain{k:02d}.wav') for k in range(4)
            ]
            for k, step_num in enumerate([1, 5, 9, 13]):
                step = track.step(step_num)
                self.assertEqual(step.sample_lock, chain_slots[k])
                self.assertIsNone(step.slice_index)

    def test_two_rows_get_independent_banks(self):
        render = load_render_module('ot-doom')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['a', 'b'], bpm=120, steps=32)
            wd.stub_sources(paths)

            def cells(offset):
                return [
                    make_capture_cell(
                        ['a', 'b', 'a', 'b'],
                        [offset + j for j in range(8)],
                    )
                    for offset in range(offset, offset + 4)
                ]

            payload = make_export([cells(0), cells(4)])
            wd.write_export(payload)

            render.OUTPUT_DIR = wd.root / 'out'
            render.RENDER_DIR = wd.root / 'render'
            zip_path = render.render(wd.export_path, 'OTDOOMRT2')
            project = Project.from_zip(zip_path)

            for bn in (1, 2):
                bank = project.bank(bn)
                pattern = bank.pattern(1)
                self.assertEqual(pattern.audio_track(1).active_steps, [1, 5, 9, 13])
                self.assertEqual(bank.part(1).scene(1).track(1).slice_index, 0)
                self.assertEqual(bank.part(1).scene(2).track(1).slice_index, 3)


if __name__ == '__main__':
    unittest.main()

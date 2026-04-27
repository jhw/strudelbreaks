"""Tests for the ot-doom (megabreak of doom) renderer.

Each tempera row → one OT pattern. Patterns pack 16 per bank: rows
1..16 → bank 1 patterns 1..16, rows 17..32 → bank 2 patterns 1..16,
etc. All patterns in a bank share part 1's per-track scene config,
so all rows in a bank must share the same `|C|`.

Two stem modes:

* split_stems=True (default): per pattern each cell renders to a bar
  of audio per drum stem (kick, snare, hat); chain[k] = stack across
  stems of (segment_k(input_0) ++ … ++ segment_k(input_{N-1})). N
  flex slots per pattern, each holding 3 * N slices. N trigs on each
  of T1/T2/T3 at intervals of 16/N. Per-track scenes on part 1
  address each stem's N-slice range.

* split_stems=False: one mixed render per cell; chain[k] holds N
  segments only. T1 alone trigs and the part has scenes on T1 only.

See docs/export/ot-doom.md for the full design.
"""
from __future__ import annotations

import unittest
import wave

from octapy import FX1Type, FX2Type, Project

from ._fixtures import (
    WorkDir,
    load_render_module,
    make_break_wavs,
    make_capture_cell,
    make_export,
    make_per_track_break_wavs,
)


TRACKS = ('kick', 'snare', 'hat')


def _load_audio_module():
    """Import the ot-doom audio module."""
    from app.export.octatrack.ot_doom import audio
    return audio


def _make_cells(n_cells):
    return [
        make_capture_cell(['a', 'b', 'a', 'b'],
                          [0, 1, 2, 3, 4, 5, 6, 7])
        for _ in range(n_cells)
    ]


class OtDoomAudioHelpersTest(unittest.TestCase):
    def setUp(self):
        self.audio = _load_audio_module()

    def test_load_break_resamples_to_ot_native(self):
        # Source wav at 48 kHz; load_break must hand back a 44.1 kHz
        # AudioSegment. See docs/export/octatrack.md for why this matters.
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
        # the captured slice index per event. render_cell_audio is
        # per-stem in the new design — caller invokes it per drum track
        # with that track's source-slice cache.
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

    def test_build_matrix_chain_packs_three_stems(self):
        # Per-track packing: kick, snare, hat each contribute n slices
        # to one packed chain. For n=4 inputs across 3 stems, total
        # slices per packed chain = 3 * 4 = 12. Each input is 1600 ms,
        # seg_ms = 400, packed length = 12 * 400 = 4800 ms.
        from pydub import AudioSegment

        rate = 44100
        bar_ms = 1600
        n = 4
        per_track_inputs = {
            t: [AudioSegment.silent(duration=bar_ms, frame_rate=rate)
                for _ in range(n)]
            for t in TRACKS
        }
        chain0 = self.audio.build_matrix_chain(
            per_track_inputs, list(TRACKS), k=0, n=n,
        )
        # 3 stems × n segments × bar_ms / n = packed length.
        expected_ms = len(TRACKS) * n * (bar_ms // n)
        self.assertEqual(len(chain0), expected_ms)
        self.assertEqual(chain0.frame_rate, rate)

    def test_build_matrix_chain_stem_ordering(self):
        # The packed chain orders stems as TRACKS = (kick, snare, hat).
        # Each stem block is `n` slices long, and block boundaries
        # land at multiples of seg_ms. Use distinct sine tones per stem
        # so we can verify the bytes at each block start match the
        # right stem's first input segment.
        from pydub.generators import Sine

        rate = 44100
        bar_ms = 400
        n = 4
        seg_ms = bar_ms // n
        per_track_inputs = {}
        for j, track in enumerate(TRACKS):
            per_track_inputs[track] = [
                Sine(220 * (j + 1) * (i + 1), sample_rate=rate)
                .to_audio_segment(duration=bar_ms)
                for i in range(n)
            ]
        chain0 = self.audio.build_matrix_chain(
            per_track_inputs, list(TRACKS), k=0, n=n,
        )
        block = n * seg_ms
        # The first slice of each block should be input_0.segment_0
        # for that track.
        for j, track in enumerate(TRACKS):
            packed_first = chain0[j * block:j * block + seg_ms]
            track_first = per_track_inputs[track][0][:seg_ms]
            self.assertEqual(
                packed_first.raw_data, track_first.raw_data,
                f'block {j} ({track}) does not start with that stem',
            )

    def test_build_matrix_chain_mixed_mode_single_stem(self):
        # Mixed mode (split_stems=False) renders one combined sample
        # per cell; the chain is just N segments (no per-stem stacking).
        from pydub import AudioSegment

        rate = 44100
        bar_ms = 1600
        n = 4
        per_stem_inputs = {
            'mixed': [AudioSegment.silent(duration=bar_ms, frame_rate=rate)
                      for _ in range(n)],
        }
        chain0 = self.audio.build_matrix_chain(
            per_stem_inputs, ['mixed'], k=0, n=n,
        )
        self.assertEqual(len(chain0), n * (bar_ms // n))
        self.assertEqual(chain0.frame_rate, rate)


class OtDoomCellCountValidationTest(unittest.TestCase):
    """|C| (cells per row) must be in {4, 8, 16}."""

    def _build_render(self, n_cells, wd):
        render = load_render_module('octatrack/ot-doom')
        paths = make_per_track_break_wavs(
            wd.samples, ['a', 'b'], tracks=TRACKS, bpm=120,
        )
        wd.stub_sources(paths)
        payload = make_export([_make_cells(n_cells)])
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


class OtDoomBankPackingTest(unittest.TestCase):
    """Multi-row exports pack 16 patterns per bank, rolling over to
    bank 2 on the 17th row. Within a bank every row must share |C|."""

    def _setup_render(self, wd, rows):
        render = load_render_module('octatrack/ot-doom')
        paths = make_per_track_break_wavs(
            wd.samples, ['a', 'b'], tracks=TRACKS, bpm=120, steps=32,
        )
        wd.stub_sources(paths)
        payload = make_export(rows)
        wd.write_export(payload)
        render.OUTPUT_DIR = wd.root / 'out'
        render.RENDER_DIR = wd.root / 'render'
        return render

    def test_two_rows_share_one_bank(self):
        # Two |C|=4 rows pack into bank 1 patterns 1 and 2.
        with WorkDir() as wd:
            render = self._setup_render(
                wd, [_make_cells(4), _make_cells(4)],
            )
            zip_path = render.render(wd.export_path, 'PACKTWO')
            project = Project.from_zip(zip_path)

            for pat_num in (1, 2):
                pattern = project.bank(1).pattern(pat_num)
                # Same trigs on T1, T2, T3.
                for track_idx in range(3):
                    self.assertEqual(
                        pattern.audio_track(track_idx + 1).active_steps,
                        [1, 5, 9, 13],
                        f'pattern {pat_num} track {track_idx + 1} wrong',
                    )

            # Each row writes its own packed chains under bank01/.
            chain_wavs = sorted((wd.root / 'render' / 'PACKTWO' / 'bank01').glob('*.wav'))
            self.assertEqual(len(chain_wavs), 8)  # 2 rows × |C|=4 chains

    def test_seventeenth_row_spills_to_bank_two(self):
        # 17 |C|=4 rows: rows 1..16 → bank 1 patterns 1..16, row 17 →
        # bank 2 pattern 1. Total chain count = 17 × 4 = 68, well
        # under the 128 flex-slot ceiling.
        with WorkDir() as wd:
            render = self._setup_render(wd, [_make_cells(4)] * 17)
            zip_path = render.render(wd.export_path, 'SPILL')
            project = Project.from_zip(zip_path)

            for pat_num in range(1, 17):
                self.assertEqual(
                    project.bank(1).pattern(pat_num).audio_track(1).active_steps,
                    [1, 5, 9, 13],
                )
            self.assertEqual(
                project.bank(2).pattern(1).audio_track(1).active_steps,
                [1, 5, 9, 13],
            )

            self.assertEqual(
                len(list((wd.root / 'render' / 'SPILL' / 'bank01').glob('*.wav'))),
                64,
            )
            self.assertEqual(
                len(list((wd.root / 'render' / 'SPILL' / 'bank02').glob('*.wav'))),
                4,
            )

    def test_mixed_c_within_bank_is_rejected(self):
        with WorkDir() as wd:
            render = self._setup_render(
                wd, [_make_cells(4), _make_cells(8)],
            )
            with self.assertRaises(SystemExit) as ctx:
                render.render(wd.export_path, 'MIXED')
            msg = str(ctx.exception)
            self.assertIn('mixed |C|', msg)
            self.assertIn('bank 1', msg)

    def test_flex_slot_ceiling(self):
        # 16 |C|=8 rows = 128 chains (at the ceiling, accepted);
        # 17 rows = 136 (rejected). We test the over case here.
        with WorkDir() as wd:
            render = self._setup_render(wd, [_make_cells(8)] * 17)
            with self.assertRaises(SystemExit) as ctx:
                render.render(wd.export_path, 'OVERFLOW')
            self.assertIn('flex slot limit exceeded', str(ctx.exception))


class OtDoomRoundtripTest(unittest.TestCase):
    """End-to-end smoke test: one row, |C|=4, full project layout
    including per-track scenes and FX."""

    def test_4_cell_row_produces_per_track_layout(self):
        render = load_render_module('octatrack/ot-doom')
        with WorkDir() as wd:
            paths = make_per_track_break_wavs(
                wd.samples, ['kk', 'sn'], tracks=TRACKS,
                bpm=120, steps=32,
            )
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

            # Chain wavs land in the per-bank render dir at 44.1 kHz.
            chain_wavs = sorted((wd.root / 'render' / 'OTDOOMRT' / 'bank01').glob('*.wav'))
            self.assertEqual(len(chain_wavs), 4)  # |C|=4 packed chains
            for wav_path in chain_wavs:
                with wave.open(str(wav_path), 'rb') as w:
                    self.assertEqual(w.getframerate(), 44100)

            project = Project.from_zip(zip_path)

            # Each packed chain → one flex slot with 3*|C| = 12 slice
            # markers (kick block 0..3, snare 4..7, hat 8..11).
            n = 4
            expected_slice_count = len(TRACKS) * n
            for k in range(n):
                slot = project.get_slot(f'b01_p01_chain{k:02d}.wav')
                self.assertIsNotNone(slot, f'missing chain {k} slot')
                sm = project.markers.get_slot(slot, is_static=False)
                self.assertEqual(sm.slice_count, expected_slice_count)
            self.assertIsNone(project.get_slot(f'b01_p01_chain{n:02d}.wav'))

            bank = project.bank(1)
            part = bank.part(1)

            # T1, T2, T3 each: flex + slice mode ON, plus DJ_EQ + COMPRESSOR.
            for track_idx in range(3):
                t = part.audio_track(track_idx + 1)
                self.assertEqual(int(t.setup.slice), 1)
                self.assertEqual(t.fx1_type, FX1Type.DJ_EQ)
                self.assertEqual(t.fx2_type, FX2Type.COMPRESSOR)

            # Per-track scenes — each stem block is n = 4 slices wide.
            #   T1 (kick):  scene A=0,  scene B=3
            #   T2 (snare): scene A=4,  scene B=7
            #   T3 (hat):   scene A=8,  scene B=11
            for track_idx in range(3):
                self.assertEqual(
                    part.scene(1).track(track_idx + 1).slice_index,
                    track_idx * n,
                )
                self.assertEqual(
                    part.scene(2).track(track_idx + 1).slice_index,
                    track_idx * n + (n - 1),
                )
            self.assertEqual(part.active_scene_a, 0)
            self.assertEqual(part.active_scene_b, 1)

            # T8: CHORUS (FX1) at mix=64 and DELAY (FX2) at send=64.
            # Different param names because the two FX expose
            # different wet/dry parameters in octapy.
            t8 = part.audio_track(8)
            self.assertEqual(t8.fx1_type, FX1Type.CHORUS)
            self.assertEqual(t8.fx1.mix, 64)
            self.assertEqual(t8.fx2_type, FX2Type.DELAY)
            self.assertEqual(t8.fx2.send, 64)

            # Pattern: 16-step grid, N=4 trigs at intervals 16/N = 4 →
            # steps 1, 5, 9, 13. Same on T1/T2/T3, all sample-locked
            # to the same packed slot per chain. No per-trig
            # slice_index lock — that would override the per-track
            # scene drives.
            pattern = bank.pattern(1)
            self.assertEqual(pattern.scale_length, 16)
            chain_slots = [
                project.get_slot(f'b01_p01_chain{k:02d}.wav') for k in range(n)
            ]
            for track_idx in range(3):
                track = pattern.audio_track(track_idx + 1)
                self.assertEqual(track.active_steps, [1, 5, 9, 13])
                for k, step_num in enumerate([1, 5, 9, 13]):
                    step = track.step(step_num)
                    self.assertEqual(step.sample_lock, chain_slots[k])
                    self.assertIsNone(step.slice_index)


class OtDoomMixedModeTest(unittest.TestCase):
    """split_stems=False renders one combined sample per cell and
    routes everything to T1; scenes A/B sweep slice_index 0 ↔ N-1.
    Used for an A/B fidelity check against the Strudel source."""

    def test_mixed_mode_single_track_layout(self):
        render = load_render_module('octatrack/ot-doom')
        with WorkDir() as wd:
            # Mixed mode pulls flat (name → path) source paths.
            paths = make_break_wavs(wd.samples, ['kk', 'sn'],
                                    bpm=120, steps=32)
            wd.stub_sources(paths)

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
            zip_path = render.render(wd.export_path, 'OTDOOMMIX',
                                     split_stems=False)
            self.assertTrue(zip_path.exists())

            project = Project.from_zip(zip_path)
            n = 4

            # Each chain is just N slices — no per-stem stacking.
            for k in range(n):
                slot = project.get_slot(f'b01_p01_chain{k:02d}.wav')
                self.assertIsNotNone(slot)
                sm = project.markers.get_slot(slot, is_static=False)
                self.assertEqual(sm.slice_count, n)

            part = project.bank(1).part(1)
            # T1 only is configured (slice mode + FX). T2/T3 untouched.
            self.assertEqual(int(part.audio_track(1).setup.slice), 1)
            self.assertEqual(part.audio_track(1).fx1_type, FX1Type.DJ_EQ)
            self.assertEqual(part.audio_track(1).fx2_type, FX2Type.COMPRESSOR)

            # T1 scenes sweep slice 0 ↔ N-1 in the single-stem chain.
            self.assertEqual(part.scene(1).track(1).slice_index, 0)
            self.assertEqual(part.scene(2).track(1).slice_index, n - 1)

            # T8 send chain still configured.
            t8 = part.audio_track(8)
            self.assertEqual(t8.fx1_type, FX1Type.CHORUS)
            self.assertEqual(t8.fx2_type, FX2Type.DELAY)

            # Pattern: trigs on T1 only.
            pattern = project.bank(1).pattern(1)
            self.assertEqual(pattern.audio_track(1).active_steps, [1, 5, 9, 13])
            self.assertEqual(pattern.audio_track(2).active_steps, [])
            self.assertEqual(pattern.audio_track(3).active_steps, [])


if __name__ == '__main__':
    unittest.main()

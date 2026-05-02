"""Tests for the ot-basic renderer (per-cell patterns target, per-track stems)."""
from __future__ import annotations

import unittest

from octapy import FX1Type, FX2Type, Project, TrigCondition

from ._fixtures import (
    WorkDir,
    load_render_module,
    make_break_wavs,
    make_capture_cell,
    make_export,
    make_per_track_break_wavs,
)


class OctatrackHelpersTest(unittest.TestCase):
    def setUp(self):
        self.render = load_render_module('octatrack/ot-basic')

    def test_expand_cell_polymetric_stretch(self):
        # `{a b c d}%8` polymetric stretch: i*M//N where M=4, N=8 →
        # a a b b c c d d
        events = self.render.expand_cell(
            ['a', 'b', 'c', 'd'],
            [0, 1, 2, 3, 4, 5, 6, 7],
            events_per_cycle=8,
        )
        self.assertEqual(
            [name for name, _ in events],
            ['a', 'a', 'b', 'b', 'c', 'c', 'd', 'd'],
        )
        self.assertEqual([s for _, s in events], [0, 1, 2, 3, 4, 5, 6, 7])

    def test_expand_cell_handles_rest(self):
        events = self.render.expand_cell(
            ['a'], [0, None, 2, 3, 4, 5, 6, 7], events_per_cycle=8,
        )
        self.assertEqual(events[1], ('a', None))

    def test_collect_break_names_dedups_first_seen(self):
        banks = [[
            make_capture_cell(['a', 'b'], [0, 1, 2, 3, 4, 5, 6, 7]),
            make_capture_cell(['b', 'c'], [0, 1, 2, 3, 4, 5, 6, 7]),
        ]]
        names = self.render.collect_break_names(banks)
        self.assertEqual(names, ['a', 'b', 'c'])


class OctatrackProbabilitySnapTest(unittest.TestCase):
    def setUp(self):
        self.render = load_render_module('octatrack/ot-basic')

    def test_one_returns_none(self):
        # 1.0 means "always fires" — no condition set.
        self.assertIsNone(self.render.probability_to_condition(1.0))

    def test_exact_bucket_match(self):
        self.assertEqual(self.render.probability_to_condition(0.5),
                         TrigCondition.PERCENT_50)
        self.assertEqual(self.render.probability_to_condition(0.25),
                         TrigCondition.PERCENT_25)

    def test_snap_to_nearest(self):
        # 0.30 is closer to 33% (distance 3) than 25% (distance 5).
        self.assertEqual(self.render.probability_to_condition(0.30),
                         TrigCondition.PERCENT_33)
        # 0.05 → 4% (distance 1) vs 6% (distance 1) — tie broken by
        # min() to the first match in iteration order = PERCENT_4.
        self.assertEqual(self.render.probability_to_condition(0.05),
                         TrigCondition.PERCENT_4)

    def test_zero_snaps_to_lowest_bucket(self):
        # OT can't express 0%; the smallest bucket is 1%.
        self.assertEqual(self.render.probability_to_condition(0.0),
                         TrigCondition.PERCENT_1)

    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            self.render.probability_to_condition(-0.01)
        with self.assertRaises(ValueError):
            self.render.probability_to_condition(1.01)


class OctatrackRoundtripTest(unittest.TestCase):
    EXPECTED_ACTIVE = [1, 3, 5, 9, 11, 13, 15]
    TRACKS = ('kick', 'snare', 'hat')

    def _render(self, probability=1.0):
        render = load_render_module('octatrack/ot-basic')
        wd = WorkDir().__enter__()
        try:
            paths = make_per_track_break_wavs(
                wd.samples, ['kk', 'sn'], tracks=self.TRACKS,
                bpm=120, steps=32,
            )
            wd.stub_sources(paths)
            payload = make_export([[
                make_capture_cell(['kk', 'sn', 'kk', 'sn'],
                                  [0, 4, 8, None, 1, 5, 9, 13]),
            ]])
            wd.write_export(payload)
            render.OUTPUT_DIR = wd.root / 'out'
            zip_path = render.render(wd.export_path, 'OTROUNDTRIP',
                                     probability=probability)
            return zip_path, wd
        except Exception:
            wd.__exit__(None, None, None)
            raise

    def test_render_produces_per_track_slots_and_trigs(self):
        zip_path, wd = self._render(probability=1.0)
        try:
            self.assertTrue(zip_path.exists())

            project = Project.from_zip(zip_path)

            # Each break gets one flex slot per drum stem (kick, snare,
            # hat) — so 2 breaks × 3 stems = 6 flex slots total, each
            # with the canonical 16 slice markers.
            slots = {}
            for name in ('kk', 'sn'):
                for track in self.TRACKS:
                    slot = project.get_slot(f'{name}__{track}.wav', slot_type='FLEX')
                    self.assertIsNotNone(slot, f'missing {name}/{track} slot')
                    sm = project.markers.get_slot(slot, is_static=False)
                    self.assertEqual(sm.slice_count, 16)
                    slots[(name, track)] = slot

            pattern = project.bank(1).pattern(1)
            self.assertEqual(pattern.scale_length, 16)

            # Same trig pattern on T1 (kick), T2 (snare), T3 (hat).
            # Each track sample-locks to its own stem's slot, slice_index
            # is identical across the three tracks at any given step.
            for track_idx, track in enumerate(self.TRACKS):
                track_obj = pattern.audio_track(track_idx + 1)
                self.assertEqual(
                    track_obj.active_steps, self.EXPECTED_ACTIVE,
                    f'track {track} active steps wrong',
                )

                # First event maps to break 'kk', this stem.
                self.assertEqual(track_obj.step(1).sample_lock,
                                 slots[('kk', track)])
                self.assertEqual(track_obj.step(1).slice_index, 0)
                # Polymetric stretch: events 4,5 map to break index 2 = 'kk'.
                self.assertEqual(track_obj.step(9).sample_lock,
                                 slots[('kk', track)])
                self.assertEqual(track_obj.step(9).slice_index, 1)
        finally:
            wd.__exit__(None, None, None)

    def test_part_fx_layout(self):
        zip_path, wd = self._render(probability=1.0)
        try:
            project = Project.from_zip(zip_path)
            part = project.bank(1).part(1)

            # T1, T2, T3: DJ_EQ on FX1, COMPRESSOR on FX2.
            for track_idx in range(3):
                t = part.audio_track(track_idx + 1)
                self.assertEqual(t.fx1_type, FX1Type.DJ_EQ)
                self.assertEqual(t.fx2_type, FX2Type.COMPRESSOR)

            # T8: CHORUS (FX1) at mix=64 and DELAY (FX2) at send=64.
            # Different param names because the two FX expose
            # different wet/dry parameters in octapy.
            t8 = part.audio_track(8)
            self.assertEqual(t8.fx1_type, FX1Type.CHORUS)
            self.assertEqual(t8.fx1.mix, 64)
            self.assertEqual(t8.fx2_type, FX2Type.DELAY)
            self.assertEqual(t8.fx2.send, 64)
        finally:
            wd.__exit__(None, None, None)

    def test_default_probability_leaves_condition_unset(self):
        zip_path, wd = self._render(probability=1.0)
        try:
            project = Project.from_zip(zip_path)
            for track_idx in range(3):
                track = project.bank(1).pattern(1).audio_track(track_idx + 1)
                for s in self.EXPECTED_ACTIVE:
                    cond = track.step(s).condition
                    # Default OT trig condition value (no override) is NONE.
                    self.assertIn(cond, (None, TrigCondition.NONE))
        finally:
            wd.__exit__(None, None, None)

    def test_custom_probability_locks_every_trig_on_all_tracks(self):
        zip_path, wd = self._render(probability=0.5)
        try:
            project = Project.from_zip(zip_path)
            for track_idx in range(3):
                track = project.bank(1).pattern(1).audio_track(track_idx + 1)
                for s in self.EXPECTED_ACTIVE:
                    self.assertEqual(track.step(s).condition,
                                     TrigCondition.PERCENT_50)
        finally:
            wd.__exit__(None, None, None)

    def test_mixed_mode_single_track_layout(self):
        # split_stems=False: one mixed sample per break, T1 only.
        # Used to A/B audio fidelity against the Strudel source.
        render = load_render_module('octatrack/ot-basic')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['kk', 'sn'],
                                    bpm=120, steps=32)
            wd.stub_sources(paths)
            payload = make_export([[
                make_capture_cell(['kk', 'sn', 'kk', 'sn'],
                                  [0, 4, 8, None, 1, 5, 9, 13]),
            ]])
            wd.write_export(payload)
            render.OUTPUT_DIR = wd.root / 'out'
            zip_path = render.render(wd.export_path, 'OTBMIX',
                                     split_stems=False)
            self.assertTrue(zip_path.exists())

            project = Project.from_zip(zip_path)

            # Two breaks → two flex slots (no per-stem multiplier).
            for name in ('kk', 'sn'):
                slot = project.get_slot(f'{name}.wav', slot_type='FLEX')
                self.assertIsNotNone(slot, f'missing {name} slot')

            # T1 only is configured / has trigs.
            pattern = project.bank(1).pattern(1)
            self.assertEqual(pattern.audio_track(1).active_steps,
                             self.EXPECTED_ACTIVE)
            self.assertEqual(pattern.audio_track(2).active_steps, [])
            self.assertEqual(pattern.audio_track(3).active_steps, [])

            part = project.bank(1).part(1)
            self.assertEqual(int(part.audio_track(1).setup.slice), 1)
            self.assertEqual(part.audio_track(1).fx1_type, FX1Type.DJ_EQ)
            self.assertEqual(part.audio_track(1).fx2_type, FX2Type.COMPRESSOR)


    def test_invalid_probability_raises(self):
        render = load_render_module('octatrack/ot-basic')
        with WorkDir() as wd:
            paths = make_per_track_break_wavs(
                wd.samples, ['kk'], tracks=self.TRACKS, bpm=120, steps=32,
            )
            wd.stub_sources(paths)
            payload = make_export([[
                make_capture_cell(['kk'], [0, 1, 2, 3, 4, 5, 6, 7]),
            ]])
            wd.write_export(payload)
            render.OUTPUT_DIR = wd.root / 'out'
            with self.assertRaises(ValueError):
                render.render(wd.export_path, 'OOR', probability=1.5)


class OctatrackNeighbourTest(unittest.TestCase):
    """neighbour=True: flex tracks shift to T1/T3/T5; T2/T4/T6 are
    neighbour machines with FILTER + DELAY; T8 drops the delay and
    keeps just the spatializer."""

    TRACKS = ('kick', 'snare', 'hat')

    def _render_neighbour(self, *, split_stems=True):
        render = load_render_module('octatrack/ot-basic')
        wd = WorkDir().__enter__()
        try:
            if split_stems:
                paths = make_per_track_break_wavs(
                    wd.samples, ['kk'], tracks=self.TRACKS,
                    bpm=120, steps=32,
                )
            else:
                paths = make_break_wavs(wd.samples, ['kk'], bpm=120, steps=32)
            wd.stub_sources(paths)
            payload = make_export([[
                make_capture_cell(['kk'], [0, 1, 2, 3, 4, 5, 6, 7]),
            ]])
            wd.write_export(payload)
            render.OUTPUT_DIR = wd.root / 'out'
            zip_path = render.render(
                wd.export_path, 'OTNB',
                split_stems=split_stems, neighbour=True,
            )
            return zip_path, wd
        except Exception:
            wd.__exit__(None, None, None)
            raise

    def test_split_neighbour_layout(self):
        from octapy.api.enums import MachineType
        zip_path, wd = self._render_neighbour(split_stems=True)
        try:
            project = Project.from_zip(zip_path)
            part = project.bank(1).part(1)

            # Flex tracks at T1, T3, T5 — DJ_EQ + COMPRESSOR each.
            for track_num in (1, 3, 5):
                t = part.audio_track(track_num)
                self.assertEqual(int(t.machine_type), int(MachineType.FLEX),
                                 f'T{track_num} should be FLEX')
                self.assertEqual(t.fx1_type, FX1Type.DJ_EQ)
                self.assertEqual(t.fx2_type, FX2Type.COMPRESSOR)

            # Neighbours at T2, T4, T6 — FILTER + DELAY each.
            for nb_num in (2, 4, 6):
                nb = part.audio_track(nb_num)
                self.assertEqual(int(nb.machine_type), int(MachineType.NEIGHBOR),
                                 f'T{nb_num} should be NEIGHBOR')
                self.assertEqual(nb.fx1_type, FX1Type.FILTER)
                self.assertEqual(nb.fx2_type, FX2Type.DELAY)

            # T8 keeps just the spatializer; the delay moved to the
            # neighbour tracks.
            t8 = part.audio_track(8)
            self.assertEqual(t8.fx1_type, FX1Type.SPATIALIZER)
            self.assertEqual(t8.fx2_type, FX2Type.OFF)

            # Pattern trigs land on the flex tracks (T1/T3/T5), not
            # T2/T3/T4 like the legacy layout.
            pattern = project.bank(1).pattern(1)
            for track_num in (1, 3, 5):
                self.assertEqual(
                    len(pattern.audio_track(track_num).active_steps), 8,
                )
            for nb_num in (2, 4, 6):
                self.assertEqual(
                    pattern.audio_track(nb_num).active_steps, [],
                    f'neighbour T{nb_num} must have no trigs',
                )
        finally:
            wd.__exit__(None, None, None)

    def test_mixed_neighbour_layout(self):
        from octapy.api.enums import MachineType
        zip_path, wd = self._render_neighbour(split_stems=False)
        try:
            project = Project.from_zip(zip_path)
            part = project.bank(1).part(1)
            # Flex on T1 only, neighbour on T2.
            self.assertEqual(int(part.audio_track(1).machine_type),
                             int(MachineType.FLEX))
            self.assertEqual(int(part.audio_track(2).machine_type),
                             int(MachineType.NEIGHBOR))
            self.assertEqual(part.audio_track(2).fx1_type, FX1Type.FILTER)
            self.assertEqual(part.audio_track(2).fx2_type, FX2Type.DELAY)
            self.assertEqual(part.audio_track(8).fx1_type, FX1Type.SPATIALIZER)
        finally:
            wd.__exit__(None, None, None)


class OctatrackFlattenTest(unittest.TestCase):
    """flatten=True collapses banks-of-cells into a flat cell list,
    then re-banks every 16 cells into a fresh bank — wraps from
    bank 1 → bank 2 etc."""

    TRACKS = ('kick', 'snare', 'hat')

    def test_flatten_repacks_18_cells_across_two_banks(self):
        render = load_render_module('octatrack/ot-basic')
        with WorkDir() as wd:
            paths = make_per_track_break_wavs(
                wd.samples, ['kk'], tracks=self.TRACKS,
                bpm=120, steps=32,
            )
            wd.stub_sources(paths)
            # Spread 18 cells across 4 input rows of varying length.
            # Total must wrap into bank 1 (16 patterns) + bank 2 (2).
            cell = make_capture_cell(['kk'], [0, 1, 2, 3, 4, 5, 6, 7])
            payload = make_export([
                [cell] * 5, [cell] * 6, [cell] * 4, [cell] * 3,
            ])
            wd.write_export(payload)
            render.OUTPUT_DIR = wd.root / 'out'
            zip_path = render.render(
                wd.export_path, 'OTFLAT', flatten=True,
            )
            project = Project.from_zip(zip_path)

            # Bank 1 patterns 1..16 should all have trigs (16 patterns).
            for pat_num in range(1, 17):
                self.assertEqual(
                    len(project.bank(1).pattern(pat_num).audio_track(1).active_steps),
                    8,
                    f'bank 1 pattern {pat_num} should be populated',
                )
            # Bank 2 should have only patterns 1 and 2 populated.
            self.assertEqual(
                len(project.bank(2).pattern(1).audio_track(1).active_steps),
                8,
            )
            self.assertEqual(
                len(project.bank(2).pattern(2).audio_track(1).active_steps),
                8,
            )
            self.assertEqual(
                project.bank(2).pattern(3).audio_track(1).active_steps,
                [],
            )


if __name__ == '__main__':
    unittest.main()

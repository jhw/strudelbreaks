"""Tests for the octatrack renderer (per-cell patterns target)."""
from __future__ import annotations

import unittest

from octapy import Project, TrigCondition

from ._fixtures import (
    WorkDir,
    load_render_module,
    make_break_wavs,
    make_capture_cell,
    make_export,
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

    def _render(self, probability=1.0):
        render = load_render_module('octatrack/ot-basic')
        wd = WorkDir().__enter__()
        try:
            paths = make_break_wavs(wd.samples, ['kk', 'sn'], bpm=120, steps=32)
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

    def test_render_produces_valid_project_with_expected_trigs(self):
        zip_path, wd = self._render(probability=1.0)
        try:
            self.assertTrue(zip_path.exists())

            project = Project.from_zip(zip_path)
            kk_slot = project.get_slot('kk.wav', slot_type='FLEX')
            sn_slot = project.get_slot('sn.wav', slot_type='FLEX')
            self.assertIsNotNone(kk_slot)
            self.assertIsNotNone(sn_slot)

            for slot in (kk_slot, sn_slot):
                sm = project.markers.get_slot(slot, is_static=False)
                self.assertEqual(sm.slice_count, 16)

            pattern = project.bank(1).pattern(1)
            self.assertEqual(pattern.scale_length, 16)
            track = pattern.audio_track(1)

            # eventsPerCycle=8 events → trigs at OT steps 1,3,5,7,9,11,13,15.
            # Step 7 (events index 3) is a rest → no trig there.
            self.assertEqual(track.active_steps, self.EXPECTED_ACTIVE)

            # First event maps to break 'kk' slot, slice 0.
            self.assertEqual(track.step(1).sample_lock, kk_slot)
            self.assertEqual(track.step(1).slice_index, 0)
            # Polymetric stretch: events 4,5 map to break index 2 = 'kk'
            # again; pattern indices were 4 (event 1) and 1 (event 4).
            self.assertEqual(track.step(9).sample_lock, kk_slot)
            self.assertEqual(track.step(9).slice_index, 1)
        finally:
            wd.__exit__(None, None, None)

    def test_default_probability_leaves_condition_unset(self):
        zip_path, wd = self._render(probability=1.0)
        try:
            project = Project.from_zip(zip_path)
            track = project.bank(1).pattern(1).audio_track(1)
            for s in self.EXPECTED_ACTIVE:
                cond = track.step(s).condition
                # Default OT trig condition value (no override) is NONE.
                self.assertIn(cond, (None, TrigCondition.NONE))
        finally:
            wd.__exit__(None, None, None)

    def test_custom_probability_locks_every_trig(self):
        zip_path, wd = self._render(probability=0.5)
        try:
            project = Project.from_zip(zip_path)
            track = project.bank(1).pattern(1).audio_track(1)
            for s in self.EXPECTED_ACTIVE:
                self.assertEqual(track.step(s).condition,
                                 TrigCondition.PERCENT_50)
        finally:
            wd.__exit__(None, None, None)

    def test_invalid_probability_raises(self):
        render = load_render_module('octatrack/ot-basic')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['kk'], bpm=120, steps=32)
            wd.stub_sources(paths)
            payload = make_export([[
                make_capture_cell(['kk'], [0, 1, 2, 3, 4, 5, 6, 7]),
            ]])
            wd.write_export(payload)
            render.OUTPUT_DIR = wd.root / 'out'
            with self.assertRaises(ValueError):
                render.render(wd.export_path, 'OOR', probability=1.5)


if __name__ == '__main__':
    unittest.main()

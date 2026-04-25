"""Tests for the octatrack renderer (per-cell patterns target)."""
from __future__ import annotations

import unittest

from octapy import Project

from ._fixtures import (
    WorkDir,
    load_render_module,
    make_break_wavs,
    make_capture_cell,
    make_export,
    stub_sample_fetch,
)


class OctatrackHelpersTest(unittest.TestCase):
    def setUp(self):
        self.render = load_render_module('octatrack')

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


class OctatrackRoundtripTest(unittest.TestCase):
    def test_render_produces_valid_project_with_expected_trigs(self):
        render = load_render_module('octatrack')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['kk', 'sn'], bpm=120, steps=32)
            stub_sample_fetch(render, paths)

            payload = make_export([[
                make_capture_cell(['kk', 'sn', 'kk', 'sn'],
                                  [0, 4, 8, None, 1, 5, 9, 13]),
            ]])
            wd.write_export(payload)

            render.OUTPUT_DIR = wd.root / 'out'
            zip_path = render.render(wd.export_path, 'OTROUNDTRIP')
            self.assertTrue(zip_path.exists())

            project = Project.from_zip(zip_path)
            # Two unique flex slots for the two break names.
            kk_slot = project.get_slot('kk.wav', slot_type='FLEX')
            sn_slot = project.get_slot('sn.wav', slot_type='FLEX')
            self.assertIsNotNone(kk_slot)
            self.assertIsNotNone(sn_slot)

            for slot in (kk_slot, sn_slot):
                sm = project.markers.get_slot(slot, is_static=False)
                self.assertEqual(sm.slice_count, 16)

            bank = project.bank(1)
            pattern = bank.pattern(1)
            self.assertEqual(pattern.scale_length, 16)
            track = pattern.audio_track(1)

            # eventsPerCycle=8 events → trigs at OT steps 1,3,5,7,9,11,13,15.
            # Step 7 (events index 3) is a rest → no trig there.
            expected_active = [1, 3, 5, 9, 11, 13, 15]
            self.assertEqual(track.active_steps, expected_active)

            # First event maps to break 'kk' slot, slice 0.
            self.assertEqual(track.step(1).sample_lock, kk_slot)
            self.assertEqual(track.step(1).slice_index, 0)
            # Polymetric stretch: events 4,5 map to break index 2 = 'kk'
            # again; pattern indices were 4 (event 1) and 1 (event 4).
            self.assertEqual(track.step(9).sample_lock, kk_slot)
            self.assertEqual(track.step(9).slice_index, 1)


if __name__ == '__main__':
    unittest.main()

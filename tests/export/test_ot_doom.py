"""Tests for the ot-doom (megabreak of doom) renderer."""
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


class OtDoomHelpersTest(unittest.TestCase):
    def setUp(self):
        self.render = load_render_module('ot-doom')

    def test_unique_preserve_order(self):
        self.assertEqual(
            self.render.unique_preserve_order(['a', 'b', 'a', 'c', 'b']),
            ['a', 'b', 'c'],
        )

    def test_pad_b_to_16_repeats(self):
        out = self.render.pad_b_to_16(['x', 'y', 'z', 'w'])
        self.assertEqual(len(out), 16)
        # Repeating cycle of length 4
        self.assertEqual(out[:4], ['x', 'y', 'z', 'w'])
        self.assertEqual(out[4:8], ['x', 'y', 'z', 'w'])
        self.assertEqual(out[12:], ['x', 'y', 'z', 'w'])

    def test_expand_break_names_polymetric(self):
        # 4 names over 8 events → a a b b c c d d (i*M//N rule)
        out = self.render.expand_break_names(['a', 'b', 'c', 'd'], 8)
        self.assertEqual(out, ['a', 'a', 'b', 'b', 'c', 'c', 'd', 'd'])


class OtDoomBValidationTest(unittest.TestCase):
    """|B| ∈ {4, 8, 16} is a hard contract; everything else exits."""

    def _build(self, break_names, wd):
        render = load_render_module('ot-doom')
        paths = make_break_wavs(wd.samples, break_names, bpm=120)
        stub_sample_fetch(render, paths)
        payload = make_export([[
            make_capture_cell(break_names, [0, 1, 2, 3, 4, 5, 6, 7]),
        ]])
        wd.write_export(payload)
        render.OUTPUT_DIR = wd.root / 'out'
        render.RENDER_DIR = wd.root / 'render'
        return render

    def test_b_2_is_rejected(self):
        with WorkDir() as wd:
            render = self._build(['a', 'b'], wd)
            with self.assertRaises(SystemExit) as ctx:
                render.render(wd.export_path, 'BCHECK')
            self.assertIn('|B|=2', str(ctx.exception))

    def test_b_3_is_rejected(self):
        with WorkDir() as wd:
            render = self._build(['a', 'b', 'c'], wd)
            with self.assertRaises(SystemExit) as ctx:
                render.render(wd.export_path, 'BCHECK')
            self.assertIn('|B|=3', str(ctx.exception))

    def test_b_4_is_accepted(self):
        with WorkDir() as wd:
            render = self._build(['a', 'b', 'c', 'd'], wd)
            zip_path = render.render(wd.export_path, 'BCHECK')
            self.assertTrue(zip_path.exists())


class OtDoomRoundtripTest(unittest.TestCase):
    def test_b_4_render_has_4_slots_scenes_and_diagonal_locks(self):
        render = load_render_module('ot-doom')
        with WorkDir() as wd:
            paths = make_break_wavs(wd.samples, ['a', 'b', 'c', 'd'], bpm=120)
            stub_sample_fetch(render, paths)
            payload = make_export([[
                make_capture_cell(['a', 'b', 'c', 'd'],
                                  [0, 4, 8, 12, 1, 5, 9, 13]),
            ]])
            wd.write_export(payload)
            render.OUTPUT_DIR = wd.root / 'out'
            render.RENDER_DIR = wd.root / 'render'
            zip_path = render.render(wd.export_path, 'OTDOOMRT')
            self.assertTrue(zip_path.exists())

            project = Project.from_zip(zip_path)
            # Slot count = |B| = 4.
            self.assertIsNotNone(project.get_slot('b01p01_s00.wav'))
            self.assertIsNotNone(project.get_slot('b01p01_s03.wav'))
            self.assertIsNone(project.get_slot('b01p01_s04.wav'))

            # Each timesliced wav has 16 sub-slices on-device.
            for s in range(4):
                slot = project.get_slot(f'b01p01_s{s:02d}.wav')
                sm = project.markers.get_slot(slot, is_static=False)
                self.assertEqual(sm.slice_count, 16)

            bank = project.bank(1)
            part = bank.part(1)
            t1 = part.audio_track(1)
            # Slice mode ON on the SRC setup page.
            self.assertEqual(int(t1.setup.slice), 1)

            # Crossfader trick: scene 1 → slice 0, scene 2 → last slice.
            self.assertEqual(part.scene(1).track(1).playback_param2, 0)
            self.assertEqual(part.scene(2).track(1).playback_param2, 127)
            self.assertEqual(part.active_scene_a, 0)
            self.assertEqual(part.active_scene_b, 1)

            pattern = bank.pattern(1)
            track = pattern.audio_track(1)
            self.assertEqual(pattern.scale_length, 16)
            self.assertEqual(track.active_steps, list(range(1, 17)))

            # Step→slot mapping (i * |B|) // 16 with |B|=4: 4 consecutive
            # steps per slot.
            slots_in_order = [
                project.get_slot(f'b01p01_s{s:02d}.wav') for s in range(4)
            ]
            for i in range(16):
                expected = slots_in_order[(i * 4) // 16]
                self.assertEqual(track.step(i + 1).sample_lock, expected)
                self.assertEqual(track.step(i + 1).slice_index, 0)


if __name__ == '__main__':
    unittest.main()

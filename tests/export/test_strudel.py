"""Tests for the strudel playback-template renderer."""
from __future__ import annotations

import re
import unittest

from ._fixtures import WorkDir, load_render_module, make_capture_cell, make_export


class StrudelHelpersTest(unittest.TestCase):
    def setUp(self):
        self.render = load_render_module('strudel')

    def test_format_break_curly_polymetric(self):
        s = self.render.format_break(['a', 'b', 'c', 'd'], 8)
        self.assertEqual(s, '{a b c d}%8')

    def test_format_pattern_with_rest(self):
        s = self.render.format_pattern([0, 4, None, 12])
        self.assertEqual(s, '[0 4 ~ 12]')

    def test_dedup_indexed_first_seen_order(self):
        vocab, idx = self.render.dedup_indexed(['x', 'y', 'x', 'z', 'y'])
        self.assertEqual(vocab, ['x', 'y', 'z'])
        self.assertEqual(idx, [0, 1, 0, 2, 1])

    def test_build_rows_pads_short_rows_modulo(self):
        # row 0 has 1 cell, row 1 has 2 cells; output max_len = 2
        # row 0 cell index 1 should wrap to cell 0
        banks = [
            [make_capture_cell(['a'], [0])],
            [make_capture_cell(['b'], [1]), make_capture_cell(['b'], [2])],
        ]
        rows, max_len = self.render.build_rows(banks, events_per_cycle=8)
        self.assertEqual(max_len, 2)
        self.assertEqual(rows[0]['length'], 1)
        self.assertEqual(rows[1]['length'], 2)
        # Row 0 wraps: both cells are the same content, so dedup yields
        # one vocab entry and idx [0, 0].
        self.assertEqual(rows[0]['break_idx'], [0, 0])
        self.assertEqual(rows[0]['pattern_idx'], [0, 0])


class StrudelRoundtripTest(unittest.TestCase):
    def test_render_emits_js_with_expected_structure(self):
        render = load_render_module('strudel')
        with WorkDir() as wd:
            payload = make_export([[
                make_capture_cell(['kk', 'sn'], [0, 4, 8, None, 1, 5, 9, 13]),
            ]])
            wd.write_export(payload)

            # redirect output dir into the temp tree
            render.OUTPUT_DIR = wd.root / 'out'
            out_path = render.render(wd.export_path, 'TESTSTRUDEL')
            self.assertTrue(out_path.exists())

            text = out_path.read_text()
            # Mini-notation strings get baked in as double-quoted literals.
            self.assertIn('"{kk sn}%8"', text)
            self.assertIn('"[0 4 8 ~ 1 5 9 13]"', text)

            # Header comment names the source file (helps tracing exports
            # back to captures).
            self.assertIn('export.json', text)

            # Loose sanity on the playback shape — should reference the
            # generated arrays by name.
            self.assertTrue(re.search(r'breakVocab\b', text))
            self.assertTrue(re.search(r'patternVocab\b', text))


if __name__ == '__main__':
    unittest.main()

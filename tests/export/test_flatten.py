"""Unit tests for the OT flatten helpers in app/export/octatrack/_flatten.py.

Flatten collapses captured banks → flat cell list, then re-rows for
the target's pattern shape:
* ot-basic: every 16 cells → one new row, no rounding constraint.
* ot-doom : greedy 16/8/4 decomposition; remainder under 4 raises.
"""
from __future__ import annotations

import unittest

from app.export.octatrack._flatten import (
    flatten_cells,
    regroup_basic,
    regroup_doom,
)


def _cells(n):
    return [{'id': i} for i in range(n)]


class FlattenCellsTest(unittest.TestCase):
    def test_preserves_order_across_rows(self):
        banks = [_cells(3), _cells(2)]
        # ids 0,1,2 then 0,1 — flatten preserves the order they appear.
        flat = flatten_cells(banks)
        self.assertEqual(len(flat), 5)
        self.assertEqual([c['id'] for c in flat], [0, 1, 2, 0, 1])

    def test_empty_banks_collapse_to_empty_list(self):
        self.assertEqual(flatten_cells([]), [])
        self.assertEqual(flatten_cells([[], []]), [])


class RegroupBasicTest(unittest.TestCase):
    def test_under_16_keeps_one_row(self):
        rows = regroup_basic(_cells(5))
        self.assertEqual([len(r) for r in rows], [5])

    def test_exactly_16_one_row(self):
        rows = regroup_basic(_cells(16))
        self.assertEqual([len(r) for r in rows], [16])

    def test_17_wraps_to_two_rows(self):
        # Bank-spillover: 17 cells → bank 1 (16) + bank 2 (1).
        rows = regroup_basic(_cells(17))
        self.assertEqual([len(r) for r in rows], [16, 1])

    def test_33_wraps_to_three_rows(self):
        rows = regroup_basic(_cells(33))
        self.assertEqual([len(r) for r in rows], [16, 16, 1])

    def test_preserves_cell_order(self):
        rows = regroup_basic(_cells(20))
        flat = [c for row in rows for c in row]
        self.assertEqual([c['id'] for c in flat], list(range(20)))


class RegroupDoomTest(unittest.TestCase):
    def test_size_4_8_16_each_one_row(self):
        for n, expected in [(4, [4]), (8, [8]), (16, [16])]:
            rows = regroup_doom(_cells(n))
            self.assertEqual([len(r) for r in rows], expected,
                             f'{n} → {[len(r) for r in rows]}')

    def test_greedy_packs_largest_first(self):
        # 12 → [8, 4] (not [4, 4, 4]); 20 → [16, 4]; 24 → [16, 8];
        # 28 → [16, 8, 4]; 32 → [16, 16].
        cases = {
            12: [8, 4],
            20: [16, 4],
            24: [16, 8],
            28: [16, 8, 4],
            32: [16, 16],
            40: [16, 16, 8],
            44: [16, 16, 8, 4],
        }
        for n, expected in cases.items():
            rows = regroup_doom(_cells(n))
            self.assertEqual([len(r) for r in rows], expected,
                             f'{n} → {[len(r) for r in rows]}')

    def test_stray_remainder_raises(self):
        # 1, 2, 3, 5, 6, 7 cells leave a remainder under 4 — the
        # decomposition fails and the caller is meant to surface the
        # message in the UI status panel.
        for n in (1, 2, 3, 5, 6, 7, 9, 13):
            with self.assertRaises(ValueError, msg=f'{n} should raise') as ctx:
                regroup_doom(_cells(n))
            self.assertIn('stray', str(ctx.exception).lower())

    def test_zero_cells_raises(self):
        with self.assertRaises(ValueError):
            regroup_doom([])

    def test_preserves_cell_order(self):
        rows = regroup_doom(_cells(28))   # [16, 8, 4]
        flat = [c for row in rows for c in row]
        self.assertEqual([c['id'] for c in flat], list(range(28)))


if __name__ == '__main__':
    unittest.main()

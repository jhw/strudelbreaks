"""Shared flatten helpers for the OT renderers.

`flatten=True` collapses the captured list-of-lists patch structure
into a single long flat cell list, then re-splits into rows the target
can handle. The two targets re-split differently:

* ot-basic: each row → one bank (16 patterns per bank). After flatten
  we re-pack every 16 cells into a fresh row, wrapping bank 1 → bank
  2 → ... up to the OT 16-bank ceiling.

* ot-doom: each row → one pattern with `|C| ∈ {4, 8, 16}` cells. After
  flatten we greedily decompose total-N into a sequence of 16/8/4
  chunks (largest first, so the export is as compact as possible). A
  remainder under 4 raises — the caller is expected to surface that
  through the UI status panel.
"""
from __future__ import annotations

from typing import Iterable, List


# Sizes ot-doom permits for `|C|` (cells per row / pattern). Greedy
# decomposition tries the largest first.
DOOM_GROUP_SIZES = (16, 8, 4)

# OT bank capacity — both targets cap a row's cell count at this many
# (ot-basic: cells in a bank, ot-doom: patterns in a bank).
OT_BANK_CAPACITY = 16


def flatten_cells(banks: Iterable[Iterable[dict]]) -> List[dict]:
    """Collapse list-of-lists captures into a flat ordered cell list."""
    return [cell for row in banks for cell in row]


def regroup_basic(cells: List[dict]) -> List[List[dict]]:
    """Re-bank a flat cell list for ot-basic: every 16 cells → one new
    row. The last row may be shorter — there's no rounding constraint
    on ot-basic banks.
    """
    return [cells[i:i + OT_BANK_CAPACITY]
            for i in range(0, len(cells), OT_BANK_CAPACITY)]


def regroup_doom(cells: List[dict]) -> List[List[dict]]:
    """Re-row a flat cell list for ot-doom.

    Greedy decomposition: peel off chunks of 16 first, then 8, then 4.
    Anything left over (1, 2, or 3 cells) raises ValueError so the
    caller can surface "stray patterns" in the UI status panel — the
    expected fix is to add or remove cells in tempera so the total
    lands on a valid count.
    """
    n = len(cells)
    if n == 0:
        raise ValueError('flatten produced no cells')
    sizes: List[int] = []
    remaining = n
    for size in DOOM_GROUP_SIZES:
        while remaining >= size:
            sizes.append(size)
            remaining -= size
    if remaining:
        raise ValueError(
            f'flatten: {n} cells leaves {remaining} stray patterns '
            f'(allowed totals are sums of {list(DOOM_GROUP_SIZES)} — '
            f'add or remove cells in tempera)'
        )

    rows: List[List[dict]] = []
    pos = 0
    for size in sizes:
        rows.append(cells[pos:pos + size])
        pos += size
    return rows

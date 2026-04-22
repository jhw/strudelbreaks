#!/usr/bin/env python3
"""Render a tempera captures JSON export into a standalone Strudel playback
template.

The template loads the sample gist at runtime (`samples(gistUrl)`) but
bakes the breaks/patterns from the export as literal mini-notation
strings, so playback does not fetch any JSON for the musical material.

Two sliders drive playback: `rowSlider` selects a bank, `cellSlider`
selects a cell within the bank. The cell slider is sized on the longest
row; shorter rows wrap by cell-index modulo their source length.

Usage:
    python pyscripts/render_capture.py <path/to/export.json>

Output:
    tmp/strudel/<basename>.strudel.js
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from jinja2 import Environment, FileSystemLoader, StrictUndefined

SCHEMA_EXPECTED = 6
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TEMPLATE_DIR = SCRIPT_DIR / 'templates'
OUTPUT_DIR = REPO_ROOT / 'tmp' / 'strudel'


def format_break(names, events_per_cycle):
    return '{' + ' '.join(names) + '}%' + str(events_per_cycle)


def format_pattern(slices, rest_char='~'):
    return '[' + ' '.join(rest_char if s is None else str(s) for s in slices) + ']'


def dedup_indexed(values):
    """Return (vocab, idx) where vocab is the unique items in first-seen
    order and idx[i] points values[i] back into vocab."""
    seen = {}
    vocab = []
    idx = []
    for v in values:
        if v not in seen:
            seen[v] = len(vocab)
            vocab.append(v)
        idx.append(seen[v])
    return vocab, idx


def build_rows(banks, events_per_cycle):
    non_empty = [b for b in banks if b]
    if not non_empty:
        sys.exit('no non-empty banks in export')
    max_len = max(len(b) for b in non_empty)
    rows = []
    for bank in non_empty:
        source_len = len(bank)
        breaks = []
        patterns = []
        for i in range(max_len):
            cell = bank[i % source_len]
            breaks.append(format_break(cell['break'], events_per_cycle))
            patterns.append(format_pattern(cell['pattern']))
        break_vocab, break_idx = dedup_indexed(breaks)
        pattern_vocab, pattern_idx = dedup_indexed(patterns)
        rows.append({
            'length': source_len,
            'break_vocab': break_vocab,
            'break_idx': break_idx,
            'pattern_vocab': pattern_vocab,
            'pattern_idx': pattern_idx,
        })
    return rows, max_len


def format_vocab_js(rows, field):
    # Mini strings are rendered double-quoted so Strudel's transpiler
    # lifts them to Pattern instances at parse time (see STRUDEL.md).
    # Each vocab item on its own line — break strings run ~55 chars,
    # so two-per-line would push past a comfortable editor width.
    blocks = []
    for ri, row in enumerate(rows):
        row_trailing = ',' if ri < len(rows) - 1 else ''
        items = row[field]
        item_lines = []
        for ii, s in enumerate(items):
            item_trailing = ',' if ii < len(items) - 1 else ''
            item_lines.append(f'    "{s}"{item_trailing}')
        body = '\n'.join(item_lines)
        blocks.append(f"  [\n{body}\n  ]{row_trailing}")
    return '\n'.join(blocks)


def format_idx_js(rows, field):
    lines = []
    for ri, row in enumerate(rows):
        trailing = ',' if ri < len(rows) - 1 else ''
        joined = ', '.join(str(k) for k in row[field])
        lines.append(f'  [{joined}]{trailing}')
    return '\n'.join(lines)


def render(export_path):
    payload = json.loads(export_path.read_text())

    schema = payload.get('schema')
    if schema != SCHEMA_EXPECTED:
        sys.exit(f'schema mismatch: got {schema}, expected {SCHEMA_EXPECTED}')

    ctx = payload.get('context') or {}
    for key in ('gistUser', 'gistId', 'bpm', 'beatsPerCycle',
                'loopCycles', 'nSlices', 'eventsPerCycle'):
        if key not in ctx:
            sys.exit(f'context missing field: {key}')

    rows, max_row_len = build_rows(payload.get('banks') or [], ctx['eventsPerCycle'])

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    tmpl = env.get_template('playback.strudel.js.j2')
    rendered = tmpl.render(
        source_filename=export_path.name,
        schema=schema,
        gist_url=f"https://gist.githubusercontent.com/{ctx['gistUser']}/{ctx['gistId']}/raw/strudel.json",
        bpm=ctx['bpm'],
        beats_per_cycle=ctx['beatsPerCycle'],
        loop_cycles=ctx['loopCycles'],
        n_slices=ctx['nSlices'],
        n_rows=len(rows),
        max_row_len=max_row_len,
        break_vocab_js=format_vocab_js(rows, 'break_vocab'),
        break_idx_js=format_idx_js(rows, 'break_idx'),
        pattern_vocab_js=format_vocab_js(rows, 'pattern_vocab'),
        pattern_idx_js=format_idx_js(rows, 'pattern_idx'),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / (export_path.stem + '.strudel.js')
    out_path.write_text(rendered)
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('export', type=pathlib.Path,
                    help='path to a tempera captures JSON export')
    args = ap.parse_args()

    if not args.export.is_file():
        sys.exit(f'not a file: {args.export}')

    out = render(args.export)
    print(out)


if __name__ == '__main__':
    main()

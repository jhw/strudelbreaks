#!/usr/bin/env python3
"""Render a tempera captures JSON export into a Torso S-4 sample bundle.

Each non-empty bank from the captures becomes one row WAV. A row is
the audio concatenation of its cells, where each cell is the captured
Strudel pattern played through the cell's break vocabulary (slice
indices into the source breaks, polymetric-stretched per STRUDEL.md).

Output is a project zip:

    tmp/torso-s4/<project>.zip
    └── <project>/
        ├── <adj-noun-1>.wav   (row 1)
        ├── <adj-noun-2>.wav   (row 2)
        └── …

Push extracts that zip into `/Volumes/S4/samples/strudelbeats/`,
landing at `/Volumes/S4/samples/strudelbeats/<project>/<row>.wav` —
the S-4's manual-defined `/samples/` is where user-imported wavs
belong.

Imported and called by the FastAPI server (`app/exporters.py`) — no CLI;
the server writes the captures payload to a temp file and points
`output_dir` / `render_dir` at a temp dir.

The seed deterministically selects the per-row wav names, so re-running
the same export with the same seed reproduces the bundle byte-for-byte.
"""
from __future__ import annotations

import pathlib
import random
import sys
import zipfile

from app.export.common import sample_source
from app.export.common.audio_fades import (
    DEFAULT_FADE_IN_MS,
    DEFAULT_FADE_OUT_MS,
)
from app.export.common.names import generate_name
from app.export.common.schema import load_export

from .audio import (
    S4_SAMPLE_RATE,
    equal_slices,
    export_wav,
    load_break,
    render_cell,
    render_row,
)


N_SLICES = 16

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / 'tmp' / 'torso-s4'
RENDER_DIR = REPO_ROOT / 'tmp' / 'torso-s4-render'

REQUIRED_CTX = ('gistUser', 'gistId', 'bpm', 'beatsPerCycle',
                'eventsPerCycle', 'nSlices')


def event_ms(bpm, beats_per_cycle, events_per_cycle):
    """Length in ms of one captured event at the project tempo (float).

    Kept as a float so callers can sum N events back to the exact bar
    length — `int(round(...))` here would drop fractional ms (e.g.
    234.375 → 234 at 128 BPM × 8 events) and the loss compounds over
    long rows. `audio.render_cell` consumes the float and computes
    integer-ms event boundaries cumulatively.
    """
    cycle_s = beats_per_cycle * 60.0 / bpm
    return cycle_s * 1000 / events_per_cycle


def unique_row_names(rng, count):
    """Generate `count` distinct adjective-noun names using `rng`.

    Re-rolls on collision so we never emit two identical filenames in
    one project zip; the adjective × noun space is large enough
    (thousands of pairs) that the rejection loop is effectively O(1)
    per draw at typical capture sizes.
    """
    seen = set()
    out = []
    while len(out) < count:
        candidate = generate_name(rng)
        if candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def build_row_wavs(export_path, name, seed=None, source='json',
                   fade_in_ms=DEFAULT_FADE_IN_MS,
                   fade_out_ms=DEFAULT_FADE_OUT_MS):
    """Return a list of (filename, AudioSegment) for each non-empty row."""
    payload, ctx = load_export(export_path, REQUIRED_CTX)
    if ctx['nSlices'] != N_SLICES:
        sys.exit(f'nSlices {ctx["nSlices"]} != {N_SLICES} (torso-s4 assumes 16)')

    banks_in = [b for b in (payload.get('banks') or []) if b]
    if not banks_in:
        sys.exit('no non-empty banks in export')

    all_names = sorted({
        break_name
        for bank_cells in banks_in
        for cell in bank_cells
        for break_name in cell['break']
    })

    paths = sample_source.resolve_break_paths(
        gist_user=ctx['gistUser'],
        gist_id=ctx['gistId'],
        names=all_names,
        source=source,
        target_bpm=ctx['bpm'],
        target_sample_rate=S4_SAMPLE_RATE,
    )

    # Source slices: each break wav cut into 16 equal slices once.
    source_slices = {n: equal_slices(load_break(paths[n]), N_SLICES)
                     for n in all_names}

    ev_ms = event_ms(ctx['bpm'], ctx['beatsPerCycle'], ctx['eventsPerCycle'])

    # Per-row wav names share `name`'s seed so the bundle is
    # reproducible — same export + same seed → same filenames.
    rng = random.Random(seed) if seed is not None else random.Random(name)
    row_names = unique_row_names(rng, len(banks_in))

    out = []
    for row_idx, bank_cells in enumerate(banks_in):
        cells = [
            render_cell(source_slices, cell['break'], cell['pattern'], ev_ms,
                        fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms)
            for cell in bank_cells
        ]
        row_seg = render_row(cells)
        out.append((f'{row_names[row_idx]}.wav', row_seg))
    return out


def render(export_path, name, *, seed=None, source='json',
           output_dir=None, render_dir=None,
           fade_in_ms=DEFAULT_FADE_IN_MS,
           fade_out_ms=DEFAULT_FADE_OUT_MS):
    """Render an export → project zip; return the zip path."""
    rows = build_row_wavs(export_path, name, seed=seed, source=source,
                          fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms)

    render_root = pathlib.Path(render_dir) if render_dir is not None else RENDER_DIR
    project_render_dir = render_root / name
    if project_render_dir.exists():
        for old in project_render_dir.glob('*.wav'):
            old.unlink()
    project_render_dir.mkdir(parents=True, exist_ok=True)

    out_dir = pathlib.Path(output_dir) if output_dir is not None else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f'{name}.zip'

    # Write each wav once into project_render_dir, then bundle into zip
    # with a top-level <project>/<wav> layout so push.py can extract
    # under /Volumes/S4/samples/strudelbeats/ and land at
    # /Volumes/S4/samples/strudelbeats/<project>/<wav>.
    for filename, seg in rows:
        export_wav(seg, project_render_dir / filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for filename, _ in rows:
            zf.write(project_render_dir / filename, arcname=f'{name}/{filename}')

    return zip_path

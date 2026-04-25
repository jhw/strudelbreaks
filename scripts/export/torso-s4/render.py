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

Usage:
    python scripts/export/torso-s4/render.py <export.json>
        [--name NAME] [--seed N]

The seed deterministically selects the project name and the per-row
wav names, so re-running the same export with the same seed
reproduces the bundle byte-for-byte.
"""
from __future__ import annotations

import json
import pathlib
import random
import sys
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.cli import build_parser, require_file, resolve_name
from common.names import generate_name
from common.schema import load_export

from audio import (
    equal_slices,
    export_wav,
    load_break,
    render_cell,
    render_row,
)


N_SLICES = 16

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / 'tmp' / 'torso-s4'
SAMPLES_DIR = REPO_ROOT / 'tmp' / 'samples'
RENDER_DIR = REPO_ROOT / 'tmp' / 'torso-s4-render'

REQUIRED_CTX = ('gistUser', 'gistId', 'bpm', 'beatsPerCycle',
                'eventsPerCycle', 'nSlices')


def fetch_sample_manifest(gist_user, gist_id):
    url = f'https://gist.githubusercontent.com/{gist_user}/{gist_id}/raw/strudel.json'
    with urllib.request.urlopen(url) as r:
        data = json.loads(r.read())
    base = data.get('_base', '')
    out = {}
    for k, v in data.items():
        if k.startswith('_'):
            continue
        first = v[0] if isinstance(v, list) else v
        out[k] = base + first if not first.startswith(('http://', 'https://')) else first
    return out


def cache_sample(name, url, cache_dir):
    ext = pathlib.Path(url.split('?', 1)[0]).suffix or '.wav'
    path = cache_dir / f'{name}{ext}'
    if path.exists():
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as r, open(path, 'wb') as f:
        f.write(r.read())
    return path


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


def build_row_wavs(export_path, name, seed=None):
    """Return a list of (filename, AudioSegment) for each non-empty row."""
    payload, ctx = load_export(export_path, REQUIRED_CTX)
    if ctx['nSlices'] != N_SLICES:
        sys.exit(f'nSlices {ctx["nSlices"]} != {N_SLICES} (torso-s4 assumes 16)')

    banks_in = [b for b in (payload.get('banks') or []) if b]
    if not banks_in:
        sys.exit('no non-empty banks in export')

    manifest = fetch_sample_manifest(ctx['gistUser'], ctx['gistId'])

    all_names = set()
    for bank_cells in banks_in:
        for cell in bank_cells:
            for break_name in cell['break']:
                all_names.add(break_name)
    missing = [n for n in all_names if n not in manifest]
    if missing:
        sys.exit(f'sample gist missing breaks: {missing}')

    cache_dir = SAMPLES_DIR / ctx['gistId']
    paths = {n: cache_sample(n, manifest[n], cache_dir) for n in all_names}

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
            render_cell(source_slices, cell['break'], cell['pattern'], ev_ms)
            for cell in bank_cells
        ]
        row_seg = render_row(cells)
        out.append((f'{row_names[row_idx]}.wav', row_seg))
    return out


def render(export_path, name, seed=None):
    """Render an export → project zip; return the zip path."""
    rows = build_row_wavs(export_path, name, seed=seed)

    render_dir = RENDER_DIR / name
    if render_dir.exists():
        for old in render_dir.glob('*.wav'):
            old.unlink()
    render_dir.mkdir(parents=True, exist_ok=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT_DIR / f'{name}.zip'

    # Write each wav once into render_dir, then bundle into zip with
    # a top-level <project>/<wav> layout so push.py can extract under
    # /Volumes/S4/samples/strudelbeats/ and land at
    # /Volumes/S4/samples/strudelbeats/<project>/<wav>.
    for filename, seg in rows:
        export_wav(seg, render_dir / filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for filename, _ in rows:
            zf.write(render_dir / filename, arcname=f'{name}/{filename}')

    return zip_path


def main():
    parser = build_parser(__doc__.splitlines()[0])
    args = parser.parse_args()
    require_file(args.export)
    out = render(args.export, resolve_name(args), seed=args.seed)
    print(out)


if __name__ == '__main__':
    main()

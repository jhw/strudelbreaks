#!/usr/bin/env python3
"""Render a tempera captures JSON export into an Octatrack megabreak-of-doom
project zip — cell-input variant.

Each non-empty row of the captures becomes one OT bank with one part and
one 16-step pattern on track 1. The cells of the row are the doom
*inputs* (must be 4, 8, or 16): each cell renders to a bar of audio,
then N matrix chains are built where chain[k] is the k-th equal segment
of every input concatenated. N flex slots receive the chains with N
slice markers each. The pattern fires N trigs at intervals of 16/N,
each sample-locked to its chain. Scenes lock track 1's slice_index to
0 and N-1 — the crossfader interpolates between, walking the input
axis: at any fader position s, every trig plays segment k of input s,
so input s plays in full grid-aligned.

This differs from the forum-canonical megabreak (which crossfades
between source breaks rather than between captured patterns). See
docs/planning/ot-doom.md for the full design + comparison.

Usage:
    python scripts/export/ot-doom/render.py <path/to/export.json> [--name NAME] [--seed N]

Output:
    tmp/ot-doom/<name>.zip
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from octapy import (
    Project,
    SliceMode,
)

from common.cli import build_parser, require_file, resolve_name
from common.schema import load_export

from audio import (
    build_matrix_chain,
    equal_slices,
    export_wav,
    load_break,
    render_cell_audio,
)


# Source break wavs are 32 steps (2 bars at 1/16). N_SLICES=16 cuts them
# into 16 slices of 2 steps each — same scheme as the existing octatrack
# target. See scripts/export/octatrack/render.py for the ghost-beat
# rationale.
N_SLICES = 16
N_PATTERN_STEPS = 16          # 1 bar at 1/16 — one Strudel cycle.
ALLOWED_INPUT_COUNTS = (4, 8, 16)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / 'tmp' / 'ot-doom'
SAMPLES_DIR = REPO_ROOT / 'tmp' / 'samples'
RENDER_DIR = REPO_ROOT / 'tmp' / 'ot-doom-render'

REQUIRED_CTX = ('gistUser', 'gistId', 'bpm', 'eventsPerCycle', 'nSlices')


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


def set_equal_slices(project, slot, n_slices, segment_ms, sample_rate):
    """N equal slice markers across `n_slices * segment_ms` of audio."""
    slot_markers = project.markers.get_slot(slot, is_static=False)
    total_ms = n_slices * segment_ms
    frame_count = int(round(total_ms * sample_rate / 1000))
    slot_markers.sample_length = frame_count
    slices = [
        (int(round(i * segment_ms)), int(round((i + 1) * segment_ms)))
        for i in range(n_slices)
    ]
    slot_markers.set_slices_ms(slices, sample_rate=sample_rate)
    project.markers.set_slot(slot, slot_markers, is_static=False)


def render_row(
    project,
    bank_idx,
    cells,
    events_per_cycle,
    source_slice_cache,
    row_render_dir,
):
    """Render one tempera row into one OT bank/pattern via matrix chains."""
    n = len(cells)
    if n not in ALLOWED_INPUT_COUNTS:
        sys.exit(
            f'row {bank_idx + 1}: |C|={n} cells (allowed {sorted(ALLOWED_INPUT_COUNTS)}) — '
            f'add or remove cells in tempera so each row has 4, 8, or 16'
        )

    # Load source slices for every break referenced in this row, lazily.
    for cell in cells:
        for name in cell['break']:
            if name not in source_slice_cache:
                seg = load_break(source_slice_cache['__paths__'][name])
                source_slice_cache[name] = equal_slices(seg, N_SLICES)

    anchor_slice = next(s[0] for k, s in source_slice_cache.items()
                        if k != '__paths__')
    sample_rate = anchor_slice.frame_rate

    # Render each cell to one bar of audio.
    input_audios = [
        render_cell_audio(cell, source_slice_cache, events_per_cycle)
        for cell in cells
    ]

    bar_ms = len(input_audios[0])
    segment_ms = bar_ms / n

    # Build N chains and bind each as a flex slot with N slice markers.
    row_render_dir.mkdir(parents=True, exist_ok=True)
    flex_slots = []
    for k in range(n):
        chain_seg = build_matrix_chain(input_audios, k, n)
        wav_path = row_render_dir / f'b{bank_idx + 1:02d}_chain{k:02d}.wav'
        export_wav(chain_seg, wav_path)
        slot = project.add_sample(str(wav_path.resolve()), slot_type='FLEX')
        set_equal_slices(project, slot, n,
                         segment_ms=segment_ms,
                         sample_rate=sample_rate)
        flex_slots.append(slot)

    # Bank/Part/Track 1 — flex, slice mode on. Default slot = chain 0
    # so that with no scene blending the first input plays through.
    bank = project.bank(bank_idx + 1)
    part = bank.part(1)
    t1 = part.audio_track(1)
    t1.configure_flex(flex_slots[0])
    t1.setup.slice = SliceMode.ON

    # Scenes drive the input axis. Scene A → input 0, Scene B → input N-1.
    # No per-trig slice_index lock; trigs inherit from the active scene.
    part.scene(1).track(1).slice_index = 0
    part.scene(2).track(1).slice_index = n - 1
    part.active_scene_a = 0
    part.active_scene_b = 1

    # Pattern — N trigs spaced at 16/N steps, each sample-locked to its
    # chain. The interval is exact only when n divides 16 (= the
    # ALLOWED_INPUT_COUNTS guarantee).
    interval = N_PATTERN_STEPS // n
    pattern = bank.pattern(1)
    pattern.scale_length = N_PATTERN_STEPS
    track = pattern.audio_track(1)
    active_steps = [k * interval + 1 for k in range(n)]
    track.active_steps = active_steps
    for k, step_num in enumerate(active_steps):
        step = track.step(step_num)
        step.sample_lock = flex_slots[k]


def build_project(export_path, name):
    payload, ctx = load_export(export_path, REQUIRED_CTX)
    if ctx['nSlices'] != N_SLICES:
        sys.exit(f'nSlices {ctx["nSlices"]} != {N_SLICES} (ot-doom assumes 16)')

    rows_in = [b for b in (payload.get('banks') or []) if b]
    if not rows_in:
        sys.exit('no non-empty rows in export')
    if len(rows_in) > 16:
        sys.exit(f'too many rows: {len(rows_in)} > 16')

    # Source-break manifest: download wavs into the shared sample cache
    # (also used by the existing octatrack target so they're not
    # re-downloaded across runs).
    manifest = fetch_sample_manifest(ctx['gistUser'], ctx['gistId'])
    cache_dir = SAMPLES_DIR / ctx['gistId']

    all_names = set()
    for cells in rows_in:
        for cell in cells:
            for break_name in cell['break']:
                all_names.add(break_name)
    missing = [n for n in all_names if n not in manifest]
    if missing:
        sys.exit(f'sample gist missing breaks: {missing}')
    paths = {n: cache_sample(n, manifest[n], cache_dir) for n in all_names}

    project = Project.from_template(name.upper()[:16])
    project.settings.tempo = float(ctx['bpm'])
    project.master_track = True

    # Source-slice cache shared across rows. The path map lives under a
    # reserved key so render_row can lazy-load on first reference per
    # row without changing the function signature.
    source_slice_cache = {'__paths__': paths}

    row_render_root = RENDER_DIR / name
    if row_render_root.exists():
        for p in row_render_root.rglob('*'):
            if p.is_file():
                p.unlink()

    for bank_idx, cells in enumerate(rows_in):
        render_row(
            project,
            bank_idx,
            cells,
            ctx['eventsPerCycle'],
            source_slice_cache,
            row_render_root / f'bank{bank_idx + 1:02d}',
        )

    return project


def render(export_path, name):
    project = build_project(export_path, name)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT_DIR / f'{name}.zip'
    project.to_zip(zip_path)
    return zip_path


def main():
    args = build_parser(__doc__.splitlines()[0]).parse_args()
    require_file(args.export)
    out = render(args.export, resolve_name(args))
    print(out)


if __name__ == '__main__':
    main()

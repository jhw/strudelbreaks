#!/usr/bin/env python3
"""Render a tempera captures JSON export into an Octatrack project zip.

Each row → one bank. Each cell → one pattern in that bank. Each pattern is a
1-bar / 16-step grid matching one Strudel cycle: the cell's pattern of
eventsPerCycle slice indices becomes eventsPerCycle trigs at every other
step (steps 1, 3, ..., 2N-1) with a FLEX sample_lock (break name) and
slice_index p-lock (pattern slice). OT pattern looping plays subsequent
cycles — equivalent to Strudel's per-cycle pattern repeat.

Samples referenced by the captures are fetched from the source gist
(`context.gistUser` / `context.gistId` → strudel.json) and cached under
tmp/samples/<gistId>/. Each sample slot gets 16 equal slice markers so the
slice_index p-locks resolve on-device.

Usage:
    python scripts/export/octatrack/render.py <path/to/export.json> [--name NAME] [--seed N]

Output:
    tmp/octatrack/<name>.zip
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from octapy import (
    Project,
    FX1Type,
    FX2Type,
    ScaleMode,
    SliceMode,
)

from common.cli import build_parser, require_file, resolve_name
from common.schema import load_export

# Source break wavs are 32 steps (2 bars at 1/16). N_SLICES=16 cuts them
# into 16 slices of 2 steps each, so a slice spans an 1/8 note plus the
# 1/16 immediately after — the off-grid ghost beat that gives breakbeats
# their swing. A finer slicing would split those ghosts off into their own
# slices and the OT pattern (1/8-note step grid) couldn't address them.
N_SLICES = 16
OT_PATTERN_STEPS = 16  # 1 bar at 1/16 per step — one Strudel cycle

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / 'tmp' / 'octatrack'
SAMPLES_DIR = REPO_ROOT / 'tmp' / 'samples'

REQUIRED_CTX = ('gistUser', 'gistId', 'bpm', 'eventsPerCycle', 'nSlices')


def fetch_sample_manifest(gist_user, gist_id):
    url = f'https://gist.githubusercontent.com/{gist_user}/{gist_id}/raw/strudel.json'
    with urllib.request.urlopen(url) as r:
        data = json.loads(r.read())
    # Strudel manifest: { "_base": "...", "name": url | [urls], ... }
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


def wav_info(path):
    with wave.open(str(path), 'rb') as w:
        return w.getnframes(), w.getframerate()


def expand_cell(break_names, pattern_idxs, events_per_cycle):
    """Expand a captured (break, pattern) cell to events_per_cycle (name, slice_idx|None) events.

    Break names come from Strudel's polymetric-stretch curly form
    `{a b c d}%N` — see STRUDEL.md ("Polymetric stretch") for the mapping.
    Pattern slice indices are positional `[i j k ...]`, one per event.
    Output is one Strudel cycle; OT pattern looping handles repeats.
    """
    events = []
    n_names = len(break_names)
    for pos in range(events_per_cycle):
        name = break_names[pos * n_names // events_per_cycle]
        slice_idx = pattern_idxs[pos] if pos < len(pattern_idxs) else None
        events.append((name, slice_idx))
    return events


def set_equal_slices(project, slot, n_slices, frame_count, sample_rate):
    total_ms = frame_count * 1000 / sample_rate
    slice_ms = total_ms / n_slices
    slices = [(int(i * slice_ms), int((i + 1) * slice_ms)) for i in range(n_slices)]
    slot_markers = project.markers.get_slot(slot, is_static=False)
    slot_markers.sample_length = frame_count
    slot_markers.set_slices_ms(slices, sample_rate=sample_rate)
    project.markers.set_slot(slot, slot_markers, is_static=False)


def collect_break_names(banks):
    names = []
    seen = set()
    for bank in banks:
        for cell in bank:
            for name in cell['break']:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


def build_project(export_path, name):
    payload, ctx = load_export(export_path, REQUIRED_CTX)
    if ctx['nSlices'] != N_SLICES:
        sys.exit(f'nSlices {ctx["nSlices"]} != {N_SLICES} (octatrack render assumes 16 slices)')

    banks_in = [b for b in (payload.get('banks') or []) if b]
    if not banks_in:
        sys.exit('no non-empty banks in export')
    if len(banks_in) > 16:
        sys.exit(f'too many banks: {len(banks_in)} > 16')
    for i, bank in enumerate(banks_in):
        if len(bank) > 16:
            sys.exit(f'bank {i} has {len(bank)} cells > 16')

    manifest = fetch_sample_manifest(ctx['gistUser'], ctx['gistId'])
    break_names = collect_break_names(banks_in)
    missing = [n for n in break_names if n not in manifest]
    if missing:
        sys.exit(f'sample gist missing breaks: {missing}')

    cache_dir = SAMPLES_DIR / ctx['gistId']
    local_paths = {n: cache_sample(n, manifest[n], cache_dir) for n in break_names}

    project = Project.from_template(name.upper()[:16])
    project.settings.tempo = float(ctx['bpm'])
    project.master_track = True

    # NOTE: source wavs are bundled at their native sample rate (the
    # strudel sample gist mixes 44.1 and 48 kHz). The OT plays back
    # assuming 44.1 kHz, so 48 kHz wavs run ~9% slow. In this target
    # each trig plays for ~1/8 note before being replaced, so the
    # drift doesn't accumulate audibly within a trig — but if trig
    # timing ever changes (e.g. doom-style longer holds), resample on
    # load to OT_SAMPLE_RATE the way ot-doom/audio.py does.
    # See OCTATRACK.md for the constraint.
    flex_slots = {}
    for n in break_names:
        path = local_paths[n]
        slot = project.add_sample(str(path.resolve()), slot_type='FLEX')
        flex_slots[n] = slot
        frames, sr = wav_info(path)
        set_equal_slices(project, slot, N_SLICES, frames, sr)

    default_slot = flex_slots[break_names[0]]

    for bank_idx, bank_cells in enumerate(banks_in):
        bank = project.bank(bank_idx + 1)
        part = bank.part(1)

        t1 = part.audio_track(1)
        t1.configure_flex(default_slot)
        t1.setup.slice = SliceMode.ON
        t1.fx2_type = FX2Type.DELAY
        t1.fx2.send = 64

        t8 = part.audio_track(8)
        t8.fx1_type = FX1Type.CHORUS
        t8.fx1.mix = 64

        for cell_idx, cell in enumerate(bank_cells):
            pattern = bank.pattern(cell_idx + 1)
            pattern.scale_mode = ScaleMode.NORMAL
            pattern.scale_length = OT_PATTERN_STEPS

            events = expand_cell(
                cell['break'], cell['pattern'],
                ctx['eventsPerCycle'],
            )
            track = pattern.audio_track(1)
            active = [2 * i + 1 for i, (_, s) in enumerate(events) if s is not None]
            track.active_steps = active
            for i, (name, slice_idx) in enumerate(events):
                if slice_idx is None:
                    continue
                step = track.step(2 * i + 1)
                step.sample_lock = flex_slots[name]
                step.slice_index = slice_idx

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

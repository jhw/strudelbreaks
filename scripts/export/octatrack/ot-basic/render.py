#!/usr/bin/env python3
"""Render a tempera captures JSON export into an Octatrack project zip.

Each row → one bank. Each cell → one pattern in that bank. Each pattern is a
1-bar / 16-step grid matching one Strudel cycle: the cell's pattern of
eventsPerCycle slice indices becomes eventsPerCycle trigs at every other
step (steps 1, 3, ..., 2N-1) on each of T1/T2/T3, with a FLEX
sample_lock (per-track break stem) and slice_index p-lock (pattern
slice). OT pattern looping plays subsequent cycles.

Per-track design: each break is rendered as three drum stems (kick /
snare / hat) via beatwav. Each cell's trig at step `s` fires
identically on T1, T2, T3 — same `slice_index`, distinct
`sample_lock` per track — so muting / EQ / compression can shape each
kit piece independently on the device.

FX layout (configured once on part 1):
  T1, T2, T3: FX1 = DJ_EQ, FX2 = COMPRESSOR
  T8:         FX1 = CHORUS,  FX2 = DELAY        (mix = 64 each)

Samples referenced by the captures are fetched from the source gist
(`context.gistUser` / `context.gistId` → strudel.json) and re-rendered
per drum stem at OT_SAMPLE_RATE via beatwav. Cached under
tmp/samples/<gistId>/. Each per-stem flex slot gets 16 equal slice
markers so the slice_index p-locks resolve on-device.

Usage:
    python scripts/export/octatrack/ot-basic/render.py <path/to/export.json> [--name NAME] [--seed N]

Output:
    tmp/ot-basic/<name>.zip
"""
from __future__ import annotations

import pathlib
import sys
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from octapy import (
    Project,
    FX1Type,
    FX2Type,
    ScaleMode,
    SliceMode,
    TrigCondition,
)

from common import sample_source
from common.cli import build_parser, require_file, resolve_name
from common.devices import OT_SAMPLE_RATE
from common.schema import load_export

# Per-drum stems we ask beatwav to produce. Maps to OT audio tracks
# 1/2/3 in trig order.
TRACKS = ('kick', 'snare', 'hat')

# Source break wavs are 32 steps (2 bars at 1/16). N_SLICES=16 cuts them
# into 16 slices of 2 steps each, so a slice spans an 1/8 note plus the
# 1/16 immediately after — the off-grid ghost beat that gives breakbeats
# their swing. A finer slicing would split those ghosts off into their own
# slices and the OT pattern (1/8-note step grid) couldn't address them.
N_SLICES = 16
OT_PATTERN_STEPS = 16  # 1 bar at 1/16 per step — one Strudel cycle

# Wet/dry value for the T8 send/master FX (CHORUS, DELAY). 64 ≈ 50%
# on the OT 0-127 parameter scale. The two effects use different
# parameter names for the wet control: CHORUS exposes .mix, DELAY
# exposes .send.
T8_FX_LEVEL = 64

# Octatrack trig probability buckets (TrigCondition.PERCENT_*). The OT
# can only express the discrete values listed below; arbitrary
# probabilities snap to the nearest bucket. p == 1.0 leaves the
# condition unset, which is the OT default ("always fires"). The
# alternative — setting PERCENT_99 for p == 1.0 — would introduce a
# 1% miss rate, which is not what the user asked for.
PROBABILITY_BUCKETS = [
    (1, TrigCondition.PERCENT_1),  (2, TrigCondition.PERCENT_2),
    (4, TrigCondition.PERCENT_4),  (6, TrigCondition.PERCENT_6),
    (9, TrigCondition.PERCENT_9),  (13, TrigCondition.PERCENT_13),
    (19, TrigCondition.PERCENT_19), (25, TrigCondition.PERCENT_25),
    (33, TrigCondition.PERCENT_33), (41, TrigCondition.PERCENT_41),
    (50, TrigCondition.PERCENT_50), (59, TrigCondition.PERCENT_59),
    (67, TrigCondition.PERCENT_67), (75, TrigCondition.PERCENT_75),
    (81, TrigCondition.PERCENT_81), (87, TrigCondition.PERCENT_87),
    (91, TrigCondition.PERCENT_91), (94, TrigCondition.PERCENT_94),
    (96, TrigCondition.PERCENT_96), (98, TrigCondition.PERCENT_98),
    (99, TrigCondition.PERCENT_99),
]

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / 'tmp' / 'ot-basic'

REQUIRED_CTX = ('gistUser', 'gistId', 'bpm', 'eventsPerCycle', 'nSlices')


def wav_info(path):
    with wave.open(str(path), 'rb') as w:
        return w.getnframes(), w.getframerate()


def probability_to_condition(p):
    """Map a 0..1 probability to a TrigCondition, or None for "always fires".

    p == 1.0 → None (no condition; OT default fires every loop).
    p ∈ [0, 1) → nearest available PERCENT_* bucket.
    Out of range → ValueError.
    """
    if not 0.0 <= p <= 1.0:
        raise ValueError(f'probability must be in [0, 1], got {p}')
    if p == 1.0:
        return None
    pct = p * 100
    return min(PROBABILITY_BUCKETS, key=lambda b: abs(b[0] - pct))[1]


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


def configure_track_fx(part):
    """Set FX layout on part 1 once per bank.

    T1/T2/T3 carry the per-stem playback. Each gets DJ_EQ on FX1 and
    COMPRESSOR on FX2 — independent EQ + dynamics per kit piece.
    T8 hosts CHORUS (FX1) and DELAY (FX2) at mix=64 as the
    project-level send chain.
    """
    for track_num in (1, 2, 3):
        t = part.audio_track(track_num)
        t.fx1_type = FX1Type.DJ_EQ
        t.fx2_type = FX2Type.COMPRESSOR

    t8 = part.audio_track(8)
    t8.fx1_type = FX1Type.CHORUS
    t8.fx1.mix = T8_FX_LEVEL    # CHORUS: wet/dry on .mix
    t8.fx2_type = FX2Type.DELAY
    t8.fx2.send = T8_FX_LEVEL   # DELAY: wet level on .send (no .mix here)


def build_project(export_path, name, probability=1.0):
    trig_condition = probability_to_condition(probability)
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

    break_names = collect_break_names(banks_in)
    # Per-stem rendering: returns {name: {kick: path, snare: path, hat: path}}.
    # JSON-source only — gist's pre-mixed WAVs can't be split into stems.
    stem_paths = sample_source.resolve_break_paths(
        gist_user=ctx['gistUser'],
        gist_id=ctx['gistId'],
        names=break_names,
        source='json',
        target_bpm=ctx['bpm'],
        target_sample_rate=OT_SAMPLE_RATE,
        tracks=TRACKS,
    )

    project = Project.from_template(name.upper()[:16])
    project.settings.tempo = float(ctx['bpm'])
    project.master_track = True

    # Each break gets one flex slot per drum stem. Slot count grows 3×
    # vs the old mixed-stem design — small, well under the 128-slot
    # ceiling for tempera-realistic break counts.
    flex_slots = {}  # {(name, track): slot}
    for n in break_names:
        for track in TRACKS:
            path = stem_paths[n][track]
            slot = project.add_sample(str(path.resolve()), slot_type='FLEX')
            flex_slots[(n, track)] = slot
            frames, sr = wav_info(path)
            set_equal_slices(project, slot, N_SLICES, frames, sr)

    # Default flex slot per track — only relevant if a step has no
    # sample_lock, which our patterns never produce; we pick the first
    # break's stem for symmetry across the three tracks.
    default_slot_per_track = {
        track: flex_slots[(break_names[0], track)] for track in TRACKS
    }

    for bank_idx, bank_cells in enumerate(banks_in):
        bank = project.bank(bank_idx + 1)
        part = bank.part(1)

        for track_idx, track in enumerate(TRACKS):
            t = part.audio_track(track_idx + 1)
            t.configure_flex(default_slot_per_track[track])
            t.setup.slice = SliceMode.ON
        configure_track_fx(part)

        for cell_idx, cell in enumerate(bank_cells):
            pattern = bank.pattern(cell_idx + 1)
            pattern.scale_mode = ScaleMode.NORMAL
            pattern.scale_length = OT_PATTERN_STEPS

            events = expand_cell(
                cell['break'], cell['pattern'],
                ctx['eventsPerCycle'],
            )
            active = [2 * i + 1 for i, (_, s) in enumerate(events) if s is not None]

            for track_idx, track in enumerate(TRACKS):
                pattern_track = pattern.audio_track(track_idx + 1)
                pattern_track.active_steps = active
                for i, (n, slice_idx) in enumerate(events):
                    if slice_idx is None:
                        continue
                    step = pattern_track.step(2 * i + 1)
                    step.sample_lock = flex_slots[(n, track)]
                    step.slice_index = slice_idx
                    if trig_condition is not None:
                        step.condition = trig_condition

    return project


def render(export_path, name, probability=1.0):
    project = build_project(export_path, name, probability=probability)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT_DIR / f'{name}.zip'
    project.to_zip(zip_path)
    return zip_path


def main():
    parser = build_parser(__doc__.splitlines()[0])
    parser.add_argument('--probability', type=float, default=1.0,
                        help='per-trig probability in [0, 1] (default 1.0 = always fires); '
                             'snaps to the nearest OT trig-condition bucket')
    args = parser.parse_args()
    require_file(args.export)
    out = render(args.export, resolve_name(args), probability=args.probability)
    print(out)


if __name__ == '__main__':
    main()

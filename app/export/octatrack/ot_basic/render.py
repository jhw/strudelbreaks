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

Imported and called by the FastAPI server (`app.exporters`) — no CLI;
the server writes the captures payload to a temp file and points
`output_dir` at a temp dir.

Output:
    <output_dir>/<name>.zip   (defaults to tmp/ot-basic/)
"""
from __future__ import annotations

import pathlib
import sys
import wave

from octapy import (
    Project,
    FX1Type,
    FX2Type,
    ScaleMode,
    SliceMode,
    TrigCondition,
)

from app.export.common import sample_source
from app.export.common.devices import OT_SAMPLE_RATE
from app.export.common.schema import load_export
from app.export.octatrack._flatten import flatten_cells, regroup_basic

# Per-drum stems we ask beatwav to produce in split mode. Maps to OT
# audio tracks 1/2/3 in trig order. Mixed mode renders one combined
# sample per break and uses T1 only — useful for an A/B fidelity
# check against the Strudel source.
TRACKS = ('kick', 'snare', 'hat')
MIXED_STEM = 'mixed'


def _stem_tracks(split_stems):
    """OT audio-track stems used for this render."""
    return TRACKS if split_stems else (MIXED_STEM,)

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

# DELAY feedback (encoder B on the FX2 page; see octapy
# `FX_PARAM_NAMES[FX2Type.DELAY]`). 32 = ¼ of the 0-127 range —
# audible repeats without runaway tail.
T8_DELAY_FEEDBACK = 32

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

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
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


def flex_track_nums(n_stems, neighbour):
    """OT track numbers each stem plays from.

    Without neighbour: stems on T1, T2, T3 (or T1 alone in mixed mode).
    With neighbour: every flex track gets a paired neighbour machine
    on the next track (T2/T4/T6) for an extra two FX, so stems move to
    T1, T3, T5 instead.
    """
    step = 2 if neighbour else 1
    return tuple(1 + i * step for i in range(n_stems))


def neighbour_track_nums(flex_tracks):
    """Each flex track's neighbour partner — flex track + 1."""
    return tuple(t + 1 for t in flex_tracks)


def configure_track_fx(part, flex_tracks, *, neighbour=False):
    """Set FX layout on part 1 once per bank.

    Each enabled flex track gets DJ_EQ on FX1 and COMPRESSOR on FX2 —
    independent EQ + dynamics per kit piece in split mode, or one
    shaping chain in mixed mode.

    Without neighbour: T8 hosts CHORUS + DELAY at mix=64 as the
    project-level send chain (the legacy layout).

    With neighbour: every flex track is paired with a neighbour
    machine on the next track (DELAY on FX1, FILTER on FX2 — two more
    FX in series). T8's send chain drops the delay (now per-track) and
    keeps just the spatializer.
    """
    for track_num in flex_tracks:
        t = part.audio_track(track_num)
        t.fx1_type = FX1Type.DJ_EQ
        t.fx2_type = FX2Type.COMPRESSOR

    if neighbour:
        # Neighbour machine sits next to its flex track and chains 2
        # more FX onto that track's audio (FILTER + DELAY here). T8's
        # send chain drops to spatializer-only — delay moved to the
        # neighbour tracks.
        for nb_num in neighbour_track_nums(flex_tracks):
            nb = part.audio_track(nb_num)
            nb.configure_neighbor()
            nb.fx1_type = FX1Type.FILTER
            nb.fx2_type = FX2Type.DELAY
            nb.fx2.send = T8_FX_LEVEL
            nb.fx2.feedback = T8_DELAY_FEEDBACK
        t8 = part.audio_track(8)
        t8.fx1_type = FX1Type.SPATIALIZER
        t8.fx2_type = FX2Type.OFF
    else:
        t8 = part.audio_track(8)
        t8.fx1_type = FX1Type.CHORUS
        t8.fx1.mix = T8_FX_LEVEL    # CHORUS: wet/dry on .mix
        t8.fx2_type = FX2Type.DELAY
        t8.fx2.send = T8_FX_LEVEL          # DELAY: wet level on .send (no .mix here)
        t8.fx2.feedback = T8_DELAY_FEEDBACK  # encoder B — see T8_DELAY_FEEDBACK


def _resolve_stem_paths(*, gist_user, gist_id, names, target_bpm, stem_tracks):
    """Fetch break audio in the layout the renderer expects:
    `{name: {stem: path}}`. Split mode returns the per-track JSON
    render; mixed mode returns one combined sample per break."""
    if stem_tracks == (MIXED_STEM,):
        flat = sample_source.resolve_break_paths(
            gist_user=gist_user,
            gist_id=gist_id,
            names=names,
            source='json',
            target_bpm=target_bpm,
            target_sample_rate=OT_SAMPLE_RATE,
        )
        return {n: {MIXED_STEM: p} for n, p in flat.items()}
    return sample_source.resolve_break_paths(
        gist_user=gist_user,
        gist_id=gist_id,
        names=names,
        source='json',
        target_bpm=target_bpm,
        target_sample_rate=OT_SAMPLE_RATE,
        tracks=stem_tracks,
    )


def build_project(export_path, name, probability=1.0,
                  split_stems=True, flatten=False, neighbour=False):
    trig_condition = probability_to_condition(probability)
    payload, ctx = load_export(export_path, REQUIRED_CTX)
    if ctx['nSlices'] != N_SLICES:
        sys.exit(f'nSlices {ctx["nSlices"]} != {N_SLICES} (octatrack render assumes 16 slices)')

    banks_in = [b for b in (payload.get('banks') or []) if b]
    if not banks_in:
        sys.exit('no non-empty banks in export')
    if flatten:
        # Collapse list-of-lists into a flat cell list, then re-pack
        # into 16-cell banks. The total cell count caps at 16×16 = 256.
        banks_in = regroup_basic(flatten_cells(banks_in))
        if not banks_in:
            sys.exit('flatten produced no cells')
    if len(banks_in) > 16:
        sys.exit(f'too many banks: {len(banks_in)} > 16')
    for i, bank in enumerate(banks_in):
        if len(bank) > 16:
            sys.exit(f'bank {i} has {len(bank)} cells > 16')

    stem_tracks = _stem_tracks(split_stems)
    flex_tracks = flex_track_nums(len(stem_tracks), neighbour)

    break_names = collect_break_names(banks_in)
    stem_paths = _resolve_stem_paths(
        gist_user=ctx['gistUser'],
        gist_id=ctx['gistId'],
        names=break_names,
        target_bpm=ctx['bpm'],
        stem_tracks=stem_tracks,
    )

    project = Project.from_template(name.upper()[:16])
    project.settings.tempo = float(ctx['bpm'])
    project.master_track = True

    # Split mode: 3 flex slots per break (kick/snare/hat), one per OT
    # track. Mixed mode: 1 slot per break used by T1 only.
    flex_slots = {}  # {(name, stem): slot}
    for n in break_names:
        for stem in stem_tracks:
            path = stem_paths[n][stem]
            slot = project.add_sample(str(path.resolve()), slot_type='FLEX')
            flex_slots[(n, stem)] = slot
            frames, sr = wav_info(path)
            set_equal_slices(project, slot, N_SLICES, frames, sr)

    # Default flex slot per track — only relevant if a step has no
    # sample_lock, which our patterns never produce; we pick the first
    # break's stem so the default sound matches the rest of the kit.
    default_slot_per_track = {
        stem: flex_slots[(break_names[0], stem)] for stem in stem_tracks
    }

    for bank_idx, bank_cells in enumerate(banks_in):
        bank = project.bank(bank_idx + 1)
        part = bank.part(1)

        for track_num, stem in zip(flex_tracks, stem_tracks):
            t = part.audio_track(track_num)
            t.configure_flex(default_slot_per_track[stem])
            t.setup.slice = SliceMode.ON
        configure_track_fx(part, flex_tracks, neighbour=neighbour)

        for cell_idx, cell in enumerate(bank_cells):
            pattern = bank.pattern(cell_idx + 1)
            pattern.scale_mode = ScaleMode.NORMAL
            pattern.scale_length = OT_PATTERN_STEPS

            events = expand_cell(
                cell['break'], cell['pattern'],
                ctx['eventsPerCycle'],
            )
            active = [2 * i + 1 for i, (_, s) in enumerate(events) if s is not None]

            for track_num, stem in zip(flex_tracks, stem_tracks):
                pattern_track = pattern.audio_track(track_num)
                pattern_track.active_steps = active
                for i, (n, slice_idx) in enumerate(events):
                    if slice_idx is None:
                        continue
                    step = pattern_track.step(2 * i + 1)
                    step.sample_lock = flex_slots[(n, stem)]
                    step.slice_index = slice_idx
                    if trig_condition is not None:
                        step.condition = trig_condition

    return project


def render(export_path, name, *, probability=1.0,
           split_stems=True, flatten=False, neighbour=False,
           output_dir=None):
    project = build_project(export_path, name,
                            probability=probability,
                            split_stems=split_stems,
                            flatten=flatten,
                            neighbour=neighbour)
    out_dir = pathlib.Path(output_dir) if output_dir is not None else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f'{name}.zip'
    project.to_zip(zip_path)
    return zip_path

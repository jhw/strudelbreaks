"""Audio rendering for the torso-s4 export.

Each captured cell holds a Strudel pattern: `eventsPerCycle` slice
indices played through a polymetric break vocabulary. We render each
cell directly to a 1-bar audio segment by walking the events, picking
the source-break slice for that event, and concatenating.

A row is then a plain concat of its cells.

No fades anywhere — Strudel doesn't apply per-event fades, and the
asymmetric envelope used to ship here introduced an audible periodic
loudness dip relative to Strudel playback. Same reasoning as
ot-doom/audio.py: if pathological patterns surface clicks at slice
boundaries, reintroduce a sub-perceptual envelope (≤ 0.5 ms) inside
`render_cell` only.

All rendered output ships at 96 kHz, the S-4's max-supported sample
rate per the manual; see TORSO-S4.md for the rationale and tradeoffs.
Source breaks come from the strudel gist at mixed 44.1/48 kHz; we
upsample on load so every chunk downstream is at one consistent rate.

Event boundaries are computed cumulatively from the bar length so
fractional event_ms (e.g. 234.375 at 128 BPM × 8 events) doesn't
accumulate per-event rounding error across a multi-cell row. The
total length of N concatenated events is therefore exactly
`round(N * event_ms)`, not `N * round(event_ms)`.
"""
from __future__ import annotations

import pathlib
from typing import Dict, List, Optional

from pydub import AudioSegment


# Torso S-4 native ceiling; everything we ship lands here. See
# TORSO-S4.md for why we pin this rather than passing source rates
# through. The S-4 will resample any input ≤ 96 kHz on its own, but
# pinning here keeps the export reproducible regardless of which
# source-break wavs the gist happens to host.
S4_SAMPLE_RATE = 96000


def load_break(path: pathlib.Path) -> AudioSegment:
    """Load a WAV at the S-4-target sample rate."""
    seg = AudioSegment.from_wav(str(path))
    if seg.frame_rate != S4_SAMPLE_RATE:
        seg = seg.set_frame_rate(S4_SAMPLE_RATE)
    return seg


def equal_slices(seg: AudioSegment, n_slices: int) -> List[AudioSegment]:
    """Cut `seg` into `n_slices` equal-ms chunks; the last chunk runs
    to end-of-segment so the trailing remainder isn't dropped to
    integer division."""
    total_ms = len(seg)
    step = total_ms / n_slices
    out = []
    for i in range(n_slices):
        start = int(round(i * step))
        end = total_ms if i == n_slices - 1 else int(round((i + 1) * step))
        out.append(seg[start:end])
    return out


def render_cell(
    source_slices: Dict[str, List[AudioSegment]],
    break_names: List[str],
    pattern_idxs: List[Optional[int]],
    event_ms: float,
) -> AudioSegment:
    """Render one captured cell to a 1-bar AudioSegment.

    Args:
        source_slices: break name → list of equal slices of that break's wav.
        break_names: the cell's polymetric break vocabulary (length M).
        pattern_idxs: slice index per event (length N = eventsPerCycle);
            None denotes a rest.
        event_ms: target length of one event slot, **as a float**. Per-event
            integer-ms boundaries are computed cumulatively so the total
            cell length equals `round(N * event_ms)` exactly — no
            rounding drift across long rows.

    Returns:
        AudioSegment of length `round(len(pattern_idxs) * event_ms)` ms.
    """
    n_events = len(pattern_idxs)
    m = len(break_names)
    rate = _anchor_frame_rate(source_slices)
    out = AudioSegment.empty()
    cum = 0
    for i, slice_idx in enumerate(pattern_idxs):
        next_cum = int(round((i + 1) * event_ms))
        this_event_ms = next_cum - cum
        cum = next_cum
        # Polymetric stretch i*M//N — see STRUDEL.md.
        name = break_names[i * m // n_events]
        if slice_idx is None:
            seg = AudioSegment.silent(duration=this_event_ms, frame_rate=rate)
        else:
            seg = source_slices[name][slice_idx]
        seg = _fit_to_ms(seg, this_event_ms)
        out += seg
    return out


def render_row(cells: List[AudioSegment]) -> AudioSegment:
    """Concat already-rendered cells into a single row segment."""
    out = AudioSegment.empty()
    for c in cells:
        out += c
    return out


def export_wav(seg: AudioSegment, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seg.export(str(path), format='wav')


def _fit_to_ms(seg: AudioSegment, target_ms: int) -> AudioSegment:
    diff = target_ms - len(seg)
    if diff == 0:
        return seg
    if diff > 0:
        return seg + AudioSegment.silent(duration=diff, frame_rate=seg.frame_rate)
    return seg[:target_ms]


def _anchor_frame_rate(source_slices: Dict[str, List[AudioSegment]]) -> int:
    for slices in source_slices.values():
        if slices:
            return slices[0].frame_rate
    return S4_SAMPLE_RATE

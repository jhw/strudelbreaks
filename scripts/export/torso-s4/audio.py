"""Audio rendering for the torso-s4 export.

Each captured cell holds a Strudel pattern: `eventsPerCycle` slice
indices played through a polymetric break vocabulary. We render each
cell directly to a 1-bar audio segment by walking the events, picking
the source-break slice for that event, and concatenating with fades.

A row is then a concat of its cells (no boundary fading needed —
each cell already ends with a fade-out from its last event and the
next cell starts with a fade-in on its first event).

Asymmetric fade envelope, identical to ot-doom (planning doc covers
the rationale): 1 ms fade-in (pure click guard, sub-perceptual on
attacks) and 3 ms fade-out (long enough to mute the discontinuity
into the next slice, inaudible on a mid-decay tail).
"""
from __future__ import annotations

import pathlib
from typing import Dict, List, Optional

from pydub import AudioSegment


FADE_IN_MS = 1
FADE_OUT_MS = 3


def load_break(path: pathlib.Path) -> AudioSegment:
    return AudioSegment.from_wav(str(path))


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
    event_ms: int,
) -> AudioSegment:
    """Render one captured cell to a 1-bar AudioSegment.

    Args:
        source_slices: break name → list of equal slices of that break's wav.
        break_names: the cell's polymetric break vocabulary (length M).
        pattern_idxs: slice index per event (length N = eventsPerCycle);
            None denotes a rest.
        event_ms: target length of one event slot. Source slices that
            come back fractionally off (rounding) are padded/trimmed.

    Returns:
        AudioSegment of length `len(pattern_idxs) * event_ms` ms.
    """
    n_events = len(pattern_idxs)
    m = len(break_names)
    rate = _anchor_frame_rate(source_slices)
    out = AudioSegment.empty()
    for i, slice_idx in enumerate(pattern_idxs):
        # Polymetric stretch i*M//N — see STRUDEL.md.
        name = break_names[i * m // n_events]
        if slice_idx is None:
            seg = AudioSegment.silent(duration=event_ms, frame_rate=rate)
        else:
            seg = source_slices[name][slice_idx]
        seg = _fit_to_ms(seg, event_ms)
        seg = seg.fade_in(FADE_IN_MS).fade_out(FADE_OUT_MS)
        out += seg
    return out


def render_row(cells: List[AudioSegment]) -> AudioSegment:
    """Concat already-rendered cells into a single row segment.

    Each cell's last event already carries a fade-out, and the next
    cell's first event has a fade-in, so cell↔cell boundaries are
    already smoothed by the per-event envelope — no extra
    crossfade needed.
    """
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
    return 44100

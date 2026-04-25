"""Audio rendering for ot-doom: source break wavs → 16 timesliced wavs.

The shipped renderer is a *one-pass* mapping: for each pattern step `i`
and each break-position `j ∈ 0..15`, the timesliced wav at step `i`
holds, in order, `source_slice(B'[j], pattern_idxs[i])`. There is no
intermediate "output break" file — see docs/planning/ot-doom.md
("One pass, not two") for the algebra.

Asymmetric fade envelope on every concatenated sub-slice:
- fade-out 3 ms cosine ramp (tail is mid-decay; long enough to bury
  the discontinuity into the next slice's transient, short enough to
  be inaudible on the dying tail).
- fade-in 1 ms cosine ramp (head is where the attack lives; pydub's
  minimum-resolution fade, ~44 samples at 44.1 kHz, is a pure click
  guard rather than an attack-shaper).

Rests (`pattern_idxs[i] is None`) become silence segments of one
slice's length. Fades on silence are no-ops, so there's no special
case.
"""
from __future__ import annotations

import pathlib
from typing import Dict, List, Optional

from pydub import AudioSegment


FADE_IN_MS = 1
FADE_OUT_MS = 3


def load_break(path: pathlib.Path) -> AudioSegment:
    """Load a WAV as an AudioSegment. Caller may cache by path."""
    return AudioSegment.from_wav(str(path))


def equal_slices(seg: AudioSegment, n_slices: int) -> List[AudioSegment]:
    """Cut `seg` into `n_slices` equal-millisecond chunks.

    The last chunk is extended to end-of-segment so we never lose the
    trailing remainder to integer division — important because the OT
    end-point on the source side is part of the audio we want to
    preserve when re-slicing on the device.
    """
    total_ms = len(seg)
    step = total_ms / n_slices
    out = []
    for i in range(n_slices):
        start = int(round(i * step))
        end = total_ms if i == n_slices - 1 else int(round((i + 1) * step))
        out.append(seg[start:end])
    return out


def render_timesliced_step(
    source_slices: Dict[str, List[AudioSegment]],
    b_prime: List[str],
    pattern_idx: Optional[int],
    slice_ms: int,
) -> AudioSegment:
    """Build one timesliced wav: concat over j of B'[j]'s source slice.

    Args:
        source_slices: name -> 16 equal slices of that break's wav.
        b_prime: length-16 list of break names (the padded source set).
        pattern_idx: the captured Strudel slice index for this step,
            or None for a rest.
        slice_ms: target slice length in ms; sub-slices that come back
            slightly off (rounding from equal_slices) are pad/trimmed
            to this so all 16 sub-slices land at exact OT sub-slice
            boundaries.

    Returns:
        AudioSegment of length `16 * slice_ms` ms.
    """
    if pattern_idx is None:
        sub = AudioSegment.silent(duration=slice_ms,
                                  frame_rate=_anchor_frame_rate(source_slices))
        # silent already has zero attack/tail; fades are no-ops, but we
        # apply them anyway so every sub-slice goes through the same
        # envelope path.
        sub = sub.fade_in(FADE_IN_MS).fade_out(FADE_OUT_MS)
        return sub * 16

    out = AudioSegment.empty()
    for j in range(16):
        name = b_prime[j]
        sub = source_slices[name][pattern_idx]
        sub = _fit_to_ms(sub, slice_ms)
        sub = sub.fade_in(FADE_IN_MS).fade_out(FADE_OUT_MS)
        out += sub
    return out


def _fit_to_ms(seg: AudioSegment, target_ms: int) -> AudioSegment:
    """Pad with silence or trim so `seg` is exactly `target_ms` long."""
    diff = target_ms - len(seg)
    if diff == 0:
        return seg
    if diff > 0:
        return seg + AudioSegment.silent(duration=diff,
                                         frame_rate=seg.frame_rate)
    return seg[:target_ms]


def _anchor_frame_rate(source_slices: Dict[str, List[AudioSegment]]) -> int:
    """Pick a frame_rate from the loaded sources so silence segments
    match. All sources share a rate in our pipeline — we just grab
    the first."""
    for slices in source_slices.values():
        if slices:
            return slices[0].frame_rate
    return 44100


def export_wav(seg: AudioSegment, path: pathlib.Path) -> None:
    """Write `seg` to a 16-bit WAV at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seg.export(str(path), format='wav')

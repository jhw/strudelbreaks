"""Audio rendering for ot-doom: source break wavs → per-cell bar audio,
then matrix chains across the cells of a row.

See docs/export/ot-doom.md for the design — short version: every cell
in a row renders to one bar of audio, then chain[k] is the k-th equal
segment of every cell concatenated. The crossfader walks slice_index
0..N-1 across all chains, which by construction picks "input k played
in full".

No fades anywhere — Strudel doesn't apply per-event or per-segment
fades and the OT side should match it. If pathological patterns cause
audible pops at slice boundaries, reintroduce a sub-perceptual envelope
(0.3 ms / 0.5 ms) at `render_cell_audio` only — never at the
matrix-chain stage, where boundaries already lie inside whatever
envelope `render_cell_audio` produced.

Rests (`pattern[i] is None`) become silence segments of one source-slice's
length.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Dict, List

from pydub import AudioSegment

# Cross-subdir import: scripts/export/ on path so we can pull the
# shared device-rate constant. Mirrors the trick render.py uses.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.devices import OT_SAMPLE_RATE  # noqa: E402

# Octatrack plays back assuming OT_SAMPLE_RATE (44100 Hz); a 48 kHz
# source plays at ~91.9% speed (= 44100/48000) and sounds "laggy".
# The strudel sample gist mixes 44.1 and 48 kHz wavs, so we
# force-resample on load. See docs/export/octatrack.md for the full
# constraint list.


def load_break(path: pathlib.Path) -> AudioSegment:
    """Load a WAV at the OT-native sample rate. Caller may cache by path."""
    seg = AudioSegment.from_wav(str(path))
    if seg.frame_rate != OT_SAMPLE_RATE:
        seg = seg.set_frame_rate(OT_SAMPLE_RATE)
    return seg


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


def render_cell_audio(
    cell: dict,
    source_slices: Dict[str, List[AudioSegment]],
    events_per_cycle: int,
) -> AudioSegment:
    """Render one captured cell to one bar of audio.

    `cell.break` is the captured curly-form name list (length M). It's
    polymetric-stretched onto `events_per_cycle` events: position i →
    name index `i * M // events_per_cycle`. `cell.pattern` is the
    captured slice index per event (or None for a rest).

    For each event: append the corresponding source slice (or a slice's
    worth of silence on rest), with the asymmetric fade envelope.
    """
    break_names = cell['break']
    m = len(break_names)
    pattern = cell['pattern']

    # Anchor slice length and frame rate from the first source we hit;
    # the source-prep pipeline guarantees they're consistent.
    anchor_name = break_names[0]
    anchor_slice = source_slices[anchor_name][0]
    slice_ms = len(anchor_slice)
    frame_rate = anchor_slice.frame_rate

    out = AudioSegment.empty()
    for i in range(events_per_cycle):
        slice_idx = pattern[i] if i < len(pattern) else None
        if slice_idx is None:
            piece = AudioSegment.silent(duration=slice_ms, frame_rate=frame_rate)
        else:
            name = break_names[i * m // events_per_cycle]
            piece = source_slices[name][slice_idx]
            piece = _fit_to_ms(piece, slice_ms)
        out += piece
    return out


def build_matrix_chain(input_audios: List[AudioSegment], k: int, n: int) -> AudioSegment:
    """Build chain[k] = segment_k of every input concatenated.

    Each input is sliced into `n` equal segments; chain[k] picks the
    k-th segment from every input. No fades — segment boundaries lie
    inside whatever envelope `render_cell_audio` produced and any
    extra here would just double-attenuate.
    """
    bar_ms = len(input_audios[0])
    seg_ms = bar_ms / n
    start_ms = int(round(k * seg_ms))
    end_ms = int(round((k + 1) * seg_ms))
    out = AudioSegment.empty()
    for inp in input_audios:
        out += inp[start_ms:end_ms]
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


def export_wav(seg: AudioSegment, path: pathlib.Path) -> None:
    """Write `seg` to a 16-bit WAV at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seg.export(str(path), format='wav')

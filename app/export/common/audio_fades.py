"""Per-event fade envelope shared by ot-doom and torso-s4 cell rendering.

Both targets concatenate event-length pieces into a 1-bar cell. Without
any envelope, abrupt amplitude jumps at event boundaries produce
audible clicks. The asymmetric default (1 ms in / 2 ms out) preserves
attack while suppressing the trailing-edge discontinuity that's
audibly worse on drum tails.

Apply this **only** at the event-concat stage. Fades at chain assembly
(`build_matrix_chain`, `_build_track_chain`, `render_row`) cut at
arbitrary points inside an already-faded event and would either
double-attenuate the boundary or punch a brief volume dip into the
middle of a sample's body.
"""
from __future__ import annotations

from pydub import AudioSegment


DEFAULT_FADE_IN_MS = 1
DEFAULT_FADE_OUT_MS = 2


def apply_envelope(seg: AudioSegment, *,
                   fade_in_ms: int = DEFAULT_FADE_IN_MS,
                   fade_out_ms: int = DEFAULT_FADE_OUT_MS) -> AudioSegment:
    """Apply asymmetric fade-in / fade-out to one event-length piece.

    `0` disables the corresponding fade. The envelope is clamped at
    half the segment length so a very short piece doesn't try to fade
    past its own midpoint and end up silent.
    """
    if len(seg) == 0:
        return seg
    cap = max(0, len(seg) // 2)
    fi = max(0, min(int(fade_in_ms), cap))
    fo = max(0, min(int(fade_out_ms), cap))
    if fi:
        seg = seg.fade_in(fi)
    if fo:
        seg = seg.fade_out(fo)
    return seg

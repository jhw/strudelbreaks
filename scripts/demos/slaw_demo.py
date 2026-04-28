#!/usr/bin/env python
"""scripts/demos/slaw_demo.py — minimal OT projects to expose the
ot-doom crossfader skew.

Goal: reproduce the skew with the simplest possible audio so we
can decide whether the misalignment is in our chain rendering or
in the device's firmware. The chain layout is the same as
ot-doom's split_stems=False mode, but the source audio is just
four pure sine tones — easy to hear which slice is playing.

We emit three variants with increasing slice density (4 / 8 / 16)
so we can compare how the fader walks through the slice pool at
each resolution. The same four pitches are reused across all
variants — for N>4, each pitch repeats; the by-ear test is
identical, only the slice grid changes.

Layout (parameterised by N = 4, 8, 16):

  N inputs cycling through [220, 330, 440, 660] Hz.
  Each input is one bar long (2000 ms at 120 BPM, BAR_MS / N per
  segment) at 44.1 kHz.

  Chain k = segment_k(input_0) ++ segment_k(input_1) ++ ...
         ++ segment_k(input_{N-1})
  N chains, each one bar = 2000 ms with N equal slice markers.
  (Same construction as ot-doom mixed mode, no +1 padding, no
  per-stem stacking.)

  T1 only:
    setup.slice = ON
    fx1 = DJ_EQ, fx2 = COMPRESSOR
  Pattern: 16-step bar, N trigs evenly spaced (interval = 16/N),
    each sample-locked to its chain slot. No per-trig
    slice_index lock.
  Scenes:
    A: T1.slice_index = 0
    B: T1.slice_index = N-1
  active_scene_a = 0, active_scene_b = 1.

Expected behaviour: sweeping the crossfader A → B walks the played
slice through 0 → N-1, so the pattern's pitch should jump through
220 → 330 → 440 → 660 (repeating for N>4) in N uniform fader
bands.

Observed (per `docs/planning/ot-doom-crossfader.md`): on a 16-tick
fader the input transitions land off the expected positions. Push
these projects, then walk the fader and compare against an
equivalent project set up by hand on the device — if the by-hand
version exhibits the same skew, the bug is firmware-side and our
job is to compensate. If it doesn't, we've done something wrong
in the chain rendering.

Outputs:
  ~/Downloads/slaw-demo-4.ot.zip
  ~/Downloads/slaw-demo-8.ot.zip
  ~/Downloads/slaw-demo-16.ot.zip

Note: the `-N` suffix breaks the strict <adj>-<noun> regex used by
tools/sync.py auto-batch verbs, so push these by hand
(e.g. `unzip -d /Volumes/OCTATRACK/AUDIO/strudelbeats/ ...`).
"""
from __future__ import annotations

import math
import pathlib
import shutil
import struct
import sys
import tempfile
import wave

from octapy import (
    FX1Type,
    FX2Type,
    Project,
    SliceMode,
)


PROJECT_BASE = 'slaw-demo'                  # ~/Downloads/<this>-<N>.ot.zip
PROJECT_NAME_BASE = 'SLAW-DEMO'             # uppercased for OT-side dirs

DOWNLOADS = pathlib.Path.home() / 'Downloads'

SAMPLE_RATE = 44100
BPM = 120
N_PATTERN_STEPS = 16
N_VARIANTS = [4, 8, 16]

# Distinct pitches so it's obvious by ear which input is selected.
# Reused (cycled) for variants where N > len(BASE_FREQS).
BASE_FREQS = [220.0, 330.0, 440.0, 660.0]

# Derived: one bar at 120 BPM = 2000 ms.
BAR_MS = int(round(60_000.0 / BPM * 4))


def render_input_segments(freq: float, n_inputs: int, seg_frames: int) -> list[bytes]:
    """Render a one-bar sine at `freq` Hz, then chop into `n_inputs`
    equal segments and return the raw 16-bit PCM bytes per segment."""
    bar_frames = seg_frames * n_inputs
    full = bytearray()
    for n in range(bar_frames):
        v = int(0.5 * 32767 * math.sin(2 * math.pi * freq * n / SAMPLE_RATE))
        full += struct.pack('<h', v)
    return [
        bytes(full[k * seg_frames * 2:(k + 1) * seg_frames * 2])
        for k in range(n_inputs)
    ]


def write_chain(path: pathlib.Path, segs: list[bytes]) -> None:
    """Concat the supplied per-input segments into one wav."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for seg in segs:
            w.writeframes(seg)


def set_equal_slice_markers(project, slot, n_inputs, seg_ms, bar_frames):
    """N equal slice markers across the chain. Mirrors
    `app/export/octatrack/ot_doom/render.py::set_equal_slices` but
    inlined here so this script has no app/* deps."""
    slot_markers = project.markers.get_slot(slot, is_static=False)
    slot_markers.sample_length = bar_frames
    slices = [
        (int(round(i * seg_ms)), int(round((i + 1) * seg_ms)))
        for i in range(n_inputs)
    ]
    slot_markers.set_slices_ms(slices, sample_rate=SAMPLE_RATE)
    project.markers.set_slot(slot, slot_markers, is_static=False)


def build(work_dir: pathlib.Path, n_inputs: int) -> pathlib.Path:
    """Assemble one OT project zip for the given slice count and
    return its path under DOWNLOADS."""
    seg_ms = BAR_MS // n_inputs
    seg_frames = int(round(SAMPLE_RATE * seg_ms / 1000))
    bar_frames = seg_frames * n_inputs

    # Cycle BASE_FREQS — for N=8 each pitch appears twice; for N=16
    # four times. Same audio, finer slice grid.
    freqs = [BASE_FREQS[i % len(BASE_FREQS)] for i in range(n_inputs)]

    per_input_segments = [render_input_segments(f, n_inputs, seg_frames) for f in freqs]

    chain_paths = []
    for k in range(n_inputs):
        path = work_dir / f'chain{k:02d}.wav'
        write_chain(path, [per_input_segments[i][k] for i in range(n_inputs)])
        chain_paths.append(path)

    project_name = f'{PROJECT_NAME_BASE}-{n_inputs}'
    project = Project.from_template(project_name[:16])
    project.settings.tempo = float(BPM)
    project.master_track = True

    flex_slots = []
    for chain_path in chain_paths:
        slot = project.add_sample(str(chain_path.resolve()), slot_type='FLEX')
        set_equal_slice_markers(project, slot, n_inputs, seg_ms, bar_frames)
        flex_slots.append(slot)

    bank = project.bank(1)
    part = bank.part(1)

    t1 = part.audio_track(1)
    t1.configure_flex(flex_slots[0])
    t1.setup.slice = SliceMode.ON
    t1.fx1_type = FX1Type.DJ_EQ
    t1.fx2_type = FX2Type.COMPRESSOR

    # Scenes: A = first slice of every chain (= input 0),
    #         B = last slice of every chain  (= input N-1).
    part.scene(1).track(1).slice_index = 0
    part.scene(2).track(1).slice_index = n_inputs - 1
    part.active_scene_a = 0
    part.active_scene_b = 1

    # Pattern: 16 steps, N evenly spaced trigs, each sample-locked to
    # its chain. No per-trig slice_index — that would override the
    # scene.
    pattern = bank.pattern(1)
    pattern.scale_length = N_PATTERN_STEPS
    interval = N_PATTERN_STEPS // n_inputs
    active = [k * interval + 1 for k in range(n_inputs)]
    pt = pattern.audio_track(1)
    pt.active_steps = active
    for k, step_num in enumerate(active):
        pt.step(step_num).sample_lock = flex_slots[k]

    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOADS / f'{PROJECT_BASE}-{n_inputs}.ot.zip'
    project.to_zip(zip_path)
    return zip_path


def main():
    if not DOWNLOADS.exists():
        sys.exit(f'~/Downloads not found at {DOWNLOADS}')
    for n in N_VARIANTS:
        work_dir = pathlib.Path(tempfile.mkdtemp(prefix=f'slaw-demo-{n}-'))
        try:
            zip_path = build(work_dir, n)
            print(f'wrote {zip_path}')
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    main()

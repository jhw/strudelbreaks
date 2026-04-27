#!/usr/bin/env python
"""tools/slaw_demo.py — minimal OT project to expose the ot-doom
crossfader skew.

Goal: reproduce the skew with the simplest possible audio so we
can decide whether the misalignment is in our chain rendering or
in the device's firmware. The chain layout is the same as
ot-doom's split_stems=False mode, but the source audio is just
four pure sine tones — easy to hear which slice is playing.

Layout:

  N = 4 inputs, each a pure sine at a distinct pitch:
      input 0 = 220 Hz   (A3)
      input 1 = 330 Hz   (E4)
      input 2 = 440 Hz   (A4)
      input 3 = 660 Hz   (E5)
  Each input is one bar long (2000 ms at 120 BPM) at 44.1 kHz.

  Chain k = segment_k(input_0) ++ segment_k(input_1)
         ++ segment_k(input_2) ++ segment_k(input_3)
  4 chains, each one bar = 2000 ms with 4 equal 500 ms slice
  markers. (Same construction as ot-doom mixed mode, no +1
  padding, no per-stem stacking.)

  T1 only:
    setup.slice = ON
    fx1 = DJ_EQ, fx2 = COMPRESSOR
  Pattern: 16-step bar, 4 trigs at steps 1 / 5 / 9 / 13, each
    sample-locked to its chain slot. No per-trig slice_index lock.
  Scenes:
    A: T1.slice_index = 0
    B: T1.slice_index = 3
  active_scene_a = 0, active_scene_b = 1.

Expected behaviour: sweeping the crossfader A → B walks the played
slice through 0 → 1 → 2 → 3, so the pattern's pitch should jump
through 220 → 330 → 440 → 660 Hz in four uniform fader bands.

Observed (per `docs/planning/ot-doom-crossfader.md`): on a 16-tick
fader the input transitions land at positions 6 / 11 / 15, not the
expected 5 / 9 / 13. Push this project, then walk the fader and set
up an equivalent project by hand on the device — if the by-hand
version exhibits the same skew, the bug is firmware-side and our
job is to compensate. If it doesn't, we've done something wrong in
the chain rendering.

Output: ~/Downloads/slaw-demo.ot.zip — adj-noun shape so
`tools/sync.py push` picks it up automatically.
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


PROJECT_FILENAME = 'slaw-demo'              # ~/Downloads/<this>.ot.zip
PROJECT_NAME_OT = 'SLAW-DEMO'               # uppercased for OT-side dirs

DOWNLOADS = pathlib.Path.home() / 'Downloads'

SAMPLE_RATE = 44100
BPM = 120
N_INPUTS = 4
N_PATTERN_STEPS = 16

# Distinct pitches so it's obvious by ear which input is selected.
INPUT_FREQS = [220.0, 330.0, 440.0, 660.0]

# Derived: one bar at 120 BPM = 2000 ms, divided into N segments.
BAR_MS = int(round(60_000.0 / BPM * 4))
SEG_MS = BAR_MS // N_INPUTS
SEG_FRAMES = int(round(SAMPLE_RATE * SEG_MS / 1000))
BAR_FRAMES = SEG_FRAMES * N_INPUTS


def render_input_segments(freq: float) -> list[bytes]:
    """Render a one-bar sine at `freq` Hz, then chop into N equal
    segments and return the raw 16-bit PCM bytes per segment."""
    full = bytearray()
    for n in range(BAR_FRAMES):
        v = int(0.5 * 32767 * math.sin(2 * math.pi * freq * n / SAMPLE_RATE))
        full += struct.pack('<h', v)
    return [
        bytes(full[k * SEG_FRAMES * 2:(k + 1) * SEG_FRAMES * 2])
        for k in range(N_INPUTS)
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


def set_equal_slice_markers(project, slot, sample_length_frames):
    """N equal slice markers across the chain. Mirrors
    `app/export/octatrack/ot_doom/render.py::set_equal_slices` but
    inlined here so this script has no app/* deps."""
    slot_markers = project.markers.get_slot(slot, is_static=False)
    slot_markers.sample_length = sample_length_frames
    slices = [
        (int(round(i * SEG_MS)), int(round((i + 1) * SEG_MS)))
        for i in range(N_INPUTS)
    ]
    slot_markers.set_slices_ms(slices, sample_rate=SAMPLE_RATE)
    project.markers.set_slot(slot, slot_markers, is_static=False)


def build(work_dir: pathlib.Path) -> pathlib.Path:
    """Assemble the OT project zip in `work_dir` and return its path
    (under DOWNLOADS, ready for `sync.py push`)."""
    # Step 1: render each input to per-segment PCM bytes.
    per_input_segments = [render_input_segments(f) for f in INPUT_FREQS]

    # Step 2: build N matrix chains.
    # chain[k] = segment_k of every input concatenated, in input-index
    # order (input_0, input_1, ..., input_{N-1}).
    chain_paths = []
    for k in range(N_INPUTS):
        path = work_dir / f'chain{k:02d}.wav'
        write_chain(path, [per_input_segments[i][k] for i in range(N_INPUTS)])
        chain_paths.append(path)

    # Step 3: OT project — flex slots, slice markers, scenes, pattern.
    project = Project.from_template(PROJECT_NAME_OT[:16])
    project.settings.tempo = float(BPM)
    project.master_track = True

    flex_slots = []
    for chain_path in chain_paths:
        slot = project.add_sample(str(chain_path.resolve()), slot_type='FLEX')
        set_equal_slice_markers(project, slot, BAR_FRAMES)
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
    part.scene(2).track(1).slice_index = N_INPUTS - 1
    part.active_scene_a = 0
    part.active_scene_b = 1

    # Pattern: 16 steps, trigs at 1/5/9/13, each sample-locked to its
    # chain. No per-trig slice_index — that would override the scene.
    pattern = bank.pattern(1)
    pattern.scale_length = N_PATTERN_STEPS
    interval = N_PATTERN_STEPS // N_INPUTS
    active = [k * interval + 1 for k in range(N_INPUTS)]
    pt = pattern.audio_track(1)
    pt.active_steps = active
    for k, step_num in enumerate(active):
        pt.step(step_num).sample_lock = flex_slots[k]

    # Step 4: write the zip into ~/Downloads.
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOADS / f'{PROJECT_FILENAME}.ot.zip'
    project.to_zip(zip_path)
    return zip_path


def main():
    if not DOWNLOADS.exists():
        sys.exit(f'~/Downloads not found at {DOWNLOADS}')
    work_dir = pathlib.Path(tempfile.mkdtemp(prefix='slaw-demo-'))
    try:
        zip_path = build(work_dir)
        print(f'wrote {zip_path}')
        print('next: tools/sync.py push slaw-demo  (or `tools/sync.py push -f`)')
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    main()

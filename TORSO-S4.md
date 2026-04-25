# Torso S-4 export quirks

Notes on what the device expects and the choices we make in the
exporter to stay strict about it. Mirrors `OCTATRACK.md` /
`STRUDEL.md` — relevant to anything under `scripts/export/torso-s4/`.

## Sample rate: 96 kHz, always

The S-4 manual states that imported WAVs are auto-resampled, with a
maximum supported source rate of 96 kHz. Any input ≤ 96 kHz is kept
on-device at its source rate; anything above is downsampled.

The strudel sample gist hosts breaks at mixed 44.1 / 48 kHz, so the
naive renderer would emit per-row WAVs at whatever rate happened to
be hit first by `pydub.AudioSegment.from_wav`. That makes the export
non-reproducible and bound to whichever break was sampled first.

We pin every export to 96 kHz instead — the device's ceiling. Two
reasons:

1. **Reproducibility.** Two runs of the renderer over the same export
   produce byte-identical WAVs, regardless of the source rates the
   gist serves up.
2. **Headroom for upstream fades / envelopes.** If we ever reintroduce
   a sub-perceptual fade envelope inside `render_cell`, the higher
   sample rate gives that fade better resolution at the boundary
   sample. Cheap insurance for future work.

The cost is upsampling 44.1 / 48 kHz sources to 96 kHz, which adds no
information but doesn't lose any either. pydub's `set_frame_rate` uses
`audioop.ratecv` (linear interpolation) — adequate for breakbeats.

Implemented in `scripts/export/torso-s4/audio.py` via
`load_break(...).set_frame_rate(S4_SAMPLE_RATE)`. See `OCTATRACK.md`
for the contrasting OT case (44.1 kHz pin, with audible
9% slowdown if violated).

## Event timing: cumulative rounding, not per-event truncation

Tempera ships at 128 BPM × 8 events/cycle, which gives an event
length of `(4/128) * 60000 / 8 = 234.375 ms`. If we truncate this to
234 ms per event and concatenate 32 events for a 4-cell row, we
ship a 7488 ms row instead of the correct 7500 ms — a 12 ms drift
per row that grows with cell count.

`audio.render_cell` therefore takes a `float` `event_ms` and computes
each event's integer-ms length cumulatively:

```python
cum = 0
for i in range(n_events):
    next_cum = round((i + 1) * event_ms)
    this_event_ms = next_cum - cum
    cum = next_cum
```

Per-event lengths now alternate between 234 and 235 ms (sub-perceptual
±1 ms jitter), but the cell total = `round(8 * 234.375)` = 1875 ms
exactly. Across N cells the row total is `round(N * cell_ms)` — no
drift accumulates regardless of how long the row is.

Side effect: `event_ms()` in `render.py` now returns float, not int.
Tests under `tests/export/test_torso_s4.py` use `assertAlmostEqual`
where the captured BPM doesn't divide cleanly.

## Other constraints worth knowing

- **Bit depth**: pydub writes 16-bit signed PCM by default; the S-4
  accepts that.
- **Channel count**: the S-4 plays mono and stereo. We take whatever
  the source provides. The strudel gist is mono today; if a future
  source is stereo, no code change needed.
- **Sample-bundle layout**: the manual reserves `/samples/` for
  user-imported WAVs (factory content lives in `/FACTORY/`, hidden
  from MSD). `push.py` extracts under
  `/Volumes/S4/samples/strudelbeats/<project>/<row>.wav` so wholesale
  backup or wipe is one folder operation.

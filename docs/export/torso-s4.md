# Torso S-4 export

Per-target notes for `app/export/torso_s4/`. Device-side
constraints below; render-pipeline contract above.

## What this target does

One WAV per non-empty bank (= row). A row WAV is the audio
concatenation of its cells; each cell renders the captured Strudel
pattern (slice indices into the cell's break vocabulary,
polymetric-stretched per `STRUDEL.md`). Output bundle:

```
~/Downloads/<project>.s4.zip
└── <project>/
    ├── <adj-noun-1>.wav   (row 1)
    ├── <adj-noun-2>.wav   (row 2)
    └── …
```

`scripts/torso-s4/push.py` extracts that into
`/Volumes/S4/samples/strudelbeats/`, landing at
`/Volumes/S4/samples/strudelbeats/<project>/<row>.wav` — the manual
reserves `/samples/` for user-imported WAVs.

Invocation: tempera's `export ▾` menu → `torso-s4`. Posts the captures
payload to `POST /api/export/binary` (`target='torso-s4'`); the server
calls `app.export.torso_s4.render.render()` and streams the project
zip back. Browser saves to `~/Downloads/<name>.s4.zip`.

The `seed` field in the request body (optional) deterministically picks
the per-row WAV names: same payload + same seed → byte-identical bundle.

## Source mode (`source` field)

Default `json`. Controls how break audio is sourced from the gist:

- **`json`** — fetch each break's beatwav pattern JSON, render to WAV
  at the captures' BPM and 96 kHz via `beatwav.AudioRenderer`. The
  renderer hits the device's target rate up-front, so no
  `set_frame_rate` upsampling is needed on load.
- **`wav`** — bundle the gist's WAVs as-is (44.1 / 48 kHz mixed).
  `load_break` upsamples to 96 kHz so every chunk downstream sits at
  the same rate.

JSON mode falls back per-break to WAV when the gist has no
`{name}.json`, with a warning. See
`app/export/common/sample_source.py` for the shared abstraction.

## Sample rate: 96 kHz, always

The S-4 manual states that imported WAVs are auto-resampled, with a
maximum supported source rate of 96 kHz. Any input ≤ 96 kHz is kept
on-device at its source rate; anything above is downsampled.

The strudel sample gist hosts breaks at mixed 44.1 / 48 kHz, so a
naive renderer would emit per-row WAVs at whatever rate happened to
be hit first by `pydub.AudioSegment.from_wav`. That makes the export
non-reproducible and bound to whichever break was sampled first.

We pin every export to 96 kHz instead — the device's ceiling. Two
reasons:

1. **Reproducibility.** Two runs of the renderer over the same export
   produce byte-identical WAVs, regardless of the source rates the
   gist serves up.
2. **Headroom for upstream fades / envelopes.** The per-event fade
   envelope inside `render_cell` (1 ms in / 2 ms out by default) gets
   better resolution at the higher sample rate — 96 samples per ms
   versus ~44 at 44.1 kHz means the gain ramp lands smoother at the
   boundary.

The cost is upsampling 44.1 / 48 kHz sources to 96 kHz (WAV-source
mode) or rendering at 96 kHz directly (JSON-source mode). Either way
the device sees one consistent rate.

Implemented in `app/export/torso_s4/audio.py` via
`load_break(...).set_frame_rate(S4_SAMPLE_RATE)`. See
`docs/export/octatrack.md` for the contrasting OT case (44.1 kHz pin,
with audible 9% slowdown if violated).

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
  from MSD). `scripts/torso-s4/push.py` extracts under
  `/Volumes/S4/samples/strudelbeats/<project>/<row>.wav` so wholesale
  backup or wipe is one folder operation.

# Octatrack export

Per-target notes for `scripts/export/octatrack/` (per-cell-pattern
target). Device-side constraints are also referenced from
`docs/export/ot-doom.md`.

## What this target does

One bank per non-empty row, one pattern per cell. Each pattern is a
1-bar 16-step grid: the captured `eventsPerCycle` slice indices fire
as trigs at every other step (1, 3, …, 2N-1), each sample-locked to
its break's flex slot and slice-locked to the captured slice index.
OT pattern looping plays subsequent cycles. Source breaks are loaded
into 16-slice flex slots once per project.

CLI:

```
python scripts/export/octatrack/render.py <export.json>
    [--name NAME] [--seed N]
    [--probability 0..1]
    [--source {json,wav}]
```

Output: `tmp/octatrack/<name>.zip`.

`--probability` (default 1.0 = always fires) snaps to the nearest
Octatrack `TrigCondition.PERCENT_*` bucket and applies it to every
captured trig. See `scripts/export/octatrack/render.py` for the bucket
list.

## Source mode (`--source`)

Default `json`. Controls how break audio is sourced from the gist:

- **`json`** — fetch each break's beatwav pattern JSON, render to WAV
  at the captures' BPM and 44.1 kHz via `beatwav.AudioRenderer`. Closes
  the latent 48 kHz drift hole described below: every bundled WAV is
  guaranteed at the OT's native rate, regardless of the gist's source
  rates.
- **`wav`** — bundle the gist's WAVs as-is. Legacy mode; necessary for
  older gists that don't carry sibling JSON files. Drift is latent but
  not audible at this target's per-event trig granularity (each trig
  plays for ~1/8 note before being replaced).

JSON mode falls back per-break to WAV when the gist has no
`{name}.json`, with a warning. See `scripts/export/common/sample_source.py`
for the shared abstraction (cache layout, S3 mirror, fallback rules).

## Sample rate: 44100 Hz, full stop

The Octatrack plays back assuming 44.1 kHz. There is no per-sample
rate metadata it consults at trig time. A WAV at any other rate plays
at the *wrong speed*: a 48 kHz file plays at `44100 / 48000 ≈ 91.9%`
speed (= ~9% slower / lower-pitched) on every trig.

The strudel sample gist mixes 44.1 and 48 kHz wavs. That's fine for
Strudel (its audio engine reads the WAV header and resamples on the
fly) but lethal for OT export — you get a per-sample timing drift
that's most audible when a trig plays an uninterrupted slice for any
length of time. In the doom render that's the entire 1/4-bar segment
between trigs, so the lag is plain. In the per-event octatrack render
each trig plays for ~1/8 note before being replaced, so the same drift
is there but doesn't accumulate audibly within a single trig.

**Rule:** every WAV bundled into an OT zip must be 44100 Hz before
export. JSON-source mode satisfies this by rendering at 44.1 kHz
up-front (the recommended path). WAV-source mode bundles the gist
files as-is — a latent bug that's only inaudible because trigs are
short; if anyone ever lengthens the trig timing, switch to JSON mode
or resample on load (the way `ot-doom/audio.py` does).

## Other constraints worth knowing

- **Bit depth**: 16-bit signed PCM. pydub writes this by default for
  WAV; nothing extra to do.
- **Channel count**: mono or stereo. The device will play either; mix
  on the source side if needed.
- **Slot pool**: 128 flex slots, 128 static slots per project. The
  per-cell-pattern target uses one flex slot per unique break name —
  small. The doom target's 16 rows × 16 cells = 256 chains exceeds
  the flex pool; its validator should sanity-check before this is a
  real concern.
- **Project tempo** is set on `Project.settings.tempo` (float BPM).
  Slice markers + crossfader interpolation are tempo-relative, so
  getting this right matters even when nothing on the OT side is
  BPM-synced.

## References

- octapy ≥ 0.1.23: `AudioSceneTrack.slice_index` (used by ot-doom for
  the crossfader). Confirmed in the 0.1.31 pin in `requirements.txt`.
- `docs/export/ot-doom.md` — second OT target (megabreak-of-doom),
  shares this device-side constraint sheet.

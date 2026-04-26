# Octatrack export

Per-target notes for `app/export/octatrack/ot_basic/` (per-cell-pattern
target, per-track stems). Device-side constraints are also referenced
from `docs/export/ot-doom.md`.

## What this target does

One bank per non-empty row, one pattern per cell. Each pattern is a
1-bar 16-step grid: the captured `eventsPerCycle` slice indices fire
as trigs at every other step (1, 3, …, 2N-1) on each of T1/T2/T3 —
sample-locked to the per-stem flex slot for that break, slice-locked
to the captured slice index. OT pattern looping plays subsequent
cycles. Source breaks are loaded into 16-slice flex slots once per
project, **three slots per break** (kick, snare, hat).

Invocation: tempera's `export ▾` menu → `ot-basic`. Posts the captures
payload to `POST /api/export/binary` (`target='ot-basic'`); the server
calls `app.export.octatrack.ot_basic.render.render()` and streams the
project zip back. Browser saves to `~/Downloads/<name>.ot-basic.zip`.

`probability` (request-body field; default 1.0 = always fires) snaps
to the nearest Octatrack `TrigCondition.PERCENT_*` bucket and applies
it to every captured trig on every track. See
`app/export/octatrack/ot_basic/render.py` for the bucket list.

## Per-track stems

Each break is rendered as three drum stems (kick / snare / hat) via
beatwav, each filtered to one drum type before mixdown. Stems map to
OT audio tracks T1, T2, T3 — same trig pattern on all three (same
step positions, same `slice_index` per step), distinct `sample_lock`
per track (the per-stem flex slot). Each kit piece can be muted, EQ'd
or compressed independently on the device.

JSON-source rendering only — there's no way to decompose a pre-mixed
gist WAV into stems after-the-fact, so any break missing its
`{name}.json` in the gist fails loudly.

## FX layout

Configured once on part 1 per bank:

- **T1, T2, T3**: `FX1 = DJ_EQ`, `FX2 = COMPRESSOR` (defaults; per-track
  EQ + dynamics shaping).
- **T8**: `FX1 = CHORUS` (`mix = 64`) and `FX2 = DELAY` (`send = 64`).
  T8 hosts the project-wide modulation send chain. The two effects
  use different parameter names for the wet control because they
  expose different params in octapy.

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
is latent rather than audible within a single trig.

**Rule:** every WAV bundled into an OT zip must be 44100 Hz before
export. The per-stem JSON render hits OT_SAMPLE_RATE up-front, so
this is satisfied by construction.

## Other constraints worth knowing

- **Bit depth**: 16-bit signed PCM. pydub writes this by default for
  WAV; nothing extra to do.
- **Channel count**: mono or stereo. The device will play either; mix
  on the source side if needed.
- **Slot pool**: 128 flex slots, 128 static slots per project. The
  per-cell-pattern target uses **3 flex slots per unique break name**
  (kick + snare + hat) — small in practice for tempera-realistic
  break-vocabulary sizes. The doom target's pack-stems-into-one-slot
  design keeps slot count the same as the old mixed-stem version;
  see `docs/export/ot-doom.md`.
- **Project tempo** is set on `Project.settings.tempo` (float BPM).
  Slice markers + crossfader interpolation are tempo-relative, so
  getting this right matters even when nothing on the OT side is
  BPM-synced.

## References

- octapy ≥ 0.1.23: `AudioSceneTrack.slice_index` (used by ot-doom for
  the crossfader). Confirmed in the 0.1.31 pin in `requirements.txt`.
- `docs/export/ot-doom.md` — second OT target (megabreak-of-doom),
  shares this device-side constraint sheet plus the per-track stems
  shape.

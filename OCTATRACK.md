# Octatrack export quirks

Notes on the device's expectations that bite if you ignore them.
Relevant to anything under `scripts/export/octatrack/` or
`scripts/export/ot-doom/` — basically, anywhere we render audio that
ends up in an OT project zip.

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
export. `audio.py` in `ot-doom/` does this with `set_frame_rate(44100)`
on `load_break`. The `octatrack/` target currently bundles source wavs
as-is — a latent bug that is not audible at the granularity it uses,
but should be fixed if anyone ever switches its trig timing.

## Other constraints worth knowing

- **Bit depth**: 16-bit signed PCM. pydub writes this by default for
  WAV; nothing extra to do.
- **Channel count**: mono or stereo. The device will play either; mix
  on the source side if needed.
- **Slot pool**: 128 flex slots, 128 static slots per project. Doom
  with 16 rows × 16 cells = 256 chains exceeds the flex pool — the
  validator should sanity-check before this becomes a real concern.
- **Project tempo** is set on the `Project.settings.tempo` (float
  BPM). Slice markers + crossfader interpolation are tempo-relative,
  so getting this right is important even when nothing on the OT
  side is BPM-synced.

## References

- octapy ≥ 0.1.23: `AudioSceneTrack.slice_index` (used by ot-doom for
  the crossfader). Confirmed in the 0.1.31 pin in `requirements.txt`.

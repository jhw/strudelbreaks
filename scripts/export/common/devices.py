"""Device-side sample rates for the export targets.

Each render target ships WAVs at its device's native rate so playback
needs no resample at trig time. Centralised here so the two
Octatrack targets (`octatrack/`, `ot-doom/`) can't drift apart.

- Octatrack assumes 44.1 kHz at trig time (no metadata read; a 48 kHz
  source plays at ~91.9% speed). See docs/export/octatrack.md.
- Torso S-4 auto-resamples imports up to a 96 kHz ceiling; we pin
  there for reproducibility. See docs/export/torso-s4.md.
"""

OT_SAMPLE_RATE = 44100
S4_SAMPLE_RATE = 96000

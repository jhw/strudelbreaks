# Export targets

Per-target documentation for `app/export/`. The captures payload
(persisted by tempera in `localStorage`) is rendered into one of
several formats by the deployed Lambda handlers in `app/api/`; each
target's render module here is imported and called by
`app/exporters.py`.

| Doc | Target | Filename | Source mode |
|---|---|---|---|
| [octatrack.md](octatrack.md) | `app/export/octatrack/ot_basic/` | `<name>.ot-basic.zip` — per-cell patterns, per-track stems | JSON-only (per-stem) |
| [ot-doom.md](ot-doom.md) | `app/export/octatrack/ot_doom/` | `<name>.ot-doom.zip` — megabreak-of-doom matrix chains, per-track stems | JSON-only (per-stem) |
| [torso-s4.md](torso-s4.md) | `app/export/torso_s4/` | `<name>.s4.zip` — Torso S-4 sample bundle, one mixed WAV per row | `source` ∈ `{json, wav}` (mixed) |
| [strudel.md](strudel.md) | `app/export/strudel/` | `<name>.strudel.js` — standalone playback template | (n/a — WAV-only by construction) |

## Shared infrastructure

- **`app/export/common/schema.py`** — captures JSON schema gate
  (currently version 7).
- **`app/export/common/names/`** — adjective × noun word lists
  for default project / row names.
- **`app/export/common/sample_source.py`** — break-name → local
  WAV path resolver, shared by the three audio targets. Two source
  modes:
  - `json` (default) — fetch each break's beatwav pattern JSON from
    the gist, render to WAV at the captures' BPM and the device's
    native sample rate. Optional `tracks` kwarg (used by the OT
    targets) renders one WAV per drum stem (kick/snare/hat) by
    filtering matched_hits per drum type.
  - `wav` — fetch each break's WAV from the gist as-is. Required for
    older WAV-only gists; per-break fallback when JSON mode finds no
    sibling JSON. Mixed-stem only — gist WAVs can't be split into
    per-track stems after the fact.

  Cache layout under `<tmp>/samples/<gistId>/` (where `<tmp>` is
  `<repo>/tmp/` locally and `/tmp/` on Lambda — set via
  `STRUDELBREAKS_TMP`):

  ```
  <name>.wav                                       gist-fetched WAVs
  json/<name>.json                                 gist-fetched JSON patterns
  rendered/sr<rate>_bpm<bpm>/<name>.wav            JSON-rendered mixed WAVs
  rendered/sr<rate>_bpm<bpm>/<name>__<track>.wav   per-track stems
  ```

  Per-stem files use a flat `<name>__<track>.wav` layout so basenames
  stay unique across breaks (the OT's `add_sample` deduplicates by
  basename, so nested per-name dirs would collapse all breaks' same
  stem into one slot).

  The rendered cache is keyed on (sample_rate, bpm) so the OT
  (44.1 kHz) and S-4 (96 kHz) caches coexist without collision.

  JSON mode mirrors the configured one-shot S3 bucket
  (`ONESHOT_S3_URI` env var; defaults to `s3://wol-samplebank/samples/`
  for local dev) to `<tmp>/oneshots/` via boto3 on first use — that's
  where the one-shot drum samples beatwav references live. On Lambda
  the bucket URI is supplied as a Pulumi config value and the
  function's IAM role grants `s3:GetObject` on it; no per-laptop AWS
  credential refresh.
- **`app/export/common/devices.py`** — per-device sample-rate
  constants (`OT_SAMPLE_RATE`, `S4_SAMPLE_RATE`) shared across
  targets so the two Octatrack targets can't drift apart.

## Strudel runtime quirks

`STRUDEL.md` (repo root) is the language / transpile reference —
double-quote-to-Pattern lifting, slider literal-args constraint,
polymetric stretch, runtime-string lifting via `.fmap(mini).innerJoin()`.
Relevant when editing `app/launch/tempera.strudel.js` or the
`app/export/strudel/templates/` Jinja2 source.

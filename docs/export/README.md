# Export targets

Per-target documentation for `scripts/export/`. Each captures-JSON
export (produced by tempera's "export" button) can be rendered into
several formats:

| Doc | Target | Output | Source mode |
|---|---|---|---|
| [octatrack.md](octatrack.md) | `scripts/export/octatrack/ot-basic/` | OT project zip — one bank per row, one pattern per cell | `--source {json,wav}` |
| [ot-doom.md](ot-doom.md) | `scripts/export/octatrack/ot-doom/` | OT project zip — megabreak-of-doom matrix chains | `--source {json,wav}` |
| [torso-s4.md](torso-s4.md) | `scripts/export/torso-s4/` | Torso S-4 sample bundle — one WAV per row | `--source {json,wav}` |
| [strudel.md](strudel.md) | `scripts/export/strudel/` | Standalone `.strudel.js` playback template | (n/a — WAV-only by construction) |

## Shared infrastructure

- **`scripts/export/common/cli.py`** — argparse skeleton (export path,
  `--name`, `--seed`).
- **`scripts/export/common/schema.py`** — captures JSON schema gate
  (currently version 7).
- **`scripts/export/common/names/`** — adjective × noun word lists
  for default project / row names.
- **`scripts/export/common/sample_source.py`** — break-name → local
  WAV path resolver, shared by the three audio targets. Two source
  modes:
  - `json` (default) — fetch each break's beatwav pattern JSON from
    the gist, render to WAV at the captures' BPM and the device's
    native sample rate.
  - `wav` — fetch each break's WAV from the gist as-is. Required for
    older WAV-only gists; per-break fallback when JSON mode finds no
    sibling JSON.

  Cache layout under `tmp/samples/<gistId>/`:

  ```
  <name>.wav                            gist-fetched WAVs
  json/<name>.json                      gist-fetched JSON patterns
  rendered/sr<rate>_bpm<bpm>/<name>.wav JSON-rendered WAVs
  ```

  The rendered cache is keyed on (sample_rate, bpm) so the OT
  (44.1 kHz) and S-4 (96 kHz) caches coexist without collision.

  JSON mode mirrors `s3://wol-samplebank/samples/` to `tmp/oneshots/`
  via `aws s3 sync` on first use — that's where the one-shot drum
  samples beatwav references live.

## Strudel runtime quirks

`STRUDEL.md` (repo root) is the language / transpile reference —
double-quote-to-Pattern lifting, slider literal-args constraint,
polymetric stretch, runtime-string lifting via `.fmap(mini).innerJoin()`.
Relevant when editing `tempera.strudel.js` or the
`scripts/export/strudel/templates/` Jinja2 source.

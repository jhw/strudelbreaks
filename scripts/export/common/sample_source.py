"""Resolve break-name → local WAV path, sourced from a Strudel sample gist.

Two modes:

- 'wav' (legacy): fetch each break's WAV directly from the gist and cache
  it. Whatever BPM the gist's WAV was recorded at is what plays back —
  fine if the captures bpm matches the source, lossy otherwise.

- 'json' (default): fetch each break's beatwav JSON pattern from the
  gist and re-render it to a WAV at the caller's target BPM and sample
  rate. Clean: tempo is correct by construction, sample rate is the
  device's native rate, no resample-on-load.

JSON mode requires a local mirror of the `wol-samplebank` S3 bucket at
`tmp/oneshots/`. The first JSON-mode call mirrors the bucket via
`aws s3 sync`; subsequent calls reuse the mirror.

Older gists are WAV-only. JSON mode falls back per-break to WAV when no
`{name}.json` is in the gist, with a warning, so legacy material still
exports successfully.

Cache layout under `tmp/samples/<gistId>/`:

    <name>.wav                            gist-fetched WAVs
    json/<name>.json                      gist-fetched JSON patterns
    rendered/sr<rate>_bpm<bpm>/<name>.wav JSON-rendered WAVs

The rendered cache is keyed on (sample_rate, bpm) so multiple targets
(octatrack at 44.1 kHz, torso-s4 at 96 kHz) coexist without collision.
"""
from __future__ import annotations

import json
import logging
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Dict, Tuple


log = logging.getLogger(__name__)

ONESHOT_S3_URI = 's3://wol-samplebank/samples/'

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
ONESHOT_CACHE = REPO_ROOT / 'tmp' / 'oneshots'
SAMPLES_CACHE = REPO_ROOT / 'tmp' / 'samples'

VALID_SOURCES = ('json', 'wav')


def fetch_manifest(gist_user: str, gist_id: str) -> Tuple[Dict[str, str], str]:
    """Return (`{name: absolute_wav_url}`, `gist_base`) from the gist's
    strudel.json. `gist_base` is the manifest's `_base`, used to derive
    sibling paths like `{name}.json`.
    """
    url = f'https://gist.githubusercontent.com/{gist_user}/{gist_id}/raw/strudel.json'
    with urllib.request.urlopen(url) as r:
        data = json.loads(r.read())
    base = data.get('_base', '')
    wav_urls: Dict[str, str] = {}
    for k, v in data.items():
        if k.startswith('_'):
            continue
        first = v[0] if isinstance(v, list) else v
        wav_urls[k] = first if first.startswith(('http://', 'https://')) else base + first
    return wav_urls, base


def _download(url: str, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as r, open(path, 'wb') as f:
        f.write(r.read())


def cache_wav(name: str, url: str, cache_dir: pathlib.Path) -> pathlib.Path:
    """Mirror a single WAV from `url` into `cache_dir/<name><ext>`."""
    ext = pathlib.Path(url.split('?', 1)[0]).suffix or '.wav'
    path = cache_dir / f'{name}{ext}'
    if not path.exists():
        _download(url, path)
    return path


def cache_json(name: str, gist_base: str, cache_dir: pathlib.Path) -> pathlib.Path | None:
    """Try to mirror `{name}.json` from the gist into
    `cache_dir/json/<name>.json`. Returns None on 404 (older gists are
    WAV-only)."""
    path = cache_dir / 'json' / f'{name}.json'
    if path.exists():
        return path
    url = f'{gist_base}{name}.json'
    try:
        _download(url, path)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return path


def ensure_oneshots_synced(verbose: bool = False) -> pathlib.Path:
    """If `tmp/oneshots/` is missing or empty, mirror wol-samplebank into
    it via `aws s3 sync`. Returns the cache path. Raises
    `CalledProcessError` if the sync fails (most likely AWS auth)."""
    ONESHOT_CACHE.mkdir(parents=True, exist_ok=True)
    if any(ONESHOT_CACHE.iterdir()):
        return ONESHOT_CACHE
    log.info('Syncing one-shot samples from %s ...', ONESHOT_S3_URI)
    subprocess.run(
        ['aws', 's3', 'sync', ONESHOT_S3_URI, str(ONESHOT_CACHE) + '/'],
        check=True,
        stdout=None if verbose else subprocess.DEVNULL,
    )
    return ONESHOT_CACHE


def render_json_to_wav(
    json_path: pathlib.Path,
    out_path: pathlib.Path,
    *,
    target_bpm: float,
    target_sample_rate: int,
    oneshots_base: pathlib.Path,
    num_bars: int = 2,
) -> pathlib.Path:
    """Render a beatwav pattern JSON to a WAV at `out_path`.

    The pattern's recorded BPM is ignored; the output renders at
    `target_bpm`. `target_sample_rate` is the device's native rate
    (44.1 kHz for OT, 96 kHz for S-4) so the export skips a resample.
    """
    # Local import: beatwav is only needed in JSON mode, so WAV-only
    # users don't have to install it.
    from beatwav import AudioRenderer

    renderer = AudioRenderer(
        sample_rate=target_sample_rate,
        samples_base=str(oneshots_base),
    )
    pattern = json.loads(pathlib.Path(json_path).read_text())
    normalised = renderer.normalize_pattern(pattern, num_bars)
    hits = renderer.convert_to_hits(normalised, target_bpm)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    renderer.render(hits, str(out_path), target_bpm, num_bars=num_bars)
    return out_path


def resolve_break_paths(
    *,
    gist_user: str,
    gist_id: str,
    names,
    source: str,
    target_bpm: float,
    target_sample_rate: int,
    num_bars: int = 2,
) -> Dict[str, pathlib.Path]:
    """Return `{name: local_wav_path}` for every requested break.

    `source='wav'`: fetch and cache the gist's WAVs as-is.

    `source='json'`: fetch each break's JSON, render to WAV at
    `target_bpm` / `target_sample_rate`, cache. Per-break fallback to
    WAV when the gist has no `{name}.json` (older gists), with a
    warning so the user knows.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f'source must be one of {VALID_SOURCES}, got {source!r}')

    cache_dir = SAMPLES_CACHE / gist_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    wav_urls, gist_base = fetch_manifest(gist_user, gist_id)
    missing = [n for n in names if n not in wav_urls]
    if missing:
        sys.exit(f'sample gist missing breaks: {missing}')

    out: Dict[str, pathlib.Path] = {}

    if source == 'wav':
        for name in names:
            out[name] = cache_wav(name, wav_urls[name], cache_dir)
        return out

    rendered_dir = (
        cache_dir / 'rendered'
        / f'sr{target_sample_rate}_bpm{int(round(target_bpm))}'
    )

    needs_render = []
    for name in names:
        rendered_path = rendered_dir / f'{name}.wav'
        if rendered_path.exists():
            out[name] = rendered_path
            continue
        json_path = cache_json(name, gist_base, cache_dir)
        if json_path is None:
            log.warning(
                'Gist %s has no %s.json — falling back to source WAV',
                gist_id, name,
            )
            out[name] = cache_wav(name, wav_urls[name], cache_dir)
            continue
        needs_render.append((name, json_path, rendered_path))

    if needs_render:
        oneshots_base = ensure_oneshots_synced()
        for name, json_path, rendered_path in needs_render:
            log.info(
                'Rendering %s.json @ %s BPM, %d Hz → %s',
                name, target_bpm, target_sample_rate, rendered_path,
            )
            render_json_to_wav(
                json_path, rendered_path,
                target_bpm=target_bpm,
                target_sample_rate=target_sample_rate,
                oneshots_base=oneshots_base,
                num_bars=num_bars,
            )
            out[name] = rendered_path

    return out


def add_source_arg(parser) -> None:
    """Register the shared `--source` flag on a target's argparse parser."""
    parser.add_argument(
        '--source', choices=VALID_SOURCES, default='json',
        help='break-source mode: "json" (default) re-renders each break '
             'from its gist-bundled JSON pattern at the target BPM and '
             'device sample rate; "wav" uses the gist WAV as-is. JSON '
             'mode falls back to WAV per-break if the gist has no JSON.',
    )

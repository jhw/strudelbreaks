"""Resolve break-name → local WAV path, sourced from a Strudel sample gist.

Two modes:

- 'wav' (legacy): fetch each break's WAV directly from the gist and cache
  it. Whatever BPM the gist's WAV was recorded at is what plays back —
  fine if the captures bpm matches the source, lossy otherwise.

- 'json' (default): fetch each break's beatwav JSON pattern from the
  gist and re-render it to a WAV at the caller's target BPM and sample
  rate. Clean: tempo is correct by construction, sample rate is the
  device's native rate, no resample-on-load.

JSON mode requires a local mirror of the one-shot S3 bucket. The
bucket URI comes from the `ONESHOT_S3_URI` env var (set as a Pulumi
config + Lambda env var on deploy); the cache root comes from
`STRUDELBREAKS_TMP` (defaults to `<repo>/tmp/` for local dev,
`/tmp/` on Lambda). The first JSON-mode call mirrors the bucket via
boto3 (no `aws` CLI required); subsequent calls reuse the mirror.

Older gists are WAV-only. JSON mode falls back per-break to WAV when no
`{name}.json` is in the gist, with a warning, so legacy material still
exports successfully.

JSON mode also supports **per-track** rendering via the `tracks` kwarg
on `resolve_break_paths`: pass `tracks=('kick', 'snare', 'hat')` to
get per-drum stems instead of a single mixed render. Used by the
Octatrack export targets to map each kit piece to its own audio
track. The torso-s4 target stays on the mixed render.

Cache layout under `<tmp>/samples/<gistId>/`:

    <name>.wav                                      gist-fetched WAVs
    json/<name>.json                                gist-fetched JSON patterns
    rendered/sr<rate>_bpm<bpm>/<name>.wav           JSON-rendered mixed WAVs
    rendered/sr<rate>_bpm<bpm>/<name>__<track>.wav  per-track stems

Per-stem files share a flat directory with a `<name>__<track>.wav`
pattern so basenames stay unique across breaks — the OT's
`add_sample` deduplicates by basename, so a nested
`<name>/<track>.wav` layout would collapse every break's same-stem
slots into one. The rendered cache is keyed on (sample_rate, bpm)
so multiple targets (octatrack at 44.1 kHz, torso-s4 at 96 kHz)
coexist without collision.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import urllib.error
import urllib.request
from typing import Dict, Iterable, Optional, Tuple, Union


log = logging.getLogger(__name__)

DEFAULT_ONESHOT_S3_URI = 's3://wol-samplebank/samples/'

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# When the image was built with oneshots baked in (via
# docker/buildspec.yml's `aws s3 sync` + Dockerfile `COPY`), the
# samples land here. The Lambda runtime mounts /opt read-only — exactly
# what we want for an immutable cache. Local dev that didn't run the
# bake step still works via the runtime boto3 sync below.
BAKED_ONESHOTS = pathlib.Path('/opt/oneshots')


def _tmp_root() -> pathlib.Path:
    """Resolve the cache root: `STRUDELBREAKS_TMP` if set, else the
    repo's `tmp/` (local dev). Lambdas set `STRUDELBREAKS_TMP=/tmp`."""
    override = os.environ.get('STRUDELBREAKS_TMP')
    if override:
        return pathlib.Path(override)
    return REPO_ROOT / 'tmp'


def _oneshot_s3_uri() -> str:
    """Bucket URI: `ONESHOT_S3_URI` if set, else the legacy default
    so existing local dev keeps working."""
    return os.environ.get('ONESHOT_S3_URI', DEFAULT_ONESHOT_S3_URI)


def _oneshot_cache() -> pathlib.Path:
    return _tmp_root() / 'oneshots'


def _baked_oneshots() -> pathlib.Path | None:
    """Return BAKED_ONESHOTS if the image bundled them, else None.
    `iterdir()` will raise if the dir doesn't exist; the existence
    check up front avoids that and keeps the fallback path clean."""
    if BAKED_ONESHOTS.is_dir():
        try:
            next(BAKED_ONESHOTS.iterdir())
            return BAKED_ONESHOTS
        except StopIteration:
            return None
    return None


def _samples_cache() -> pathlib.Path:
    return _tmp_root() / 'samples'

VALID_SOURCES = ('json', 'wav')

# beatwav drum-track names. Anything else passed via `tracks` is
# rejected up-front so a typo doesn't silently produce empty stems.
VALID_TRACKS = ('kick', 'snare', 'hat')


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


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    """`s3://bucket/prefix/` → `('bucket', 'prefix/')`. Trailing slash
    on the prefix is preserved so callers can compute relative paths
    by simple string-strip."""
    if not uri.startswith('s3://'):
        raise ValueError(f'expected s3:// URI, got {uri!r}')
    rest = uri[len('s3://'):]
    bucket, _, prefix = rest.partition('/')
    return bucket, prefix


def ensure_oneshots_synced(verbose: bool = False) -> pathlib.Path:
    """Return a populated one-shot directory.

    Preference order:
      1. `/opt/oneshots/` baked into the container image at build time
         (Lambda fast path — no I/O on cold start).
      2. `<tmp>/oneshots/` if it's already populated (local dev cache
         from a prior run).
      3. Mirror the configured S3 bucket into `<tmp>/oneshots/` via
         boto3. Raises if boto3 / AWS credentials aren't available.
    """
    baked = _baked_oneshots()
    if baked is not None:
        return baked

    cache = _oneshot_cache()
    cache.mkdir(parents=True, exist_ok=True)
    if any(cache.iterdir()):
        return cache

    uri = _oneshot_s3_uri()
    bucket, prefix = _parse_s3_uri(uri)
    log.info('Syncing one-shot samples from %s ...', uri)

    # Local import: boto3 is in the Lambda base image but isn't
    # required for the WAV-only path, so don't pay the import cost
    # unless the JSON path actually needs the sync.
    import boto3  # type: ignore

    s3 = boto3.client('s3')
    paginator = s3.get_paginator('list_objects_v2')
    n = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents') or []:
            key = obj['Key']
            rel = key[len(prefix):] if prefix and key.startswith(prefix) else key
            if not rel or rel.endswith('/'):
                continue
            dest = cache / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(dest))
            n += 1
            if verbose:
                log.info('  %s → %s', key, dest)
    log.info('Synced %d one-shot files', n)
    return cache


def render_json_to_wav(
    json_path: pathlib.Path,
    out_path: pathlib.Path,
    *,
    target_bpm: float,
    target_sample_rate: int,
    oneshots_base: pathlib.Path,
    num_bars: int = 2,
    track: Optional[str] = None,
) -> pathlib.Path:
    """Render a beatwav pattern JSON to a WAV at `out_path`.

    The pattern's recorded BPM is ignored; the output renders at
    `target_bpm`. `target_sample_rate` is the device's native rate
    (44.1 kHz for OT, 96 kHz for S-4) so the export skips a resample.

    If `track` is set (`'kick'` | `'snare'` | `'hat'`), only hits of
    that drum type are rendered — the other tracks fall to silence.
    Used to produce per-track stems for the OT export targets.
    """
    # Local import: beatwav is only needed in JSON mode, so WAV-only
    # users don't have to install it.
    from beatwav import AudioRenderer

    if track is not None and track not in VALID_TRACKS:
        raise ValueError(f'track must be one of {VALID_TRACKS}, got {track!r}')

    renderer = AudioRenderer(
        sample_rate=target_sample_rate,
        samples_base=str(oneshots_base),
    )
    pattern = json.loads(pathlib.Path(json_path).read_text())
    normalised = renderer.normalize_pattern(pattern, num_bars)
    hits = renderer.convert_to_hits(normalised, target_bpm)
    if track is not None:
        hits = [h for h in hits if h.get('type') == track]
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
    tracks: Optional[Iterable[str]] = None,
) -> Union[Dict[str, pathlib.Path], Dict[str, Dict[str, pathlib.Path]]]:
    """Return local WAV paths for every requested break.

    `source='wav'`: fetch and cache the gist's WAVs as-is. Returns
    `{name: path}`. Per-track is not supported — the gist's WAVs are
    pre-mixed.

    `source='json'`: fetch each break's JSON, render to WAV at
    `target_bpm` / `target_sample_rate`, cache. Per-break fallback to
    WAV when the gist has no `{name}.json` (older gists), with a
    warning so the user knows.

    If `tracks` is supplied (e.g. `('kick', 'snare', 'hat')`), each
    break is rendered once per track (filtering matched_hits by drum
    type) and the return value is `{name: {track: path}}`. Requires
    `source='json'`.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f'source must be one of {VALID_SOURCES}, got {source!r}')

    if tracks is not None:
        tracks = tuple(tracks)
        if source != 'json':
            raise ValueError(
                'per-track rendering requires source="json"; '
                f'got source={source!r}'
            )
        bad = [t for t in tracks if t not in VALID_TRACKS]
        if bad:
            raise ValueError(
                f'tracks must be a subset of {VALID_TRACKS}, got bad: {bad}'
            )

    cache_dir = _samples_cache() / gist_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    wav_urls, gist_base = fetch_manifest(gist_user, gist_id)
    missing = [n for n in names if n not in wav_urls]
    if missing:
        sys.exit(f'sample gist missing breaks: {missing}')

    if source == 'wav':
        out_flat: Dict[str, pathlib.Path] = {}
        for name in names:
            out_flat[name] = cache_wav(name, wav_urls[name], cache_dir)
        return out_flat

    rendered_dir = (
        cache_dir / 'rendered'
        / f'sr{target_sample_rate}_bpm{int(round(target_bpm))}'
    )

    if tracks is None:
        return _resolve_mixed(
            names, gist_id, gist_base, wav_urls,
            cache_dir, rendered_dir,
            target_bpm, target_sample_rate, num_bars,
        )

    return _resolve_per_track(
        names, tracks, gist_id, gist_base,
        cache_dir, rendered_dir,
        target_bpm, target_sample_rate, num_bars,
    )


def _resolve_mixed(
    names, gist_id, gist_base, wav_urls,
    cache_dir, rendered_dir,
    target_bpm, target_sample_rate, num_bars,
) -> Dict[str, pathlib.Path]:
    """JSON-source resolution for the single mixed-stem path."""
    out: Dict[str, pathlib.Path] = {}
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


def _resolve_per_track(
    names, tracks, gist_id, gist_base,
    cache_dir, rendered_dir,
    target_bpm, target_sample_rate, num_bars,
) -> Dict[str, Dict[str, pathlib.Path]]:
    """JSON-source resolution for the per-track-stem path. JSON-only —
    we can't decompose a pre-mixed gist WAV into kick/snare/hat
    after-the-fact, so any break missing its JSON fails loudly."""
    needs_render = []
    out: Dict[str, Dict[str, pathlib.Path]] = {}

    for name in names:
        per_track: Dict[str, pathlib.Path] = {}
        json_needed = False
        for track in tracks:
            # Flat <name>__<track>.wav layout so basenames stay unique
            # — the OT slot manager dedupes by basename.
            rendered_path = rendered_dir / f'{name}__{track}.wav'
            per_track[track] = rendered_path
            if not rendered_path.exists():
                json_needed = True
        out[name] = per_track
        if not json_needed:
            continue
        json_path = cache_json(name, gist_base, cache_dir)
        if json_path is None:
            sys.exit(
                f'gist {gist_id} has no {name}.json — per-track '
                f'rendering needs the JSON pattern, source WAV is '
                f'pre-mixed and can\'t be split'
            )
        for track in tracks:
            rendered_path = per_track[track]
            if rendered_path.exists():
                continue
            needs_render.append((name, track, json_path, rendered_path))

    if needs_render:
        oneshots_base = ensure_oneshots_synced()
        for name, track, json_path, rendered_path in needs_render:
            log.info(
                'Rendering %s.json [%s] @ %s BPM, %d Hz → %s',
                name, track, target_bpm, target_sample_rate, rendered_path,
            )
            render_json_to_wav(
                json_path, rendered_path,
                target_bpm=target_bpm,
                target_sample_rate=target_sample_rate,
                oneshots_base=oneshots_base,
                num_bars=num_bars,
                track=track,
            )

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

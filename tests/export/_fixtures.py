"""Shared fixtures for the export-target test suite.

Each renderer is reachable as a normal package under `app.export.<target>`
(`octatrack.ot_basic`, `octatrack.ot_doom`, `strudel`, `torso_s4`). The
sample-source layer is stubbed per-test so nothing reaches the gist or
S3; we synthesise tiny sine wavs locally and the stub hands those back
to `resolve_break_paths`.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys
import tempfile
import wave
from typing import Dict, List, Optional


SAMPLE_RATE = 44100


def load_render_module(target: str):
    """Import `app.export.<target>.render`. The legacy slash-and-hyphen
    form (`octatrack/ot-basic`, `torso-s4`) is accepted and translated
    so callers don't have to know the on-disk Python package layout.

    Tests monkey-patch module-level constants like `OUTPUT_DIR` /
    `RENDER_DIR` on the returned module. `import_module` returns the
    cached module, so monkey-patches set in one test stick around — but
    every test resets the constants before use, so the cached state is
    harmless.
    """
    parts = [p.replace('-', '_') for p in target.split('/')]
    return importlib.import_module('.'.join(['app.export', *parts, 'render']))


def write_sine_wav(path: pathlib.Path, freq: float, duration_s: float) -> None:
    """Write a silent mono 16-bit 44.1 kHz WAV of `duration_s` at `path`.

    The name is historical — fixture callers used to want frequency
    differentiation. They don't: the OT slot manager dedupes by path,
    not by audio bytes, and the per-track helper already gives every
    `(name, track)` pair its own filename. So the fastest correct
    fixture is an all-zero buffer — sub-ms per WAV vs. tens of ms
    for the per-frame sine loop.

    `freq` is kept in the signature for callers that pass it; it's
    ignored.
    """
    del freq  # unused — see docstring
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_s * SAMPLE_RATE)
    silence = b'\x00\x00' * n_frames
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(silence)


def make_break_wavs(
    dest_dir: pathlib.Path,
    names: List[str],
    bpm: int = 120,
    steps: int = 32,
) -> Dict[str, pathlib.Path]:
    """Synthesise one sine WAV per name. Each wav is `steps` 1/16-notes
    long at the given bpm — i.e. the canonical 2-bar source-break
    duration when steps=32.
    """
    duration_s = steps * 60.0 / bpm / 4
    paths: Dict[str, pathlib.Path] = {}
    for i, name in enumerate(names):
        freq = 220.0 * (1.05946 ** i)  # walk a chromatic scale for distinguishability
        path = dest_dir / f'{name}.wav'
        write_sine_wav(path, freq, duration_s)
        paths[name] = path
    return paths


def make_per_track_break_wavs(
    dest_dir: pathlib.Path,
    names: List[str],
    tracks: tuple = ('kick', 'snare', 'hat'),
    bpm: int = 120,
    steps: int = 32,
) -> Dict[str, Dict[str, pathlib.Path]]:
    """Synthesise one sine WAV per (name, track) pair so per-stem slots
    registered with `add_sample` get distinct paths and don't dedupe.
    Each (name, track) gets a unique frequency.

    Filename layout: `<name>__<track>.wav` so basenames stay unique
    inside one OT project's slot pool.
    """
    duration_s = steps * 60.0 / bpm / 4
    out: Dict[str, Dict[str, pathlib.Path]] = {}
    for i, name in enumerate(names):
        per_track: Dict[str, pathlib.Path] = {}
        for j, track in enumerate(tracks):
            freq = 220.0 * (1.05946 ** (i * len(tracks) + j))
            path = dest_dir / f'{name}__{track}.wav'
            write_sine_wav(path, freq, duration_s)
            per_track[track] = path
        out[name] = per_track
    return out


def make_capture_cell(
    break_names: List[str],
    pattern_idxs: List[Optional[int]],
) -> dict:
    """Tempera-shaped capture cell."""
    return {
        't': 0,
        'seed': 0,
        'sliders': {'rootBreak': 0, 'altBreak': 0, 'pattern': 0, 'prob': 0},
        'break': break_names,
        'pattern': pattern_idxs,
    }


def make_export(
    banks: List[List[dict]],
    bpm: int = 120,
    events_per_cycle: int = 8,
    n_slices: int = 16,
    gist_id: str = 'TESTGIST',
) -> dict:
    """Tempera-shaped export payload (schema 7)."""
    return {
        'schema': 7,
        'context': {
            'gistUser': 'test',
            'gistId': gist_id,
            'bpm': bpm,
            'beatsPerCycle': 4,
            'loopCycles': 2,
            'nSlices': n_slices,
            'eventsPerCycle': events_per_cycle,
            'nBreaks': 1,
            'nPatterns': 1,
            'nProbs': 1,
        },
        'banks': banks,
    }


def stub_sample_source(name_to_path):
    """Replace `common.sample_source.resolve_break_paths` with a stub that
    returns pre-supplied local files instead of hitting the gist or
    rendering JSON.

    `name_to_path` may be either:
    - flat `{name: Path}` — used for mixed-stem renders (or when the
      caller doesn't ask for `tracks`); per-track requests reuse the
      same path across all tracks (fine for plumbing-only tests).
    - nested `{name: {track: Path}}` — distinct path per stem so
      `project.add_sample` registers separate flex slots; required by
      tests that walk per-track slot/sample-lock wiring.

    Returns a teardown callable that restores the original — call it
    from a `finally` (or use `WorkDir.stub_sources`, which wires
    teardown into the WorkDir's exit).
    """
    from app.export.common import sample_source

    original = sample_source.resolve_break_paths

    def _flat(name):
        v = name_to_path[name]
        if isinstance(v, dict):
            # Nested input but caller asked for the flat form — pick
            # an arbitrary track. Tests that need flat output should
            # pass flat input.
            raise ValueError(
                f'stub configured per-track for {name!r}; mixed-stem '
                f'request not supported in this stub'
            )
        return v

    def _per_track(name, tracks):
        v = name_to_path[name]
        if isinstance(v, dict):
            return {t: v[t] for t in tracks}
        return {t: v for t in tracks}

    def fake_resolve(*, gist_user, gist_id, names, source,
                     target_bpm, target_sample_rate, num_bars=2,
                     tracks=None):
        if tracks is None:
            return {n: _flat(n) for n in names}
        return {n: _per_track(n, tracks) for n in names}

    sample_source.resolve_break_paths = fake_resolve

    def restore():
        sample_source.resolve_break_paths = original

    return restore


class WorkDir:
    """Context manager that creates an ephemeral working tree:
    samples/, an export.json, and an output dir. Cleans up on exit
    unless `keep=True`. Also tracks any sample-source stubs registered
    via `stub_sources` so they're torn down with the WorkDir."""

    def __init__(self, keep: bool = False):
        self._tmp = None
        self.keep = keep
        self._teardowns = []

    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix='strudelbreaks-test-')
        root = pathlib.Path(self._tmp)
        self.root = root
        self.samples = root / 'samples'
        self.export_path = root / 'export.json'
        self.samples.mkdir()
        return self

    def __exit__(self, *exc):
        for teardown in reversed(self._teardowns):
            try:
                teardown()
            except Exception:
                pass
        self._teardowns.clear()
        if not self.keep:
            import shutil
            shutil.rmtree(self._tmp, ignore_errors=True)

    def write_export(self, payload: dict) -> pathlib.Path:
        self.export_path.write_text(json.dumps(payload))
        return self.export_path

    def stub_sources(self, name_to_path: Dict[str, pathlib.Path]) -> None:
        """Stub the shared sample_source for the lifetime of this WorkDir."""
        self._teardowns.append(stub_sample_source(name_to_path))

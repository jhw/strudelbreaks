"""Shared fixtures for the export-target test suite.

Each renderer is reachable from `<repo>/scripts/export/<target>/render.py`.
Two of the targets (octatrack, strudel) live in importable directory names;
the third (`ot-doom`) has a hyphen, which Python won't import as a package.
`load_render_module` handles both: it injects `scripts/export` and the
target's own directory into `sys.path` so that `import render` works.

The renderers all do remote work in production (fetch a sample manifest
from a gist, download wavs). Tests stub those out. We synthesise tiny
sine wavs locally and stand up a manifest pointing at them.
"""
from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import struct
import sys
import tempfile
import wave
from typing import Dict, List, Optional


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
EXPORT_ROOT = REPO_ROOT / 'scripts' / 'export'

SAMPLE_RATE = 44100


def load_render_module(target: str):
    """Load `scripts/export/<target>/render.py` as a fresh module each
    call so tests don't share monkey-patch state.

    Targets containing a hyphen (`ot-doom`) can't be imported as
    packages, so we go through importlib.util directly. We also
    prepend `scripts/export` to sys.path so the renderer's own
    `from common.cli import ...` and `from audio import ...` lines
    resolve.
    """
    target_dir = EXPORT_ROOT / target
    render_path = target_dir / 'render.py'
    for path in (str(EXPORT_ROOT), str(target_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)
    mod_name = f'_test_render_{target.replace("-", "_")}'
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, render_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_sine_wav(path: pathlib.Path, freq: float, duration_s: float) -> None:
    """Synthesise a mono 16-bit 44.1 kHz sine WAV at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_s * SAMPLE_RATE)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for i in range(n_frames):
            v = int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
            w.writeframes(struct.pack('<h', v))


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


def stub_sample_fetch(render_module, name_to_path: Dict[str, pathlib.Path]) -> None:
    """Patch `fetch_sample_manifest` and `cache_sample` on a renderer
    so it reads our synthetic wavs instead of hitting the gist."""

    def fake_fetch(_user, _gid):
        return {n: str(p) for n, p in name_to_path.items()}

    def fake_cache(name, url, _cache_dir):
        p = pathlib.Path(url)
        if not p.exists():
            raise FileNotFoundError(p)
        return p

    render_module.fetch_sample_manifest = fake_fetch
    render_module.cache_sample = fake_cache


class WorkDir:
    """Context manager that creates an ephemeral working tree:
    samples/, an export.json, and an output dir. Cleans up on exit
    unless `keep=True`."""

    def __init__(self, keep: bool = False):
        self._tmp = None
        self.keep = keep

    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix='strudelbreaks-test-')
        root = pathlib.Path(self._tmp)
        self.root = root
        self.samples = root / 'samples'
        self.export_path = root / 'export.json'
        self.samples.mkdir()
        return self

    def __exit__(self, *exc):
        if not self.keep:
            import shutil
            shutil.rmtree(self._tmp, ignore_errors=True)

    def write_export(self, payload: dict) -> pathlib.Path:
        self.export_path.write_text(json.dumps(payload))
        return self.export_path

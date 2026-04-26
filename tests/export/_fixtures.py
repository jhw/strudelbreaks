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

    Targets containing a hyphen (`ot-doom`, `torso-s4`) can't be
    imported as packages, so we go through importlib.util directly.
    We prepend `scripts/export` to sys.path so the renderer's own
    `from common.cli import ...` resolves, and we put the target's
    own dir at the *front* of sys.path before each load so its sibling
    `audio` module wins over any cached one from a previous target —
    several targets ship their own audio.py, and Python's import cache
    would otherwise hand back whichever one loaded first.
    """
    target_dir = EXPORT_ROOT / target
    render_path = target_dir / 'render.py'
    if str(EXPORT_ROOT) not in sys.path:
        sys.path.insert(0, str(EXPORT_ROOT))
    target_path = str(target_dir)
    while target_path in sys.path:
        sys.path.remove(target_path)
    sys.path.insert(0, target_path)
    # Evict any cached sibling modules — each target ships its own
    # audio.py, and we don't want the previous target's version to
    # bleed into this load.
    for cached in ('audio',):
        sys.modules.pop(cached, None)
    mod_name = f'_test_render_{target.replace("-", "_")}'
    sys.modules.pop(mod_name, None)
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


def stub_sample_source(name_to_path: Dict[str, pathlib.Path]):
    """Replace `common.sample_source.resolve_break_paths` with a stub that
    returns pre-supplied local files instead of hitting the gist or
    rendering JSON. Returns a teardown callable that restores the
    original — call it from a `finally` (or use `WorkDir.stub_sources`,
    which wires teardown into the WorkDir's exit)."""
    if str(EXPORT_ROOT) not in sys.path:
        sys.path.insert(0, str(EXPORT_ROOT))
    from common import sample_source

    original = sample_source.resolve_break_paths

    def fake_resolve(*, gist_user, gist_id, names, source,
                     target_bpm, target_sample_rate, num_bars=2):
        return {n: name_to_path[n] for n in names}

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

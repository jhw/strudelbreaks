"""Render-target coordinator.

Each per-target render module under `app/export/<target>/` exposes a
`render(export_path, name, *, output_dir, ...)` function. This module
wraps them with the same shape: take the in-memory captures payload,
materialise it as a temp .json, point the renderer at a fresh temp
output dir, read the artifact bytes (or text) back, return.

The temp dir is torn down on return so nothing accumulates locally —
the browser saves the response to `~/Downloads/`, which is the only
persistent output location.
"""
from __future__ import annotations

import json
import pathlib
import random
import shutil
import tempfile

from app.export.common.names import generate_name
from app.export.octatrack.ot_basic import render as ot_basic_render
from app.export.octatrack.ot_doom import render as ot_doom_render
from app.export.strudel import render as strudel_render
from app.export.torso_s4 import render as torso_s4_render


def resolve_name(name: str | None, seed: int | None) -> str:
    """Fall back to a deterministic adjective-noun if the client didn't
    supply one."""
    if name:
        return name
    rng = random.Random(seed) if seed is not None else None
    return generate_name(rng)


def _temp_export(payload: dict, name: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Materialise the in-memory payload as a real .json file inside a
    fresh temp dir. Returns (tmp_dir, export_json_path). Caller is
    responsible for cleaning up the tmp dir."""
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix='strudelbreaks-export-'))
    export_path = tmp_dir / f'{name}.json'
    export_path.write_text(json.dumps(payload))
    return tmp_dir, export_path


def _cleanup(tmp_dir: pathlib.Path) -> None:
    shutil.rmtree(tmp_dir, ignore_errors=True)


def export_strudel(payload: dict, name: str) -> str:
    """Return the rendered .strudel.js as text."""
    tmp_dir, export_path = _temp_export(payload, name)
    try:
        out_path = strudel_render.render(export_path, name,
                                         output_dir=tmp_dir / 'out')
        return out_path.read_text()
    finally:
        _cleanup(tmp_dir)


def export_ot_basic(payload: dict, name: str, *, probability: float = 1.0) -> bytes:
    """Return the OT-basic project .zip as bytes."""
    tmp_dir, export_path = _temp_export(payload, name)
    try:
        out_path = ot_basic_render.render(
            export_path, name,
            probability=probability,
            output_dir=tmp_dir / 'out',
        )
        return out_path.read_bytes()
    finally:
        _cleanup(tmp_dir)


def export_ot_doom(payload: dict, name: str) -> bytes:
    """Return the OT-doom project .zip as bytes."""
    tmp_dir, export_path = _temp_export(payload, name)
    try:
        out_path = ot_doom_render.render(
            export_path, name,
            output_dir=tmp_dir / 'out',
            render_dir=tmp_dir / 'render',
        )
        return out_path.read_bytes()
    finally:
        _cleanup(tmp_dir)


def export_torso_s4(payload: dict, name: str, *,
                    seed: int | None = None, source: str = 'json') -> bytes:
    """Return the Torso S-4 project .zip as bytes."""
    tmp_dir, export_path = _temp_export(payload, name)
    try:
        out_path = torso_s4_render.render(
            export_path, name,
            seed=seed,
            source=source,
            output_dir=tmp_dir / 'out',
            render_dir=tmp_dir / 'render',
        )
        return out_path.read_bytes()
    finally:
        _cleanup(tmp_dir)

"""POST /api/export/torso-s4 — render the Torso S-4 project zip."""
from __future__ import annotations

from app import exporters
from app.api import _common


def _body(body: dict) -> dict:
    payload = _common._validate_payload(body)
    name = _common._validate_name(body)
    seed = _common._validate_seed(body)
    source = _common._validate_source(body)
    resolved = exporters.resolve_name(name, seed)
    data = exporters.export_torso_s4(
        payload, resolved, seed=seed, source=source,
    )
    return _common.binary_response(data, filename=f'{resolved}.s4.zip')


def handler(event, _context=None):
    return _common.run_handler(event, _body)

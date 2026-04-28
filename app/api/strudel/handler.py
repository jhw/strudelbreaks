"""POST /api/export/strudel — render the Strudel playback template."""
from __future__ import annotations

from app import exporters
from app.api import _common


def _body(body: dict) -> dict:
    payload = _common._validate_payload(body)
    name = _common._validate_name(body)
    seed = _common._validate_seed(body)
    resolved = exporters.resolve_name(name, seed)
    text = exporters.export_strudel(payload, resolved)
    return _common.text_response(
        text,
        filename=f'{resolved}.strudel.js',
        media_type='application/javascript',
    )


def handler(event, _context=None):
    return _common.run_handler(event, _body)

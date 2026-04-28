"""POST /api/export/ot-doom — render the OT-doom project zip."""
from __future__ import annotations

from app import exporters
from app.api import _common


def _body(body: dict) -> dict:
    payload = _common._validate_payload(body)
    name = _common._validate_name(body)
    seed = _common._validate_seed(body)
    split_stems = _common._validate_split_stems(body)
    resolved = exporters.resolve_name(name, seed)
    data = exporters.export_ot_doom(
        payload, resolved, split_stems=split_stems,
    )
    return _common.binary_response(data, filename=f'{resolved}.ot.zip')


def handler(event, _context=None):
    return _common.run_handler(event, _body)

"""POST /api/export/ot-basic — render the OT-basic project zip."""
from __future__ import annotations

from app import exporters
from app.api import _common


def _body(body: dict) -> dict:
    payload = _common._validate_payload(body)
    name = _common._validate_name(body)
    seed = _common._validate_seed(body)
    probability = _common._validate_probability(body)
    flatten = _common._validate_flatten(body)
    resolved = exporters.resolve_name(name, seed)
    data = exporters.export_ot_basic(
        payload, resolved,
        probability=probability,
        flatten=flatten,
    )
    return _common.binary_response(data, filename=f'{resolved}.ot.zip')


def handler(event, _context=None):
    return _common.run_handler(event, _body)

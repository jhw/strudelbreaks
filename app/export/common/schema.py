"""Schema-gated loader for tempera captures exports.

The tempera template persists captures with `{ schema, context, banks }`
shape. Each target render loads the export the same way — validate the
schema version, assert required context fields are present — so the
logic lives here.
"""
from __future__ import annotations

import json
import pathlib
import sys


SCHEMA_EXPECTED = 7


def load_export(path: pathlib.Path, required_ctx_fields: tuple[str, ...]):
    """Parse and validate an export. Exits the process on schema mismatch
    or missing context fields so every target script fails identically.
    Returns (payload, ctx)."""
    payload = json.loads(path.read_text())
    schema = payload.get('schema')
    if schema != SCHEMA_EXPECTED:
        sys.exit(f'schema mismatch: got {schema}, expected {SCHEMA_EXPECTED}')
    ctx = payload.get('context') or {}
    for key in required_ctx_fields:
        if key not in ctx:
            sys.exit(f'context missing field: {key}')
    return payload, ctx

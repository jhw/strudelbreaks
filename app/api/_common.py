"""Shared request parsing / response building for the four export
handlers. Each handler still owns its own field validators (one
`_validate_*` function per field group) — this module just covers
the boilerplate every handler runs on every request: body decode,
JSON parse, base64 encoding for binary responses, error mapping.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Tuple

log = logging.getLogger(__name__)

# API Gateway HTTP API sync response cap is 10 MB; Lambda's own
# response cap is 6 MB. Take the tighter of the two and reject
# eagerly with a 413 + actionable message rather than letting API
# Gateway truncate.
MAX_RESPONSE_BYTES = 6 * 1024 * 1024


def parse_body(event: dict) -> Any:
    """Decode + JSON-parse `event['body']`. API Gateway base64-encodes
    binary request bodies; we accept both forms."""
    body = event.get('body')
    if body is None:
        raise ValueError('missing request body')
    if event.get('isBase64Encoded'):
        body = base64.b64decode(body).decode('utf-8')
    if isinstance(body, (bytes, bytearray)):
        body = body.decode('utf-8')
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f'invalid JSON body: {e}') from e


def _require(body: dict, key: str) -> Any:
    if key not in body:
        raise ValueError(f'missing required field: {key!r}')
    return body[key]


def _validate_payload(body: dict) -> dict:
    payload = _require(body, 'payload')
    if not isinstance(payload, dict):
        raise ValueError('payload must be an object')
    return payload


def _validate_name(body: dict) -> str | None:
    name = body.get('name')
    if name is None:
        return None
    if not isinstance(name, str) or not name:
        raise ValueError('name must be a non-empty string')
    return name


def _validate_seed(body: dict) -> int | None:
    seed = body.get('seed')
    if seed is None:
        return None
    if not isinstance(seed, int):
        raise ValueError('seed must be an integer')
    return seed


def _validate_split_stems(body: dict) -> bool:
    v = body.get('split_stems', True)
    if not isinstance(v, bool):
        raise ValueError('split_stems must be a boolean')
    return v


def _validate_probability(body: dict) -> float:
    v = body.get('probability', 1.0)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ValueError('probability must be a number')
    if not 0.0 <= float(v) <= 1.0:
        raise ValueError('probability must be in [0, 1]')
    return float(v)


def _validate_source(body: dict) -> str:
    v = body.get('source', 'json')
    if v not in ('json', 'wav'):
        raise ValueError("source must be 'json' or 'wav'")
    return v


def text_response(text: str, *, filename: str, media_type: str) -> dict:
    encoded = text.encode('utf-8')
    if len(encoded) > MAX_RESPONSE_BYTES:
        return error_response(413, f'response exceeds {MAX_RESPONSE_BYTES} bytes')
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': media_type,
            'Content-Disposition': f'attachment; filename="{filename}"',
        },
        'body': text,
    }


def binary_response(data: bytes, *, filename: str) -> dict:
    if len(data) > MAX_RESPONSE_BYTES:
        return error_response(
            413,
            f'response exceeds {MAX_RESPONSE_BYTES} bytes — try fewer rows',
        )
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/zip',
            'Content-Disposition': f'attachment; filename="{filename}"',
        },
        'body': base64.b64encode(data).decode('ascii'),
        'isBase64Encoded': True,
    }


def error_response(status: int, message: str) -> dict:
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'text/plain; charset=utf-8'},
        'body': message,
    }


def run_handler(event: dict, fn) -> dict:
    """Wrap a per-target handler body so every target shares the same
    auth gate, validation-error → 400, runtime-error → 500 mapping."""
    from app.api._auth import check_auth, unauthorized
    if not check_auth(event):
        return unauthorized()
    try:
        body = parse_body(event)
    except ValueError as e:
        return error_response(400, str(e))
    if not isinstance(body, dict):
        return error_response(400, 'request body must be a JSON object')
    try:
        return fn(body)
    except ValueError as e:
        return error_response(400, str(e))
    except SystemExit as e:
        return error_response(400, str(e))
    except Exception:
        log.exception('handler crashed')
        return error_response(500, 'internal error')

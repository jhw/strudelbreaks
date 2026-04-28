"""HTTP Basic auth shared by every export handler.

The Lambda env carries `AUTH_TOKEN=username:password`; the handler
decodes the inbound `Authorization: Basic <b64>` header and
string-compares. If `AUTH_TOKEN` is unset (local invocation, tests)
auth is disabled — the bare check_auth call returns True so handlers
stay drop-in invocable without a deploy round-trip.
"""
from __future__ import annotations

import base64
import os


def check_auth(event: dict) -> bool:
    expected = os.environ.get('AUTH_TOKEN')
    if not expected:
        return True
    headers = event.get('headers') or {}
    auth = headers.get('authorization') or headers.get('Authorization') or ''
    if not auth.startswith('Basic '):
        return False
    try:
        return base64.b64decode(auth[6:]).decode('utf-8') == expected
    except Exception:
        return False


def unauthorized() -> dict:
    return {
        'statusCode': 401,
        'headers': {'WWW-Authenticate': 'Basic'},
        'body': 'Unauthorized',
    }

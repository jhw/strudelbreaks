"""GET /launch — bookmarkable redirect into strudel.cc with the
tempera capture template baked into the URL hash.

The handler reads the bundled tempera.strudel.js, optionally rewrites
the `gistUser` / `gistId` literals from query params, base64s the
UTF-8 bytes, and returns a 302 to `https://strudel.cc/#<base64>` —
strudel.cc decodes the hash on first paint and drops the program
straight into the editor.

    GET /launch                          # default gistUser / gistId
    GET /launch?gistId=<id>              # override gistId only
    GET /launch?gistUser=foo&gistId=abc  # override both

Public endpoint — no auth (the script source has no secrets; the
deployed export endpoints behind /api/export/* are the auth boundary).
"""
from __future__ import annotations

import base64
import logging
import pathlib
import re

from app.api import _common


log = logging.getLogger(__name__)

# tempera.strudel.js is COPYed into the image at /var/task/app/launch/.
TEMPERA_PATH = pathlib.Path(__file__).resolve().parents[2] / 'launch' / 'tempera.strudel.js'

# Mirror app/launch/route.py's input validation: GitHub usernames are
# 1-39 alphanumeric chars + internal hyphens; gist ids are 20-32 hex
# in practice but allow alnum 1-64 to be permissive. We splice these
# into JS source via a literal-replace, so any input that isn't matched
# by the regex is rejected to avoid escape issues.
USER_RX = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$')
GIST_ID_RX = re.compile(r'^[A-Za-z0-9]{1,64}$')

GIST_USER_LINE = re.compile(r"const gistUser = '[^']*';")
GIST_ID_LINE = re.compile(r"const gistId = '[^']*';")


def _redirect(url: str) -> dict:
    return {
        'statusCode': 302,
        'headers': {'Location': url, 'Cache-Control': 'no-store'},
        'body': '',
    }


def handler(event, _context=None):
    qs = event.get('queryStringParameters') or {}
    gist_user = qs.get('gistUser')
    gist_id = qs.get('gistId')

    if gist_user is not None and not USER_RX.match(gist_user):
        return _common.error_response(400, f'invalid gistUser: {gist_user!r}')
    if gist_id is not None and not GIST_ID_RX.match(gist_id):
        return _common.error_response(400, f'invalid gistId: {gist_id!r}')

    try:
        src = TEMPERA_PATH.read_text()
    except FileNotFoundError:
        log.exception('tempera.strudel.js not found at %s', TEMPERA_PATH)
        return _common.error_response(500, 'tempera.strudel.js not bundled')

    if gist_user is not None:
        src, n = GIST_USER_LINE.subn(
            f"const gistUser = '{gist_user}';", src, count=1)
        if n == 0:
            return _common.error_response(500, 'gistUser literal not found in tempera.strudel.js')
    if gist_id is not None:
        src, n = GIST_ID_LINE.subn(
            f"const gistId = '{gist_id}';", src, count=1)
        if n == 0:
            return _common.error_response(500, 'gistId literal not found in tempera.strudel.js')

    encoded = base64.b64encode(src.encode('utf-8')).decode('ascii')
    return _redirect(f'https://strudel.cc/#{encoded}')

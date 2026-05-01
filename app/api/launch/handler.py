"""GET /launch — bookmarkable redirect into strudel.cc with the
tempera capture template baked into the URL hash.

The handler reads the bundled tempera.strudel.js, rewrites four
configurable `const` literals (gistUser, gistId, BPM, SEED), base64s
the UTF-8 bytes, and returns a 302 to `https://strudel.cc/#<base64>`
— strudel.cc decodes the hash on first paint and drops the program
straight into the editor.

Resolution order for each field (most specific wins):
  1. Query string             — `?gistUser=foo&gistId=abc&bpm=140&seed=42`
  2. Lambda env var           — `LAUNCH_GIST_USER`, `LAUNCH_GIST_ID`,
                                `LAUNCH_BPM`, `LAUNCH_SEED`
                                (set by infra/app from Pulumi config)
  3. Literal in tempera.strudel.js — the committed defaults

Auth: HTTP Basic via the shared `AUTH_TOKEN` env var, same as the
export routes. Browsers follow `WWW-Authenticate: Basic` with a
credential prompt and re-try the GET, so a bookmarked URL "just
works" once the user enters credentials once.
"""
from __future__ import annotations

import base64
import logging
import os
import pathlib
import re

from app.api import _auth, _common


log = logging.getLogger(__name__)

# tempera.strudel.js is COPYed into the image at /var/task/app/launch/.
TEMPERA_PATH = pathlib.Path(__file__).resolve().parents[2] / 'launch' / 'tempera.strudel.js'

# Validation regexes — applied to whichever source supplies the value
# (env or query string). We splice these into JS source via a
# literal-replace, so anything not matched is rejected up-front.
USER_RX = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$')
GIST_ID_RX = re.compile(r'^[A-Za-z0-9]{1,64}$')
INT_RX = re.compile(r'^[0-9]{1,10}$')

# The four `const X = ...;` lines in tempera.strudel.js that the
# launch handler rewrites. Each entry is
# (override key, regex matching the line, formatter for the new line,
#  validator regex applied to the override value).
REWRITES = [
    ('gistUser',
     re.compile(r"const gistUser = '[^']*';"),
     lambda v: f"const gistUser = '{v}';",
     USER_RX),
    ('gistId',
     re.compile(r"const gistId = '[^']*';"),
     lambda v: f"const gistId = '{v}';",
     GIST_ID_RX),
    ('bpm',
     re.compile(r"const BPM = \d+;"),
     lambda v: f"const BPM = {v};",
     INT_RX),
    ('seed',
     re.compile(r"const SEED = \d+;"),
     lambda v: f"const SEED = {v};",
     INT_RX),
]

# Per-field env var keys. Read at request time so a Pulumi config
# update + Lambda redeploy lands without code changes.
ENV_VARS = {
    'gistUser': 'LAUNCH_GIST_USER',
    'gistId':   'LAUNCH_GIST_ID',
    'bpm':      'LAUNCH_BPM',
    'seed':     'LAUNCH_SEED',
}


def _redirect(url: str) -> dict:
    return {
        'statusCode': 302,
        'headers': {'Location': url, 'Cache-Control': 'no-store'},
        'body': '',
    }


def handler(event, _context=None):
    if not _auth.check_auth(event):
        return _auth.unauthorized()

    qs = event.get('queryStringParameters') or {}

    # Resolve each field: query > env > leave the file's default in place.
    resolved: dict[str, str] = {}
    for key, _line_rx, _fmt, value_rx in REWRITES:
        v = qs.get(key) or os.environ.get(ENV_VARS[key])
        if v is None or v == '':
            continue
        if not value_rx.match(v):
            return _common.error_response(400, f'invalid {key}: {v!r}')
        resolved[key] = v

    try:
        src = TEMPERA_PATH.read_text()
    except FileNotFoundError:
        log.exception('tempera.strudel.js not found at %s', TEMPERA_PATH)
        return _common.error_response(500, 'tempera.strudel.js not bundled')

    for key, line_rx, fmt, _value_rx in REWRITES:
        if key not in resolved:
            continue
        src, n = line_rx.subn(fmt(resolved[key]), src, count=1)
        if n == 0:
            return _common.error_response(
                500, f'{key} literal not found in tempera.strudel.js'
            )

    encoded = base64.b64encode(src.encode('utf-8')).decode('ascii')
    return _redirect(f'https://strudel.cc/#{encoded}')

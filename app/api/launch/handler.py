"""GET /launch — bookmarkable redirect into strudel.cc with the
tempera capture template baked into the URL hash.

The handler reads the bundled tempera.strudel.js, rewrites a handful
of configurable `const` literals (gistUser, gistId, BPM, SEED,
SERVER_URL, AUTH_HEADER), base64s the UTF-8 bytes, and returns a 302
to `https://strudel.cc/#<base64>` — strudel.cc decodes the hash on
first paint and drops the program straight into the editor.

Resolution order for the user-overridable fields (most specific wins):
  1. Query string    — `?gistUser=foo&gistId=abc&bpm=140&seed=42`
  2. Stored defaults — last validated qs values, persisted as
                       `s3://<artifacts-bucket>/launch-defaults/global.json`
                       (see LAUNCH_DEFAULTS_S3_URI). Single global
                       blob; last visitor wins for everyone.
  3. Lambda env      — `LAUNCH_GIST_USER`, `LAUNCH_GIST_ID`,
                       `LAUNCH_BPM`, `LAUNCH_SEED` (Pulumi config →
                       Lambda env)
  4. Committed default in tempera.strudel.js

`SERVER_URL` and `AUTH_HEADER` are computed from request context:
  - `SERVER_URL` is `https://{requestContext.domainName}` — the
    same custom domain the user just authenticated against. No
    need for a separate config knob.
  - `AUTH_HEADER` is `Basic {base64(AUTH_TOKEN)}`, computed from
    the same env var the export handlers gate on. /launch is
    auth-gated, so anyone reading the template has already proved
    they have the token; baking it in saves tempera a redundant
    prompt.

Auth: HTTP Basic via `AUTH_TOKEN`. Browsers follow
`WWW-Authenticate: Basic` with a credential prompt + retry, so a
bookmarked URL "just works" once the user enters credentials once.
"""
from __future__ import annotations

import base64
import json
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
# Match the deployed-server URL we'll splice in. Restrict to https://
# (we only ever bake the API Gateway URL, never plain http) and the
# alnum + .-_ host charset to keep the substitution safe.
URL_RX = re.compile(r'^https://[A-Za-z0-9._-]+(?::[0-9]+)?$')
# Basic <b64>; matches what we generate from AUTH_TOKEN.
BASIC_RX = re.compile(r'^Basic [A-Za-z0-9+/=]+$')

# The `const X = ...;` lines in tempera.strudel.js that the launch
# handler rewrites. Each entry is (override key, regex matching the
# line, formatter for the new line, validator regex applied to the
# override value).
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
    ('serverUrl',
     re.compile(r"const SERVER_URL = '[^']*';"),
     lambda v: f"const SERVER_URL = '{v}';",
     URL_RX),
    ('authHeader',
     re.compile(r"const AUTH_HEADER = '[^']*';"),
     lambda v: f"const AUTH_HEADER = '{v}';",
     BASIC_RX),
]

# Per-field env var keys for user-overridable fields. Read at
# request time so a Pulumi config update + Lambda redeploy lands
# without code changes. SERVER_URL / AUTH_HEADER are computed,
# not env-controlled — see _server_url and _auth_header below.
ENV_VARS = {
    'gistUser': 'LAUNCH_GIST_USER',
    'gistId':   'LAUNCH_GIST_ID',
    'bpm':      'LAUNCH_BPM',
    'seed':     'LAUNCH_SEED',
}


# Stored-defaults S3 location. Set by infra/app/__main__.py to a
# single-key URI like `s3://<artifacts-bucket>/launch-defaults/global.json`.
# Empty (or unset) → handler skips the persistence path entirely
# (used by tests and by any non-AWS local invocation).
DEFAULTS_S3_URI_VAR = 'LAUNCH_DEFAULTS_S3_URI'


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith('s3://'):
        raise ValueError(f'expected s3:// URI, got {uri!r}')
    rest = uri[len('s3://'):]
    bucket, _, key = rest.partition('/')
    if not bucket or not key:
        raise ValueError(f'expected s3://bucket/key, got {uri!r}')
    return bucket, key


def _load_stored_defaults() -> dict[str, str]:
    """Read launch-defaults from S3. Returns `{}` on every failure
    mode (missing key, unparseable JSON, network blip, no IAM, env
    var unset) — a transient S3 error must never break the user's
    redirect, so this swallows everything and logs.

    Output is filtered to known ENV_VARS keys with str values, so a
    corrupted blob can't poison the rewrite step downstream.
    """
    uri = os.environ.get(DEFAULTS_S3_URI_VAR, '').strip()
    if not uri:
        return {}
    try:
        bucket, key = _parse_s3_uri(uri)
        # boto3 import deferred so unit tests that don't touch S3 don't
        # need it on the path. Lambda runtime always has it available.
        import boto3
        from botocore.exceptions import ClientError
        s3 = boto3.client('s3')
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code')
            if code in ('NoSuchKey', 'NoSuchBucket', '404'):
                return {}
            raise
        body = obj['Body'].read()
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            return {}
        return {
            k: str(v) for k, v in parsed.items()
            if k in ENV_VARS and isinstance(v, (str, int))
        }
    except Exception:
        log.warning('could not load stored defaults', exc_info=True)
        return {}


def _save_stored_defaults(defaults: dict[str, str]) -> None:
    """Write defaults back to S3. Best-effort — a write failure logs
    but doesn't change the response, since the user already got their
    redirect with the correct values."""
    uri = os.environ.get(DEFAULTS_S3_URI_VAR, '').strip()
    if not uri:
        return
    try:
        bucket, key = _parse_s3_uri(uri)
        import boto3
        s3 = boto3.client('s3')
        s3.put_object(
            Bucket=bucket, Key=key,
            Body=json.dumps(defaults).encode('utf-8'),
            ContentType='application/json',
        )
    except Exception:
        log.warning('could not save stored defaults', exc_info=True)


def _server_url(event: dict) -> str | None:
    """Derive `https://<request-host>` from the API Gateway request
    context. Falls back to the `Host` header for local invocation
    paths that don't carry requestContext."""
    rc = (event.get('requestContext') or {}).get('domainName')
    if rc:
        return f'https://{rc}'
    headers = event.get('headers') or {}
    host = headers.get('host') or headers.get('Host')
    if host:
        return f'https://{host}'
    return None


def _auth_header() -> str | None:
    """Pre-encode `AUTH_TOKEN` as a Basic header for tempera's POSTs.
    Returns None when AUTH_TOKEN is unset (local dev / tests) — the
    handler bakes an empty AUTH_HEADER literal in that case and
    tempera sends no Authorization header."""
    token = os.environ.get('AUTH_TOKEN')
    if not token:
        return None
    return 'Basic ' + base64.b64encode(token.encode('utf-8')).decode('ascii')


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
    stored = _load_stored_defaults()
    # Track which qs values to persist back. Only validated qs values
    # land in S3 — env defaults and committed literals stay out of the
    # stored blob so they remain easy to change centrally.
    to_persist: dict[str, str] = {}

    # Resolve each field. User-overridable: query > stored > env > skip.
    # Computed (serverUrl, authHeader): from request context / env.
    resolved: dict[str, str] = {}
    for key, _line_rx, _fmt, value_rx in REWRITES:
        if key in ENV_VARS:
            v = (qs.get(key)
                 or stored.get(key)
                 or os.environ.get(ENV_VARS[key]))
        elif key == 'serverUrl':
            v = _server_url(event)
        elif key == 'authHeader':
            v = _auth_header()
        else:
            v = None
        if v is None or v == '':
            continue
        if not value_rx.match(v):
            return _common.error_response(400, f'invalid {key}: {v!r}')
        resolved[key] = v
        # Only mark for persistence when the value came from this
        # request's query string. We persist the *validated* value, so
        # a malformed qs would have already short-circuited above.
        if key in ENV_VARS and qs.get(key):
            to_persist[key] = v

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

    if to_persist:
        # Merge the new qs values into whatever was already stored
        # (partial overrides accumulate — `?bpm=140` once doesn't wipe
        # a previously-stored gistId). Persist before redirect so a
        # very fast follow-up visit sees the freshly-stored value.
        _save_stored_defaults({**stored, **to_persist})

    encoded = base64.b64encode(src.encode('utf-8')).decode('ascii')
    return _redirect(f'https://strudel.cc/#{encoded}')

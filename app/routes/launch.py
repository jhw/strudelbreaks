"""One-click launcher for tempera.strudel.js.

`GET /launch` reads tempera.strudel.js from the repo root, optionally
rewrites the `gistUser` / `gistId` literals from query params, base64s
the UTF-8 bytes, and 302-redirects to `https://strudel.cc/#<base64>`
— strudel.cc decodes the hash on load and drops the program straight
into the editor, same mechanism the in-app `export ▾ → strudel` flow
uses.

    GET /launch                          # default gistUser / gistId
    GET /launch?gistId=<id>              # override gistId only
    GET /launch?gistUser=foo&gistId=abc  # override both

The URL is bookmarkable; cmd-click / middle-click opens in a new tab.
"""
from __future__ import annotations

import base64
import pathlib
import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse


router = APIRouter()

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPERA_PATH = REPO_ROOT / 'tempera.strudel.js'

# Validate query inputs before splicing them into JS source. GitHub
# usernames: 1-39 alphanumeric chars + internal hyphens. Gist ids in
# practice are 20-32 hex chars; allow alnum 1-64 to be permissive.
USER_RX = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$')
GIST_ID_RX = re.compile(r'^[A-Za-z0-9]{1,64}$')

GIST_USER_LINE = re.compile(r"const gistUser = '[^']*';")
GIST_ID_LINE = re.compile(r"const gistId = '[^']*';")


@router.get('/launch')
def launch(
    gistUser: str | None = Query(None),
    gistId: str | None = Query(None),
):
    if gistUser is not None and not USER_RX.match(gistUser):
        raise HTTPException(400, f'invalid gistUser: {gistUser!r}')
    if gistId is not None and not GIST_ID_RX.match(gistId):
        raise HTTPException(400, f'invalid gistId: {gistId!r}')

    src = TEMPERA_PATH.read_text()
    if gistUser is not None:
        src, n = GIST_USER_LINE.subn(
            f"const gistUser = '{gistUser}';", src, count=1)
        if n == 0:
            raise HTTPException(500, 'gistUser literal not found in tempera.strudel.js')
    if gistId is not None:
        src, n = GIST_ID_LINE.subn(
            f"const gistId = '{gistId}';", src, count=1)
        if n == 0:
            raise HTTPException(500, 'gistId literal not found in tempera.strudel.js')

    encoded = base64.b64encode(src.encode('utf-8')).decode('ascii')
    return RedirectResponse(url=f'https://strudel.cc/#{encoded}', status_code=302)

"""Text-format export endpoint.

Currently the only text target is the Strudel playback template. A
single endpoint instead of one-per-target keeps room for future
text formats (JSON passthrough, Lua, etc.) without proliferating
routes.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app import exporters

router = APIRouter()
log = logging.getLogger(__name__)

TARGETS = {
    'strudel': {
        'extension': 'strudel.js',
        'media_type': 'application/javascript',
    },
}


class TextExportBody(BaseModel):
    target: str
    payload: dict
    name: str | None = None
    seed: int | None = None


@router.post('/api/export/text')
async def export_text(body: TextExportBody):
    spec = TARGETS.get(body.target)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f'unknown text target: {body.target!r} (allowed: {sorted(TARGETS)})',
        )

    name = exporters.resolve_name(body.name, body.seed)
    log.info('export/text target=%s name=%s', body.target, name)

    try:
        if body.target == 'strudel':
            text = await asyncio.to_thread(
                exporters.export_strudel, body.payload, name,
            )
        else:
            raise HTTPException(status_code=500, detail='unreachable')
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception('text export crashed')
        raise HTTPException(status_code=500, detail=str(e))

    filename = f'{name}.{spec["extension"]}'
    return Response(
        content=text,
        media_type=spec['media_type'],
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )

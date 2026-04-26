"""Binary-format export endpoint.

Routes the three zip-emitting targets (`ot-basic`, `ot-doom`,
`torso-s4`) through one endpoint. The response body is the raw zip
bytes; the filename in `Content-Disposition` carries a target-specific
suffix (`.ot-basic.zip`, `.ot-doom.zip`, `.s4.zip`) so the push
scripts can glob `~/Downloads` without sniffing zip contents.
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

# Both OT variants land at `.ot.zip` — the device-side push tooling
# only cares it's an OT project zip; which renderer produced it is
# the user's choice at export time, not push time.
TARGETS = {
    'ot-basic': {'extension': 'ot.zip'},
    'ot-doom':  {'extension': 'ot.zip'},
    'torso-s4': {'extension': 's4.zip'},
}


class BinaryExportBody(BaseModel):
    target: str
    payload: dict
    name: str | None = None
    seed: int | None = None
    # ot-basic only
    probability: float = 1.0
    # torso-s4 only
    source: str = 'json'


@router.post('/api/export/binary')
async def export_binary(body: BinaryExportBody):
    spec = TARGETS.get(body.target)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f'unknown binary target: {body.target!r} (allowed: {sorted(TARGETS)})',
        )

    name = exporters.resolve_name(body.name, body.seed)
    log.info('export/binary target=%s name=%s', body.target, name)

    try:
        if body.target == 'ot-basic':
            data = await asyncio.to_thread(
                exporters.export_ot_basic, body.payload, name,
                probability=body.probability,
            )
        elif body.target == 'ot-doom':
            data = await asyncio.to_thread(
                exporters.export_ot_doom, body.payload, name,
            )
        elif body.target == 'torso-s4':
            data = await asyncio.to_thread(
                exporters.export_torso_s4, body.payload, name,
                seed=body.seed, source=body.source,
            )
        else:
            raise HTTPException(status_code=500, detail='unreachable')
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception('binary export crashed')
        raise HTTPException(status_code=500, detail=str(e))

    filename = f'{name}.{spec["extension"]}'
    return Response(
        content=data,
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )

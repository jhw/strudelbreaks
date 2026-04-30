"""FastAPI app for the strudelbreaks export server.

The tempera template (running at https://strudel.cc) POSTs the
in-memory captures payload to this server, which renders the chosen
target and streams the artifact back as a download. Browsers save it
to `~/Downloads`; the per-device push scripts under `scripts/octatrack/`
and `scripts/torso-s4/` then copy from there.

Localhost-only: Chrome treats `http://localhost` / `127.0.0.1` as a
secure context exempt from mixed-content blocking, so the HTTPS
strudel.cc page can fetch our HTTP server without flags. CORS is
opened wide for the same reason — anything reachable on this loopback
address is implicitly trusted.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.launch import route as launch_route
from app.routes import binary_export, text_export

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('strudelbreaks')


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info('strudelbreaks server ready on http://%s:%d',
             config.HTTP_HOST, config.HTTP_PORT)
    yield


app = FastAPI(title='strudelbreaks', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['Content-Disposition'],
)

app.include_router(text_export.router)
app.include_router(binary_export.router)
app.include_router(launch_route.router)


@app.get('/')
def root() -> dict:
    return {'ok': True, 'service': 'strudelbreaks'}

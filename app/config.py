"""Server config. Localhost-only by default — the tempera client at
strudel.cc only ever talks to a server on the same machine."""
import os

HTTP_HOST = os.getenv('STRUDELBREAKS_HTTP_HOST', '127.0.0.1')
HTTP_PORT = int(os.getenv('STRUDELBREAKS_HTTP_PORT', '8000'))

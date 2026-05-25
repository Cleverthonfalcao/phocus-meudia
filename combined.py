"""
Phocus Meu Dia — Servidor combinado
Flask (web app) + FastMCP (MCP server) em uma única porta.

URL do conector MCP: https://meudia.up.railway.app/mcp/SEU_TOKEN/sse
"""

import re
import time
import warnings
warnings.filterwarnings('ignore')

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware.wsgi import WSGIMiddleware

from app import app as flask_app
from mcp_server import mcp, mcp_token_ctx

mcp_asgi = mcp.http_app(transport='sse')

# IP do cliente → (token, timestamp)
# Vincula o token ao IP no momento da conexão SSE.
# Tool calls do mesmo IP nas próximas 2h usam esse token.
_token_by_ip: dict[str, tuple[str, float]] = {}

_starlette = Starlette(routes=[
    Mount('/mcp',      app=mcp_asgi),
    Mount('/messages', app=mcp_asgi),
    Mount('/',         app=WSGIMiddleware(flask_app)),
])


class TokenMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            await self.app(scope, receive, send)
            return

        path   = scope.get('path', '')
        client = scope.get('client') or ('', 0)
        ip     = client[0]

        # ── 1. /mcp/TOKEN/sse ────────────────────────────────────────────────────
        m = re.match(r'^/mcp/([^/]+)/sse', path)
        if m:
            token = m.group(1)
            # Registra token por IP — válido por 2 horas
            _token_by_ip[ip] = (token, time.time())
            print(f'[MW] SSE ip={ip!r} token={token!r}', flush=True)
            scope = {**scope, 'path': '/mcp/sse', 'raw_path': b'/mcp/sse'}
            await self.app(scope, receive, send)
            return

        # ── 2. /mcp/TOKEN/messages/... (endpoint reescrito pelo cliente) ─────────
        m = re.match(r'^/mcp/([^/]+)/(messages.*)', path)
        if m:
            token    = m.group(1)
            new_path = '/mcp/' + m.group(2)
            mcp_token_ctx.set(token)
            print(f'[MW] msg-url ip={ip!r} token={token!r}', flush=True)
            scope = {**scope, 'path': new_path, 'raw_path': new_path.encode()}
            await self.app(scope, receive, send)
            return

        # ── 3. /messages/... ou /mcp/messages/... — injeta token pelo IP ─────────
        if re.match(r'^(?:/mcp)?/messages/', path):
            entry = _token_by_ip.get(ip)
            if entry:
                token, ts = entry
                if time.time() - ts < 7200:          # 2 horas
                    mcp_token_ctx.set(token)
                    print(f'[MW] msg-ip ip={ip!r} token={token!r}', flush=True)
                else:
                    del _token_by_ip[ip]
                    print(f'[MW] msg-ip EXPIRED ip={ip!r}', flush=True)
            else:
                print(f'[MW] msg-ip NOT FOUND ip={ip!r} known={list(_token_by_ip.keys())}', flush=True)
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)


combined = TokenMiddleware(_starlette)

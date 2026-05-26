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

from app import app as flask_app, save_ip_token, get_token_by_ip
from mcp_server import mcp, mcp_token_ctx

mcp_asgi = mcp.http_app(transport='sse')

# Cache em memória (velocidade) + persistência no banco (sobrevive a deploys)
_token_by_ip: dict[str, tuple[str, float]] = {}

def _set_token(ip: str, token: str):
    _token_by_ip[ip] = (token, time.time())
    try:
        save_ip_token(ip, token)
    except Exception:
        pass

def _get_ip_token(ip: str) -> str:
    entry = _token_by_ip.get(ip)
    if entry:
        token, ts = entry
        if time.time() - ts < 86400:
            return token
    try:
        return get_token_by_ip(ip)
    except Exception:
        return ''


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
            _set_token(ip, token)
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
            token = _get_ip_token(ip)
            if token:
                mcp_token_ctx.set(token)
                print(f'[MW] msg-ip ip={ip!r} token={token!r}', flush=True)
            else:
                print(f'[MW] msg-ip NOT FOUND ip={ip!r}', flush=True)
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)


combined = TokenMiddleware(_starlette)

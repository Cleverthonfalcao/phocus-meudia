"""
Phocus Meu Dia — Servidor combinado
Flask (web app) + FastMCP (MCP server) em uma única porta.

URL do conector MCP: https://meudia.up.railway.app/mcp/SEU_TOKEN/sse
O middleware extrai o token ANTES do Starlette rotear — sem path rewriting bugs.
"""

import re
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

# session_id → token  (preenchido ao conectar SSE)
_sessions: dict[str, str] = {}

_starlette = Starlette(routes=[
    Mount('/mcp', app=mcp_asgi),
    Mount('/', app=WSGIMiddleware(flask_app)),
])


class TokenMiddleware:
    """
    Roda ANTES do Starlette.
    /mcp/TOKEN/sse       → reescreve para /mcp/sse + captura session_id
    /mcp/TOKEN/messages  → reescreve para /mcp/messages + injeta token
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '')

        # /mcp/TOKEN/sse → /mcp/sse
        m = re.match(r'^/mcp/([^/]+)/sse$', path)
        if m:
            token = m.group(1)
            scope = {**scope, 'path': '/mcp/sse', 'raw_path': b'/mcp/sse'}

            async def capture_send(message):
                if message.get('type') == 'http.response.body':
                    body = message.get('body', b'').decode('utf-8', errors='replace')
                    sid = re.search(r'data:\s*/messages/([^\n\r\s]+)', body)
                    if sid:
                        _sessions[sid.group(1).strip()] = token
                await send(message)

            await self.app(scope, receive, capture_send)
            return

        # /mcp/TOKEN/messages/... → /mcp/messages/...
        m = re.match(r'^/mcp/([^/]+)/(messages.*)$', path)
        if m:
            token = m.group(1)
            new_path = '/mcp/' + m.group(2)
            mcp_token_ctx.set(token)
            scope = {**scope, 'path': new_path, 'raw_path': new_path.encode()}
            await self.app(scope, receive, send)
            return

        # /mcp/messages/SESSION_ID → injeta token via registro de sessão
        m = re.match(r'^/mcp/messages/([^\s?]+)', path)
        if m:
            session_id = m.group(1)
            token = _sessions.get(session_id, '')
            if token:
                mcp_token_ctx.set(token)
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)


combined = TokenMiddleware(_starlette)

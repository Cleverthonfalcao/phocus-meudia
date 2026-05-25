"""
Phocus Meu Dia — Servidor combinado
Flask (web app) + FastMCP (MCP server) em uma única porta via Starlette router.

URL do conector MCP: https://meudia.up.railway.app/mcp/TOKEN/sse
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

# Registro global: session_id → token
# Preenchido quando o cliente conecta via SSE /TOKEN/sse
_session_tokens: dict[str, str] = {}

mcp_asgi = mcp.http_app(transport='sse')


class TokenMiddleware:
    """
    Extrai token do path /TOKEN/sse e registra no mapa session_id→token.
    Quando mensagens chegam em /messages/SESSION_ID, injeta o token no contextvar.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '').lstrip('/')
        parts = path.split('/', 1)
        first = parts[0]
        rest  = '/' + parts[1] if len(parts) > 1 else '/'

        # ── Conexão SSE: /TOKEN/sse ──────────────────────────────────────────
        if rest == '/sse' and first not in ('', 'sse', 'messages'):
            token = first
            scope = {**scope, 'path': '/sse', 'raw_path': b'/sse'}

            # Intercepta resposta para capturar o session_id que o FastMCP gerar
            async def capturing_send(message):
                if message.get('type') == 'http.response.body':
                    body = message.get('body', b'').decode('utf-8', errors='replace')
                    # FastMCP envia: "data: /messages/SESSION_ID"
                    m = re.search(r'data:\s*/messages/([^\n\r\s]+)', body)
                    if m:
                        session_id = m.group(1).strip()
                        _session_tokens[session_id] = token
                await send(message)

            await self.app(scope, receive, capturing_send)
            return

        # ── Chamada de ferramenta: /messages/SESSION_ID ──────────────────────
        if first == 'messages' and len(parts) > 1:
            session_id = parts[1].split('?')[0].strip()
            token = _session_tokens.get(session_id, '')
            if token:
                mcp_token_ctx.set(token)
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)


combined = Starlette(routes=[
    Mount('/mcp', app=TokenMiddleware(mcp_asgi)),
    Mount('/', app=WSGIMiddleware(flask_app)),
])

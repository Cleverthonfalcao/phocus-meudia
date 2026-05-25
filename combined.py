"""
Phocus Meu Dia — Servidor combinado
Flask (web app) + FastMCP (MCP server) em uma única porta via Starlette router.

Rotas:
  /mcp/{TOKEN}/sse       → MCP server com token embutido na URL
  /mcp/{TOKEN}/messages  → MCP messages endpoint
  /* (resto)             → Flask app (login, dashboard, api)
"""

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

# ASGI app do MCP (SSE transport)
mcp_asgi = mcp.http_app(transport='sse')


class TokenExtractMiddleware:
    """Extrai token do path /TOKEN/... e injeta no contextvar antes de chamar o MCP."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            path = scope.get('path', '')
            parts = path.lstrip('/').split('/', 1)
            if parts[0] and parts[0] != 'sse' and parts[0] != 'messages':
                # Primeiro segmento é o token
                token = parts[0]
                remaining = '/' + parts[1] if len(parts) > 1 else '/'
                mcp_token_ctx.set(token)
                scope = dict(scope)
                scope['path'] = remaining
                scope['raw_path'] = remaining.encode()
        await self.app(scope, receive, send)


mcp_with_token = TokenExtractMiddleware(mcp_asgi)

combined = Starlette(routes=[
    Mount('/mcp', app=mcp_with_token),
    Mount('/', app=WSGIMiddleware(flask_app)),
])

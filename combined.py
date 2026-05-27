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

_starlette = Starlette(routes=[
    Mount('/mcp',      app=mcp_asgi),
    Mount('/messages', app=mcp_asgi),
    Mount('/',         app=WSGIMiddleware(flask_app)),
])


class TokenMiddleware:
    """
    Garante que cada requisição carregue o token correto do usuário.

    Estratégia segura:
    1. SSE /mcp/TOKEN/sse  → token fica na URL, reescrevemos o endpoint
       que o FastMCP envia ao cliente para /mcp/TOKEN/messages/...
       Assim TODAS as tool calls subsequentes carregam o token na URL.
    2. Tool calls /mcp/TOKEN/messages/... → token extraído da URL (seguro).
    3. Fallback /messages/... sem token → bloqueado (retorna 401).
       Nunca usamos IP para inferir token — elimina vazamento entre usuários.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '')

        # ── 1. /mcp/TOKEN/sse — reescreve endpoint no corpo da resposta SSE ──────
        m = re.match(r'^/mcp/([^/]+)/sse', path)
        if m:
            token = m.group(1)
            mcp_token_ctx.set(token)
            # Persiste para o _get_token() dos tools
            try:
                save_ip_token(scope.get('client', ('', 0))[0], token)
            except Exception:
                pass
            print(f'[MW] SSE token={token!r}', flush=True)

            # Reescreve a URL de endpoint que FastMCP manda ao cliente via SSE:
            # "data: /messages/SESSION" → "data: /mcp/TOKEN/messages/SESSION"
            scope = {**scope, 'path': '/mcp/sse', 'raw_path': b'/mcp/sse'}

            token_bytes = token.encode()

            async def send_rewrite(message):
                if message.get('type') == 'http.response.body':
                    body = message.get('body', b'')
                    # Reescreve endpoint para incluir token
                    body = body.replace(
                        b'data: /messages/',
                        b'data: /mcp/' + token_bytes + b'/messages/'
                    )
                    body = body.replace(
                        b'data: /mcp/messages/',
                        b'data: /mcp/' + token_bytes + b'/messages/'
                    )
                    message = {**message, 'body': body}
                await send(message)

            await self.app(scope, receive, send_rewrite)
            return

        # ── 2. /mcp/TOKEN/messages/... — token extraído da URL (caminho seguro) ──
        m = re.match(r'^/mcp/([^/]+)/(messages.*)', path)
        if m:
            token    = m.group(1)
            new_path = '/mcp/' + m.group(2)
            mcp_token_ctx.set(token)
            print(f'[MW] tool token={token!r}', flush=True)
            scope = {**scope, 'path': new_path, 'raw_path': new_path.encode()}
            await self.app(scope, receive, send)
            return

        # ── 3. /messages/... sem token na URL — BLOQUEADO por segurança ──────────
        # Nunca inferimos token por IP: evita que um usuário veja dados de outro.
        if re.match(r'^(?:/mcp)?/messages/', path):
            print(f'[MW] BLOQUEADO path sem token: {path!r}', flush=True)
            async def send_401(send):
                await send({'type': 'http.response.start', 'status': 401,
                            'headers': [[b'content-type', b'text/plain']]})
                await send({'type': 'http.response.body', 'body': b'Token required', 'more_body': False})
            await send_401(send)
            return

        await self.app(scope, receive, send)


combined = TokenMiddleware(_starlette)

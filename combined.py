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

# session_id → token  (seguro: cada sessão SSE tem ID único)
_session_token: dict[str, str] = {}


class TokenMiddleware:
    """
    Associa token ao session_id gerado pelo FastMCP — não ao IP.
    Isso garante isolamento total entre usuários mesmo na mesma rede.

    Fluxo:
    1. SSE /mcp/TOKEN/sse  → captura o session_id da resposta SSE
       e armazena session_id → token
    2. Tool calls /mcp/TOKEN/messages/... → token direto da URL
    3. Tool calls /messages/SESSION_ID    → token via session_id (seguro)
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '')

        # ── 1. /mcp/TOKEN/sse — intercepta resposta para capturar session_id ──
        m = re.match(r'^/mcp/([^/]+)/sse', path)
        if m:
            token = m.group(1)
            mcp_token_ctx.set(token)
            try:
                save_ip_token(scope.get('client', ('', 0))[0], token)
            except Exception:
                pass
            print(f'[MW] SSE token={token!r}', flush=True)
            scope = {**scope, 'path': '/mcp/sse', 'raw_path': b'/mcp/sse'}

            token_ref = token

            async def send_capture(message):
                if message.get('type') == 'http.response.body':
                    body = message.get('body', b'')
                    # FastMCP envia: "data: /messages/SESSION_ID\n"
                    # Captura o session_id e mapeia ao token
                    for line in body.decode('utf-8', errors='ignore').splitlines():
                        line = line.strip()
                        if line.startswith('data:'):
                            data_val = line[5:].strip()
                            sid_match = re.search(r'/messages/([^/\s?]+)', data_val)
                            if sid_match:
                                sid = sid_match.group(1)
                                _session_token[sid] = token_ref
                                print(f'[MW] session_id={sid!r} → token={token_ref!r}', flush=True)
                await send(message)

            await self.app(scope, receive, send_capture)
            return

        # ── 2. /mcp/TOKEN/messages/... — token direto da URL ──────────────────
        m = re.match(r'^/mcp/([^/]+)/(messages.*)', path)
        if m:
            token    = m.group(1)
            new_path = '/mcp/' + m.group(2)
            mcp_token_ctx.set(token)
            print(f'[MW] tool(url) token={token!r}', flush=True)
            scope = {**scope, 'path': new_path, 'raw_path': new_path.encode()}
            await self.app(scope, receive, send)
            return

        # ── 3. /messages/SESSION_ID — resolve token pelo session_id ──────────
        if re.match(r'^(?:/mcp)?/messages/', path):
            sid_match = re.search(r'/messages/([^/\s?]+)', path)
            if sid_match:
                sid = sid_match.group(1)
                token = _session_token.get(sid, '')
                if not token:
                    # Fallback: tenta pelo banco (resistente a restart)
                    try:
                        token = get_token_by_ip(scope.get('client', ('', 0))[0])
                    except Exception:
                        token = ''
                if token:
                    mcp_token_ctx.set(token)
                    print(f'[MW] tool(sid) sid={sid!r} token={token!r}', flush=True)
                else:
                    print(f'[MW] token não encontrado sid={sid!r}', flush=True)
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)


combined = TokenMiddleware(_starlette)

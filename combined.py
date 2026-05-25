"""
Phocus Meu Dia — Servidor combinado
Flask (web app) + FastMCP (MCP server) em uma única porta.

URL do conector MCP: https://meudia.up.railway.app/mcp/SEU_TOKEN/sse
O middleware extrai o token da URL ANTES do roteamento.
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
_last_sse_body: dict[str, str] = {}  # token → último corpo SSE (debug)

_starlette = Starlette(routes=[
    Mount('/mcp',      app=mcp_asgi),
    Mount('/messages', app=mcp_asgi),
    Mount('/',         app=WSGIMiddleware(flask_app)),
])


class TokenMiddleware:
    """
    Roda ANTES do Starlette.
    /mcp/TOKEN/sse       → reescreve para /mcp/sse + intercepta resposta SSE
    /mcp/TOKEN/messages  → reescreve para /mcp/messages + injeta token
    /messages/SESSION_ID → injeta token via registro de sessão
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope_type = scope.get('type', '')

        # Log de todas as requisições HTTP para debug
        if scope_type == 'http':
            path = scope.get('path', '')
            method = scope.get('method', '')
            print(f'[MW] {method} {path!r}', flush=True)
        else:
            # Para lifespan e websocket, passa direto
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '')

        # ── 1. /mcp/TOKEN/sse → /mcp/sse + reescrita da URL de endpoint ──────────
        m = re.match(r'^/mcp/([^/]+)/sse', path)  # sem $ para tolerar query strings
        if m:
            token = m.group(1)
            print(f'[MW] SSE conectando token={token!r}', flush=True)
            scope = {**scope, 'path': '/mcp/sse', 'raw_path': b'/mcp/sse'}

            async def capture_send(message):
                msg_type = message.get('type', '')
                print(f'[CAPTURE] token={token!r} msg_type={msg_type!r}', flush=True)

                if msg_type == 'http.response.body':
                    body_raw = message.get('body', b'')
                    body = body_raw.decode('utf-8', errors='replace') if isinstance(body_raw, bytes) else str(body_raw)
                    _last_sse_body[token] = f'type={msg_type} | {body[:400]}'

                    if body:
                        print(f'[CAPTURE] body={body[:200]!r}', flush=True)

                        # Reescreve URL de endpoint SSE para incluir o token:
                        # FastMCP envia:  data: /messages/SESSION_ID
                        # Queremos:       data: /mcp/TOKEN/messages/SESSION_ID
                        def rewrite_endpoint(mo):
                            prefix = mo.group(1)   # "data: "
                            ep = mo.group(2)        # "/messages/..." ou "/mcp/messages/..."

                            # Normaliza para /messages/SESSION_ID
                            sid_m = re.search(r'/messages/([^\s\n\r?]+)', ep)
                            if sid_m:
                                sid = sid_m.group(1).strip()
                                _sessions[sid] = token
                                print(f'[SESSION] registrado {sid!r} → {token!r}', flush=True)
                                new_ep = f'/mcp/{token}/messages/{sid}'
                            else:
                                # Formato com querystring: /messages?sessionId=...
                                new_ep = f'/mcp/{token}' + ep if ep.startswith('/messages') else ep

                            print(f'[REWRITE] {ep!r} → {new_ep!r}', flush=True)
                            return prefix + new_ep

                        new_body = re.sub(
                            r'(data:\s*)(/(?:mcp/)?messages[^\n\r]*)',
                            rewrite_endpoint,
                            body
                        )

                        if new_body != body:
                            message = {**message, 'body': new_body.encode('utf-8')}

                await send(message)

            await self.app(scope, receive, capture_send)
            return

        # ── 2. /mcp/TOKEN/messages/... → /mcp/messages/... + injeta token ────────
        m = re.match(r'^/mcp/([^/]+)/(messages.*)', path)
        if m:
            token = m.group(1)
            new_path = '/mcp/' + m.group(2)
            print(f'[MW] messages via URL token={token!r} → {new_path!r}', flush=True)
            mcp_token_ctx.set(token)
            scope = {**scope, 'path': new_path, 'raw_path': new_path.encode()}
            await self.app(scope, receive, send)
            return

        # ── 3. /messages/SESSION_ID → injeta token via registro ──────────────────
        m = re.match(r'^(?:/mcp)?/messages/([^\s?/]+)', path)
        if m:
            session_id = m.group(1)
            token = _sessions.get(session_id, '')
            if token:
                mcp_token_ctx.set(token)
                print(f'[MW] session lookup {session_id!r} → {token!r}', flush=True)
            else:
                print(f'[MW] session NOT FOUND {session_id!r}, known={list(_sessions.keys())}', flush=True)
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)


combined = TokenMiddleware(_starlette)

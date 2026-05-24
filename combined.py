"""
Phocus Meu Dia — Servidor combinado
Flask (web app) + FastMCP (MCP server) em uma única porta via Starlette router.

Rotas:
  /sse        → MCP server (claude.ai conecta aqui)
  /mcp/*      → MCP server
  /* (resto)  → Flask app (login, dashboard, api)
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
from starlette.responses import RedirectResponse
from starlette.requests import Request

from app import app as flask_app
from mcp_server import mcp

# ASGI app do MCP (SSE transport)
mcp_asgi = mcp.http_app(transport='sse')

# Router combinado
combined = Starlette(routes=[
    Mount('/mcp', app=mcp_asgi),
    Mount('/', app=WSGIMiddleware(flask_app)),
])

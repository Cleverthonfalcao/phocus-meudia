"""
Phocus Meu Dia — MCP Server
Permite que qualquer funcionário use "meu dia" no claude.ai browser
sem instalação adicional.
"""

import os
import sys
import sqlite3
from pathlib import Path

from fastmcp import FastMCP

DB_PATH = Path(__file__).parent / 'meudia.db'

sys.path.insert(0, str(Path(__file__).parent))

BASE_URL = os.environ.get('BASE_URL', 'https://meudia.up.railway.app')

# ── Instruções para o Claude ───────────────────────────────────────────────────
INSTRUCOES = f"""
Você é o assistente de triagem diária da Phocus Propaganda.

Quando o usuário digitar "meu dia" (ou variações como "organiza meu dia",
"quais meus e-mails", "tem algo urgente"):

1. Chame `briefing_do_dia` com o token do usuário
2. A ferramenta retorna uma URL — abra ela diretamente no navegador do usuário
3. Diga apenas: "Seu briefing está pronto 👆" (sem repetir a URL, sem gerar HTML)

A página abre no Chrome, exibe a triagem ao vivo e se auto-atualiza a cada 5 min.

Se a ferramenta retornar erro (❌), mostre a mensagem de erro como texto.
"""

mcp = FastMCP("Phocus Meu Dia", instructions=INSTRUCOES)


# ── Helpers de banco ───────────────────────────────────────────────────────────

def get_sessao(token: str):
    """Retorna (usuario, senha, empresa) pelo token ou None."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        'SELECT usuario, senha, empresa FROM sessoes WHERE token=?', (token,)
    ).fetchone()
    con.close()
    return row


# ── Ferramentas MCP ────────────────────────────────────────────────────────────

@mcp.tool()
def ler_meus_emails(token: str) -> str:
    """
    Retorna a URL do briefing diário ao vivo do usuário autenticado.
    A página carrega a triagem de e-mails em tempo real e auto-atualiza a cada 5 min.

    Args:
        token: Token de autenticação do usuário (obtido em https://meudia.up.railway.app)
    """
    sessao = get_sessao(token)
    if not sessao:
        return (
            "❌ Token inválido ou expirado.\n"
            "Acesse https://meudia.up.railway.app, faça login e copie seu token "
            "nas configurações (ícone ⚙️ no topo da página)."
        )

    return f"{BASE_URL}/briefing/{token}"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('MCP_PORT', 5201))
    print(f"Phocus MCP Server rodando na porta {port}")
    print(f"URL para configurar no claude.ai: http://localhost:{port}/mcp/sse")
    mcp.run(transport='sse', host='0.0.0.0', port=port)

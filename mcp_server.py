"""
Phocus Meu Dia — MCP Server
Token embutido na URL do conector: /mcp/{TOKEN}/sse
"""

import os
import sys
import sqlite3
import urllib.request
import json
from contextvars import ContextVar
from datetime import date
from pathlib import Path

from fastmcp import FastMCP

DB_PATH  = Path(__file__).parent / 'meudia.db'
BASE_URL = os.environ.get('BASE_URL', 'https://meudia.up.railway.app')

sys.path.insert(0, str(Path(__file__).parent))
from app import ler_emails

# Contextvar preenchido pelo middleware com o token da URL
mcp_token_ctx: ContextVar[str] = ContextVar('mcp_token', default='')

INSTRUCOES = """
Você é o assistente de triagem diária da Phocus Propaganda.
Quando o usuário disser "meu dia" (ou pedir e-mails, triagem, bom dia):
- Chame imediatamente a ferramenta `meu_dia` sem nenhum parâmetro.
- Com os dados retornados, gere o briefing com sugestões de resposta.
- Ao final, chame `salvar_briefing` com o token e o briefing completo.
"""

mcp = FastMCP("Phocus Meu Dia", instructions=INSTRUCOES)


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_sessao(token: str):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        'SELECT usuario, senha, empresa FROM sessoes WHERE token=?', (token,)
    ).fetchone()
    con.close()
    return row


def _buscar_emails(token: str) -> str:
    sessao = get_sessao(token)
    if not sessao:
        return "❌ Token inválido. Reconfigure o conector MCP em meudia.up.railway.app"

    usuario, senha, empresa = sessao
    emails, erros, _ = ler_emails(usuario, senha, horas=18)

    if erros:
        return f"❌ Erro ao acessar e-mails: {erros}"

    nome = usuario.split('@')[0].replace('.', ' ').title().split()[0]

    if not emails:
        return f"✅ Caixa em dia — nenhum e-mail não lido nas últimas 18h. Bom dia, {nome}!"

    hoje = date.today().strftime('%d/%m/%Y')
    linhas = [f"TRIAGEM — {hoje} — {nome} ({empresa.upper()})", f"Total: {len(emails)} e-mail(s)\n"]

    ordem = ['urgente', 'importante', 'atencao', 'baixa']
    labels = {'urgente': '🔴 URGENTE', 'importante': '🟡 IMPORTANTE',
              'atencao': '🟢 ATENÇÃO', 'baixa': '⚪ BAIXA'}

    for prioridade in ordem:
        grupo = [e for e in emails if e['prioridade'] == prioridade]
        if not grupo:
            continue
        linhas.append(f"\n{labels[prioridade]} ({len(grupo)})")
        linhas.append("─" * 40)
        for e in grupo:
            linhas.append(f"De: {e['remetente']}")
            linhas.append(f"Assunto: {e['assunto']}")
            linhas.append(f"Data: {e['data']}")
            if e.get('corpo'):
                linhas.append(f"Prévia: {e['corpo'][:300]}{'...' if len(e['corpo']) > 300 else ''}")
            linhas.append("")

    linhas.append(f"\n[TOKEN:{token} | Usuário:{nome} | Empresa:{empresa.upper()} | Briefing:{BASE_URL}/briefing/{token}]")
    return '\n'.join(linhas)


# ── Ferramentas MCP ────────────────────────────────────────────────────────────

@mcp.tool()
def meu_dia() -> str:
    """
    Lê e classifica os e-mails não lidos das últimas 18h.
    Chame este tool quando o usuário pedir 'meu dia', 'triagem' ou quiser ver e-mails.
    Não requer parâmetros — o token vem da URL do conector MCP.
    """
    token = mcp_token_ctx.get()
    if not token:
        return (
            "❌ Token não encontrado.\n"
            "A URL do conector MCP deve ser: https://meudia.up.railway.app/mcp/SEU_TOKEN\n"
            "Acesse meudia.up.railway.app para obter sua URL personalizada."
        )
    return _buscar_emails(token)


@mcp.tool()
def salvar_briefing(conteudo: str) -> str:
    """
    Salva o briefing gerado na página web do usuário.
    Chame SEMPRE após gerar o briefing — aparece em meudia.up.railway.app/briefing/TOKEN.

    Args:
        conteudo: Texto completo do briefing com sugestões de resposta
    """
    token = mcp_token_ctx.get()
    if not token:
        return "❌ Token não disponível na conexão."
    if not get_sessao(token):
        return "❌ Token inválido."
    try:
        payload = json.dumps({'token': token, 'conteudo': conteudo}).encode()
        req = urllib.request.Request(
            f'{BASE_URL}/api/salvar-briefing',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if result.get('ok'):
            return f"✅ Briefing salvo — {BASE_URL}/briefing/{token}"
        return f"❌ Erro ao salvar: {result.get('erro')}"
    except Exception as e:
        return f"❌ Erro: {e}"


# ── Entry point (dev local) ────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('MCP_PORT', 5201))
    print(f"Phocus MCP Server rodando na porta {port}")
    mcp.run(transport='sse', host='0.0.0.0', port=port)

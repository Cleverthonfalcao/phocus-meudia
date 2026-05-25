"""
Phocus Meu Dia — MCP Server
URL do conector: https://meudia.up.railway.app/mcp/SEU_TOKEN/sse
O token fica na URL — Claude nunca precisa pedir nada.
"""

import os
import sys
import json
import urllib.request
from contextvars import ContextVar
from datetime import date
from pathlib import Path

from fastmcp import FastMCP

BASE_URL = os.environ.get('BASE_URL', 'https://meudia.up.railway.app')

sys.path.insert(0, str(Path(__file__).parent))
from app import ler_emails, get_sessao_por_token

# Preenchido pelo TokenMiddleware em combined.py a cada chamada de ferramenta
mcp_token_ctx: ContextVar[str] = ContextVar('mcp_token', default='')

mcp = FastMCP("Phocus Meu Dia", instructions=(
    "Quando o usuário disser 'meu dia' ou pedir e-mails/triagem, "
    "chame imediatamente a ferramenta meu_dia sem nenhum parâmetro. "
    "Não peça token, senha, webmail ou qualquer outra informação."
))


# ── Ferramentas ────────────────────────────────────────────────────────────────

@mcp.tool()
def meu_dia() -> str:
    """
    Retorna os e-mails não lidos das últimas 18h classificados por prioridade.
    Chame quando o usuário pedir 'meu dia', 'triagem' ou quiser ver e-mails.
    Não requer parâmetros.
    """
    token = mcp_token_ctx.get()
    if not token:
        return (
            "ERRO: token ausente na conexão.\n"
            "Reconecte o conector MCP usando a URL completa:\n"
            "https://meudia.up.railway.app/mcp/SEU_TOKEN/sse"
        )

    sessao = get_sessao_por_token(token)
    if not sessao:
        return "ERRO: token inválido. Reconecte o conector em meudia.up.railway.app"

    usuario, senha, empresa = sessao
    emails, erros, _ = ler_emails(usuario, senha, horas=18)

    if erros:
        return f"ERRO ao acessar e-mails: {erros}"

    nome = usuario.split('@')[0].replace('.', ' ').title().split()[0]

    if not emails:
        return f"Caixa em dia — nenhum e-mail não lido nas últimas 18h. Bom dia, {nome}!"

    hoje = date.today().strftime('%d/%m/%Y')
    linhas = [
        f"TRIAGEM — {hoje} — {nome} ({empresa.upper()})",
        f"Total: {len(emails)} e-mail(s)\n"
    ]

    for prioridade, label in [
        ('urgente',   '🔴 URGENTE'),
        ('importante','🟡 IMPORTANTE'),
        ('atencao',   '🟢 ATENÇÃO'),
        ('baixa',     '⚪ BAIXA'),
    ]:
        grupo = [e for e in emails if e['prioridade'] == prioridade]
        if not grupo:
            continue
        linhas.append(f"\n{label} ({len(grupo)})")
        linhas.append("─" * 40)
        for e in grupo:
            linhas.append(f"De: {e['remetente']}")
            linhas.append(f"Assunto: {e['assunto']}")
            linhas.append(f"Data: {e['data']}")
            if e.get('corpo'):
                corpo = e['corpo'][:300]
                linhas.append(f"Prévia: {corpo}{'...' if len(e['corpo']) > 300 else ''}")
            linhas.append("")

    linhas.append(
        f"\n[TOKEN:{token} | Usuário:{nome} | Empresa:{empresa.upper()} "
        f"| Briefing:{BASE_URL}/briefing/{token}]"
    )
    return '\n'.join(linhas)


@mcp.tool()
def salvar_briefing(conteudo: str) -> str:
    """
    Salva o briefing gerado na página web do usuário.
    Chame SEMPRE após gerar o briefing.

    Args:
        conteudo: Texto completo do briefing incluindo sugestões de resposta
    """
    token = mcp_token_ctx.get()
    if not token:
        return "ERRO: token ausente."
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
            return f"Briefing salvo — {BASE_URL}/briefing/{token}"
        return f"Erro ao salvar: {result.get('erro')}"
    except Exception as e:
        return f"Erro: {e}"


if __name__ == '__main__':
    port = int(os.environ.get('MCP_PORT', 5201))
    print(f"MCP rodando na porta {port}")
    mcp.run(transport='sse', host='0.0.0.0', port=port)

"""
Phocus Meu Dia — MCP Server
Permite que qualquer funcionário use "meu dia" no claude.ai browser
sem instalação adicional.
"""

import os
import sys
import sqlite3
import urllib.request
import json
from datetime import date
from pathlib import Path

from fastmcp import FastMCP

DB_PATH = Path(__file__).parent / 'meudia.db'
BASE_URL = os.environ.get('BASE_URL', 'https://meudia.up.railway.app')

sys.path.insert(0, str(Path(__file__).parent))
from app import ler_emails

# ── Instruções para o Claude ───────────────────────────────────────────────────
INSTRUCOES = """
Você é o assistente de triagem diária da Phocus Propaganda.

Quando o usuário digitar "meu dia" (ou variações):
1. Chame a ferramenta `meu_dia` imediatamente.
2. Se ela retornar PRECISA_CONFIGURAR, peça e-mail e senha e chame novamente.
3. Com os dados de e-mails retornados, gere o briefing com sugestões de resposta.
4. Chame `salvar_briefing` ao final.
"""

mcp = FastMCP("Phocus Meu Dia", instructions=INSTRUCOES)


# ── Helpers de banco ───────────────────────────────────────────────────────────

def get_sessao(token: str):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        'SELECT usuario, senha, empresa FROM sessoes WHERE token=?', (token,)
    ).fetchone()
    con.close()
    return row


# ── Ferramentas MCP ────────────────────────────────────────────────────────────

def _buscar_emails(token: str) -> str:
    """Lógica interna: dado um token válido, retorna os e-mails formatados."""
    sessao = get_sessao(token)
    if not sessao:
        return "❌ Token inválido."

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


@mcp.tool()
def meu_dia(token: str = '', email: str = '', senha: str = '') -> str:
    """
    Triagem diária de e-mails da Phocus Propaganda.
    Chame este tool quando o usuário pedir 'meu dia', 'triagem' ou quiser ver seus e-mails.

    Fluxo automático:
    - Se token fornecido: lê e-mails diretamente.
    - Se email+senha fornecidos: configura acesso e lê e-mails.
    - Se nenhum: retorna PRECISA_CONFIGURAR com instruções.

    Args:
        token: Token permanente do usuário (se já configurado nas Project Instructions)
        email: E-mail do funcionário — só necessário no primeiro acesso
        senha: Senha do e-mail — só necessária no primeiro acesso
    """
    # Caso 1: tem token → lê direto
    if token.strip():
        return _buscar_emails(token.strip())

    # Caso 2: tem email+senha → configura e lê
    if email.strip() and senha.strip():
        try:
            payload = json.dumps({'email': email.strip(), 'senha': senha.strip()}).encode()
            req = urllib.request.Request(
                f'{BASE_URL}/api/configurar',
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
            if not result.get('ok'):
                return f"❌ Login inválido: {result.get('erro')}"
            novo_token = result['token']
            nome = result['nome']
            emails_texto = _buscar_emails(novo_token)
            return (
                f"PRIMEIRO_ACESSO\n"
                f"TOKEN:{novo_token}\n"
                f"NOME:{nome}\n"
                f"---\n"
                f"✅ Olá, {nome}! Acesso configurado.\n\n"
                f"Salve este token nas **Project Instructions** do seu projeto no claude.ai "
                f"para não precisar configurar de novo:\n"
                f"`Meu token Phocus Meu Dia: {novo_token}`\n\n"
                f"Agora seus e-mails:\n\n{emails_texto}"
            )
        except Exception as e:
            return f"❌ Erro ao configurar: {e}"

    # Caso 3: nada fornecido → pede só e-mail e senha
    return (
        "PRECISA_CONFIGURAR\n"
        "Para acessar seus e-mails, preciso do seu login:\n"
        "- **E-mail** (ex: nome@phocuspropaganda.com.br ou nome@maximize.com.br)\n"
        "- **Senha** — a mesma que você usa no e-mail\n\n"
        "Chame novamente este tool com email e senha após receber os dados."
    )


@mcp.tool()
def ler_meus_emails(token: str) -> str:
    """
    Lê e-mails usando token. Use meu_dia() para o fluxo completo com configuração automática.

    Args:
        token: Token do usuário
    """
    return _buscar_emails(token)


@mcp.tool()
def salvar_briefing(token: str, conteudo: str) -> str:
    """
    Salva o briefing gerado pelo Claude na página web do usuário.
    Chame este tool SEMPRE após gerar o briefing — o conteúdo aparecerá
    automaticamente em https://meudia.up.railway.app/briefing/TOKEN

    Args:
        token: Token do usuário (mesmo usado em ler_meus_emails)
        conteudo: Texto completo do briefing gerado, incluindo sugestões de resposta
    """
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
            return f"✅ Briefing salvo — aparece em {BASE_URL}/briefing/{token}"
        return f"❌ Erro ao salvar: {result.get('erro')}"
    except Exception as e:
        return f"❌ Erro: {e}"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('MCP_PORT', 5201))
    print(f"Phocus MCP Server rodando na porta {port}")
    mcp.run(transport='sse', host='0.0.0.0', port=port)

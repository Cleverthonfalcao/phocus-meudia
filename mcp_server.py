"""
Phocus Meu Dia — MCP Server
URL do conector: https://meudia.up.railway.app/mcp/SEU_TOKEN/sse
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
from app import ler_emails, get_sessao_por_token, salvar_briefing_ia

mcp_token_ctx: ContextVar[str] = ContextVar('mcp_token', default='')

INSTRUCOES = """
Você é o assistente de triagem diária da Phocus Propaganda.

Quando o usuário digitar "meu dia" (ou variações):

1. Chame `meu_dia` — retorna os e-mails com seus Message-IDs.

2. Para cada e-mail URGENTE ou IMPORTANTE:
   - Chame `salvar_sugestao` com o Message-ID exato, a ação necessária e o rascunho de resposta.
   - Faça isso antes de responder ao usuário.

3. Chame `salvar_briefing` com um resumo textual do dia (plano de ação, observações gerais).

4. Apresente o resumo ao usuário e mostre o link da página.

Tom: direto, sem corporativês. Sugestões curtas, como o próprio usuário escreveria.
Não peça token, senha nem qualquer outra informação.
"""

mcp = FastMCP("Phocus Meu Dia", instructions=INSTRUCOES)


def _get_token() -> str:
    token = mcp_token_ctx.get()
    if not token:
        try:
            import time
            from combined import _token_by_ip
            validos = sorted(
                [(t, ts) for t, ts in _token_by_ip.values() if time.time() - ts < 7200],
                key=lambda x: x[1], reverse=True
            )
            if validos:
                token = validos[0][0]
        except Exception:
            pass
    return token


@mcp.tool()
def meu_dia() -> str:
    """
    Busca e-mails não lidos das últimas 18h classificados por prioridade.
    Retorna os dados brutos para o Claude gerar o briefing com sugestões de resposta.
    Após gerar o briefing, chame salvar_briefing com o texto completo.
    """
    token = _get_token()
    if not token:
        return "Erro: reconecte o conector em https://meudia.up.railway.app/meu-token"

    sessao = get_sessao_por_token(token)
    if not sessao:
        return "Erro: token inválido. Reconecte em meudia.up.railway.app/meu-token"

    usuario, senha, empresa = sessao
    emails, erros, _ = ler_emails(usuario, senha, horas=18)

    if erros:
        return f"Erro ao acessar e-mails: {erros}"

    nome = usuario.split('@')[0].replace('.', ' ').title().split()[0]
    pagina = f"{BASE_URL}/briefing/{token}"

    if not emails:
        salvar_briefing_ia(token, json.dumps({"emails": [], "plano": ""}, ensure_ascii=False))
        return (
            f"Caixa em dia — nenhum e-mail não lido nas últimas 18h. Bom dia, {nome}!\n\n"
            f"📋 {pagina}"
        )

    # Salva JSON base imediatamente — página já atualiza antes do Claude gerar sugestões
    base_json = json.dumps({
        "emails": [
            {
                "message_id": e.get('message_id', ''),
                "remetente":  e['remetente'],
                "assunto":    e['assunto'],
                "prioridade": e['prioridade'],
                "acao":       "",
                "sugestao":   "",
            }
            for e in emails
        ],
        "plano": ""
    }, ensure_ascii=False)
    salvar_briefing_ia(token, base_json)

    hoje = date.today().strftime('%d/%m/%Y')
    linhas = [f"TRIAGEM — {hoje} — {nome} ({empresa.upper()})", f"Total: {len(emails)} e-mail(s)\n"]

    for prioridade, label in [
        ('urgente',    '🔴 URGENTE'),
        ('importante', '🟡 IMPORTANTE'),
        ('atencao',    '🟢 ATENÇÃO'),
        ('baixa',      '⚪ BAIXA'),
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
            linhas.append(f"Message-ID: {e.get('message_id', '')}")
            if e.get('corpo'):
                linhas.append(f"Prévia: {e['corpo'][:400]}{'...' if len(e['corpo']) > 400 else ''}")
            linhas.append("")

    linhas.append(f"\n📋 {pagina}")
    linhas.append(f"[Usuário: {nome} | Empresa: {empresa.upper()}]")

    return '\n'.join(linhas)


@mcp.tool()
def salvar_sugestao(message_id: str, sugestao: str, acao: str = '') -> str:
    """
    Salva a sugestão de resposta para um e-mail específico.
    Chame uma vez para cada e-mail urgente ou importante ANTES de responder ao usuário.

    Args:
        message_id: Message-ID exato do e-mail (campo "Message-ID:" retornado por meu_dia)
        sugestao: Rascunho de resposta pronto para colar, tom humano, 2-4 linhas
        acao: O que precisa ser feito em 1 linha (ex: "→ Aprovar layout até 15h")
    """
    token = _get_token()
    if not token:
        return "Erro: token ausente."
    try:
        payload = json.dumps({
            'token': token,
            'message_id': message_id,
            'sugestao': sugestao,
            'acao': acao,
        }).encode()
        req = urllib.request.Request(
            f'{BASE_URL}/api/salvar-sugestao',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if result.get('ok'):
            return f"✅ Sugestão salva para {message_id[:40]}..."
        return f"Erro: {result.get('erro')}"
    except Exception as e:
        return f"Erro: {e}"


@mcp.tool()
def salvar_briefing(conteudo: str) -> str:
    """
    Salva o resumo/plano de ação do dia na página web.
    Chame após salvar todas as sugestões individuais com salvar_sugestao.

    Args:
        conteudo: Resumo textual do dia — plano de ação, observações gerais
    """
    token = _get_token()
    if not token:
        return "Erro: token ausente."
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
            return f"✅ Briefing com sugestões salvo — {BASE_URL}/briefing/{token}"
        return f"Erro: {result.get('erro')}"
    except Exception as e:
        return f"Erro: {e}"


if __name__ == '__main__':
    port = int(os.environ.get('MCP_PORT', 5201))
    mcp.run(transport='sse', host='0.0.0.0', port=port)

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

Quando o usuário digitar "meu dia" (ou variações como "triagem", "e-mails", "o que chegou"):

1. Chame `meu_dia` para buscar os e-mails.

2. Apresente o resumo ao usuário (urgentes, importantes, atenção, baixa, plano de ação).
   Para cada e-mail urgente ou importante inclua uma sugestão de resposta: direta, 2-3 linhas,
   tom humano, assina com o primeiro nome do usuário.

3. OBRIGATÓRIO: chame `salvar_briefing` com um JSON no formato abaixo.
   Isso faz as sugestões aparecerem dentro de cada card na página web — não pule essa etapa.

   {
     "emails": [
       {
         "message_id": "<id exato recebido>",
         "uid": "<uid exato recebido>",
         "webmail_url": "<webmail_url exata recebida>",
         "remetente": "Nome legível",
         "assunto": "Assunto do e-mail",
         "prioridade": "urgente|importante|atencao|baixa",
         "acao": "→ O que fazer em 1 linha",
         "sugestao": "Rascunho de resposta pronto para colar (só para urgente/importante)"
       }
     ],
     "plano": "1. Primeiro item\\n2. Segundo item"
   }

4. Mostre o link da página do dia ao usuário.

Tom: direto, sem corporativês. Não peça token, senha nem qualquer outra informação.
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
        return (
            f"Caixa em dia — nenhum e-mail não lido nas últimas 18h. Bom dia, {nome}!\n\n"
            f"📋 Página do dia: {pagina}"
        )

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
            linhas.append(f"UID: {e.get('uid', '')}")
            linhas.append(f"Webmail: {e.get('webmail_url', '')}")
            linhas.append(f"Message-ID: {e.get('message_id', '')}")
            if e.get('corpo'):
                linhas.append(f"Prévia: {e['corpo'][:400]}{'...' if len(e['corpo']) > 400 else ''}")
            linhas.append("")

    linhas.append(f"\n📋 Página do dia: {pagina}")
    linhas.append(f"[Usuário: {nome} | Empresa: {empresa.upper()}]")

    return '\n'.join(linhas)


@mcp.tool()
def salvar_briefing(conteudo: str) -> str:
    """
    Salva o briefing gerado com sugestões de resposta na página web do usuário.
    SEMPRE chame este tool após gerar o briefing — o conteúdo aparecerá em /briefing/TOKEN.

    Args:
        conteudo: Texto completo do briefing gerado, incluindo sugestões de resposta por e-mail
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

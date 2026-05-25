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
NÃO pergunte sobre webmail, Chrome, URL ou configuração. Use APENAS as ferramentas abaixo.

Quando o usuário digitar "meu dia" (ou variações como "bom dia", "triagem", "e-mails"):

── PRIMEIRO ACESSO (sem token) ──
Se não houver token nas instruções do projeto:
1. Pergunte APENAS: "Para configurar, preciso do seu e-mail e senha da Phocus/Maximize."
2. Chame `configurar_acesso` com o e-mail e senha informados.
3. O tool retorna o token. Responda EXATAMENTE assim (substituindo TOKEN pelo valor real):
   "✅ Configurado, [NOME]! Seu token é: `TOKEN`

   Para não precisar configurar de novo, cole isto nas **Project Instructions** deste projeto no claude.ai:
   `Meu token Phocus Meu Dia: TOKEN`

   Agora vou buscar seus e-mails..."
4. Chame imediatamente `ler_meus_emails` com o token recebido e gere o briefing.

── USO DIÁRIO (com token) ──
Se o token já estiver disponível:
1. Chame IMEDIATAMENTE `ler_meus_emails` com o token.
2. Gere o briefing completo:

☀️ **Triagem — [DATA]** · [N] e-mails

🔴 **URGENTES — agir agora**
> **[Remetente]** · [data] · [assunto]
> → [ação em 1 linha]
> 💬 *Sugestão:* "[resposta direta, 2-3 linhas, tom humano, assina com primeiro nome]"

🟡 **IMPORTANTES — resolver hoje** [mesmo formato]

🟢 **ATENÇÃO** · ⚪ **BAIXA** [lista compacta]

✅ **Plano de ação**
1. [mais urgente] ...

3. OBRIGATÓRIO após gerar: chame `salvar_briefing` com o token e o texto completo.
   Isso faz o briefing aparecer em https://meudia.up.railway.app/briefing/TOKEN automaticamente.

Tom: direto, sem corporativês, sugestões curtas como o próprio funcionário escreveria.
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

@mcp.tool()
def configurar_acesso(email: str, senha: str) -> str:
    """
    Primeiro acesso: valida e-mail e senha, cria sessão e retorna token permanente.
    Use este tool quando o usuário ainda não tiver token configurado.

    Args:
        email: E-mail completo do funcionário (ex: nome@phocuspropaganda.com.br)
        senha: Senha do e-mail (mesma usada no webmail)
    """
    try:
        payload = json.dumps({'email': email, 'senha': senha}).encode()
        req = urllib.request.Request(
            f'{BASE_URL}/api/configurar',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        if result.get('ok'):
            return json.dumps({
                'ok': True,
                'token': result['token'],
                'nome': result['nome'],
                'empresa': result['empresa']
            })
        return f"❌ {result.get('erro', 'Erro desconhecido')}"
    except Exception as e:
        return f"❌ Erro ao configurar: {e}"


@mcp.tool()
def ler_meus_emails(token: str) -> str:
    """
    Lê e classifica os e-mails não lidos das últimas 18h do usuário autenticado.
    Retorna os dados para o Claude gerar briefing com sugestões de resposta por IA.

    Args:
        token: Token do usuário (obtido em https://meudia.up.railway.app clicando em ⚙️)
    """
    sessao = get_sessao(token)
    if not sessao:
        return (
            "❌ Token inválido ou expirado.\n"
            "Acesse https://meudia.up.railway.app, faça login e copie seu token "
            "nas configurações (ícone ⚙️ no topo da página)."
        )

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

    linhas.append(f"\n[Usuário: {nome} | Empresa: {empresa.upper()} | Briefing: {BASE_URL}/briefing/{token}]")
    return '\n'.join(linhas)


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

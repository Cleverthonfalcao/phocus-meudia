"""
Phocus Meu Dia — MCP Server
Permite que qualquer funcionário use "meu dia" no claude.ai browser
sem instalação adicional.
"""

import os
import sys
import sqlite3
from datetime import date
from pathlib import Path

from fastmcp import FastMCP

# Caminho compartilhado com o Flask app
DB_PATH = Path(__file__).parent / 'meudia.db'

# Adiciona o diretório ao path para importar funções do app
sys.path.insert(0, str(Path(__file__).parent))
from app import ler_emails

# ── Instruções para o Claude ───────────────────────────────────────────────────
INSTRUCOES = """
Você é o assistente de triagem diária da Phocus Propaganda.

Quando o usuário digitar "meu dia" (ou variações como "organiza meu dia",
"quais meus e-mails", "tem algo urgente"):

1. Chame `ler_meus_emails` com o token do usuário
2. Analise os e-mails retornados
3. Gere um briefing completo seguindo EXATAMENTE esta estrutura:

---
☀️ **Triagem — [DATA DE HOJE]**
[N] e-mails não lidos nas últimas 18h

🔴 **[N] Urgentes** · 🟡 **[N] Importantes** · 🟢 **[N] Atenção** · ⚪ **[N] Baixa**

---

**🔴 URGENTES — agir agora**
Para cada e-mail urgente:
> **[Remetente]**
> Assunto: [assunto]
> Data: [data]
> → [o que precisa ser feito em 1 linha]
> 💬 *Sugestão de resposta:* "[resposta direta, 2-3 linhas, tom Phocus]"

**🟡 IMPORTANTES — resolver hoje**
[mesmo formato, sem sugestão de resposta obrigatória]

**🟢 ATENÇÃO — acompanhar**
[lista compacta: remetente · assunto]

**⚪ BAIXA PRIORIDADE**
[lista compacta, agrupa automáticos]

---

**✅ Plano de ação**
1. [ação mais urgente]
2. [próxima]
...

---

Tom obrigatório:
- Direto, sem "Prezado(a)", sem "Segue em anexo", sem "Att,"
- Sugestões de resposta: curtas, humanas, como o próprio funcionário escreveria
- Não usar linguagem corporativa
- Assinar com o primeiro nome do usuário
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
    Lê e classifica os e-mails não lidos das últimas 18h do usuário autenticado.
    Retorna os dados formatados para análise e geração do briefing diário.

    Args:
        token: Token de autenticação do usuário (gerado em https://meudia.phocus.com.br)
    """
    sessao = get_sessao(token)
    if not sessao:
        return (
            "❌ Token inválido ou expirado.\n"
            "Acesse https://meudia.phocus.com.br, faça login e copie seu token "
            "nas configurações do claude.ai."
        )

    usuario, senha, empresa = sessao

    emails, erros, _ = ler_emails(usuario, senha, horas=18)

    if erros:
        return f"❌ Erro ao acessar e-mails: {erros}"

    if not emails:
        return (
            f"✅ Nenhum e-mail não lido nas últimas 18h para {usuario}.\n"
            f"Caixa em dia — dia livre para focar nas pautas!"
        )

    # Formata os dados para o Claude processar
    hoje = date.today().strftime('%d/%m/%Y')
    linhas = [
        f"DADOS DE E-MAIL — {hoje} — {usuario}",
        f"Total: {len(emails)} e-mail(s) não lido(s)",
        ""
    ]

    ordem = ['urgente', 'importante', 'atencao', 'baixa']
    labels = {
        'urgente':    '🔴 URGENTE',
        'importante': '🟡 IMPORTANTE',
        'atencao':    '🟢 ATENÇÃO',
        'baixa':      '⚪ BAIXA PRIORIDADE',
    }

    for prioridade in ordem:
        grupo = [e for e in emails if e['prioridade'] == prioridade]
        if not grupo:
            continue
        linhas.append(f"\n{'─'*50}")
        linhas.append(f"{labels[prioridade]} ({len(grupo)})")
        linhas.append('─'*50)
        for e in grupo:
            linhas.append(f"De: {e['remetente']}")
            linhas.append(f"Assunto: {e['assunto']}")
            linhas.append(f"Data: {e['data']}")
            if e.get('corpo'):
                preview = e['corpo'][:400]
                linhas.append(f"Prévia: {preview}{'...' if len(e['corpo']) > 400 else ''}")
            linhas.append("")

    # Dados de contexto para o Claude gerar respostas corretas
    nome_usuario = usuario.split('@')[0].replace('.', ' ').title().split()[0]
    linhas.append(f"\n[CONTEXTO: usuário = {nome_usuario}, empresa = {empresa.upper()}]")

    return '\n'.join(linhas)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('MCP_PORT', 5201))
    print(f"Phocus MCP Server rodando na porta {port}")
    print(f"URL para configurar no claude.ai: http://localhost:{port}/mcp/sse")
    mcp.run(transport='sse', host='0.0.0.0', port=port)

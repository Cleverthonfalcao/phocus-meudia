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
2. A ferramenta já retorna o HTML COMPLETO do briefing — não modifique, não resuma, não gere texto extra
3. Apresente o resultado como artifact do tipo `text/html` diretamente

IMPORTANTE:
- NÃO gere HTML próprio — use exatamente o que a ferramenta retornar
- NÃO adicione texto antes ou depois do artifact
- NÃO use markdown, listas ou formatação — só o artifact HTML
- Se a ferramenta retornar uma mensagem de erro (começa com ❌), mostre como texto simples
- Se retornar "✅ Nenhum e-mail", mostre como texto simples

Exemplo de resposta correta quando há e-mails:
[artifact type="text/html"]
[conteúdo retornado pela ferramenta]
[/artifact]
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


# ── Gerador de HTML ────────────────────────────────────────────────────────────

def gerar_html_briefing(emails: list, usuario: str, empresa: str) -> str:
    """Gera o widget HTML completo do briefing diário."""
    hoje = date.today().strftime('%d de %B de %Y').replace(
        'January','janeiro').replace('February','fevereiro').replace(
        'March','março').replace('April','abril').replace(
        'May','maio').replace('June','junho').replace(
        'July','julho').replace('August','agosto').replace(
        'September','setembro').replace('October','outubro').replace(
        'November','novembro').replace('December','dezembro')

    nome = usuario.split('@')[0].replace('.', ' ').title().split()[0]

    urgentes   = [e for e in emails if e['prioridade'] == 'urgente']
    importantes = [e for e in emails if e['prioridade'] == 'importante']
    atencao    = [e for e in emails if e['prioridade'] == 'atencao']
    baixa      = [e for e in emails if e['prioridade'] == 'baixa']

    total = len(emails)

    def card(e, cls, tag_cls, tag_label, with_reply=False):
        corpo = e.get('corpo', '')[:300] or ''
        corpo_html = f'<div class="ecard-preview">{corpo}{"…" if len(e.get("corpo","")) > 300 else ""}</div>' if corpo else ''
        reply = ''
        if with_reply:
            reply = '<div class="ecard-reply">💬 <em>Sugestão de resposta gerada pelo Claude após análise do conteúdo</em></div>'
        return f'''
        <div class="ecard {cls}">
          <div class="ecard-header">
            <div class="ecard-subject">{e.get("assunto","(sem assunto)")}</div>
            <span class="tag {tag_cls}">{tag_label}</span>
          </div>
          <div class="ecard-from">{e.get("remetente","?")} &middot; {e.get("data","")}</div>
          {corpo_html}
          {reply}
        </div>'''

    # Seção urgentes
    sec_urgentes = ''
    if urgentes:
        cards = ''.join(card(e,'ecard-red','tag-red','🔴 Urgente', True) for e in urgentes)
        sec_urgentes = f'''
        <div class="section">
          <div class="section-title">🔴 Urgentes — agir agora</div>
          {cards}
        </div>
        <div class="divider"></div>'''

    # Seção importantes
    sec_importantes = ''
    if importantes:
        cards = ''.join(card(e,'ecard-yellow','tag-yellow','🟡 Importante', True) for e in importantes)
        sec_importantes = f'''
        <div class="section">
          <div class="section-title">🟡 Importantes — resolver hoje</div>
          {cards}
        </div>
        <div class="divider"></div>'''

    # Seção atenção
    sec_atencao = ''
    if atencao:
        cards = ''.join(card(e,'ecard-green','tag-green','🟢 Atenção') for e in atencao)
        sec_atencao = f'''
        <div class="section">
          <div class="section-title">🟢 Atenção — acompanhar</div>
          {cards}
        </div>
        <div class="divider"></div>'''

    # Seção baixa
    sec_baixa = ''
    if baixa:
        itens = ' &middot; '.join(
            f'<span>{e.get("assunto","?")}</span>' for e in baixa
        )
        sec_baixa = f'''
        <div class="section">
          <div class="section-title">⚪ Baixa prioridade</div>
          <div class="info-group">{itens}</div>
        </div>
        <div class="divider"></div>'''

    # Plano de ação
    acoes = []
    for e in urgentes[:3]:
        acoes.append(f'Responder <strong>{e.get("remetente","?").split("<")[0].strip()}</strong> — {e.get("assunto","?")}')
    for e in importantes[:2]:
        acoes.append(f'Tratar: {e.get("assunto","?")}')
    if not acoes:
        acoes = ['Caixa em dia — foque nas pautas do dia']

    plano_items = ''.join(
        f'<div class="plano-item"><div class="plano-num">{i+1}</div><div>{a}</div></div>'
        for i, a in enumerate(acoes[:6])
    )

    # Badge zeros ficam acinzentados
    def badge_n(n, cls, label):
        style = 'opacity:0.35' if n == 0 else ''
        return f'<span class="badge {cls}" style="{style}">{label} {n}</span>'

    html = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--color-background-primary,#F9F9F9);color:var(--color-text-primary,#191818)}}
.header{{padding:1.25rem 1.25rem 1rem;border-bottom:.5px solid var(--color-border-tertiary,#E5E5E5)}}
.header-top{{display:flex;align-items:center;gap:10px;margin-bottom:4px}}
.header-top h2{{font-size:17px;font-weight:500}}
.header-meta{{font-size:13px;color:var(--color-text-secondary,#666)}}
.badges{{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}}
.badge{{font-size:12px;font-weight:500;padding:3px 10px;border-radius:20px}}
.badge-red{{background:#FCEBEB;color:#A32D2D}}
.badge-yellow{{background:#FAEEDA;color:#854F0B}}
.badge-green{{background:#EAF3DE;color:#3B6D11}}
.badge-grey{{background:#F1EFE8;color:#5F5E5A}}
.section{{padding:1rem 1.25rem}}
.section-title{{font-size:12px;font-weight:500;letter-spacing:.04em;text-transform:uppercase;color:var(--color-text-secondary,#666);margin-bottom:10px}}
.ecard{{border-radius:8px;border:.5px solid var(--color-border-tertiary,#E5E5E5);border-left-width:3px;padding:.85rem 1rem;margin-bottom:8px;background:var(--color-background-primary,#fff)}}
.ecard-red{{border-left-color:#E24B4A}}
.ecard-yellow{{border-left-color:#EF9F27}}
.ecard-green{{border-left-color:#639922}}
.ecard-header{{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:4px}}
.ecard-subject{{font-size:14px;font-weight:500;line-height:1.3}}
.ecard-from{{font-size:12px;color:var(--color-text-secondary,#666);margin-bottom:6px}}
.ecard-preview{{font-size:12px;color:var(--color-text-secondary,#888);line-height:1.5;margin-top:4px}}
.ecard-reply{{font-size:12px;color:var(--color-text-secondary,#666);background:var(--color-background-secondary,#F5F5F5);border-radius:6px;padding:6px 10px;margin-top:7px;line-height:1.5;border-left:2px solid #B0A2F9}}
.tag{{font-size:11px;font-weight:500;padding:2px 8px;border-radius:10px;white-space:nowrap;flex-shrink:0}}
.tag-red{{background:#FCEBEB;color:#A32D2D}}
.tag-yellow{{background:#FAEEDA;color:#854F0B}}
.tag-green{{background:#EAF3DE;color:#3B6D11}}
.info-group{{font-size:12px;color:var(--color-text-secondary,#666);padding:6px 10px;background:var(--color-background-secondary,#F5F5F5);border-radius:6px;margin-bottom:8px;line-height:1.8}}
.divider{{height:.5px;background:var(--color-border-tertiary,#E5E5E5);margin:0 1.25rem}}
.plano{{padding:1rem 1.25rem}}
.plano-title{{font-size:12px;font-weight:500;letter-spacing:.04em;text-transform:uppercase;color:var(--color-text-secondary,#666);margin-bottom:10px}}
.plano-item{{display:flex;align-items:flex-start;gap:10px;margin-bottom:8px;font-size:13px;line-height:1.5}}
.plano-num{{width:22px;height:22px;border-radius:50%;background:var(--color-background-secondary,#F5F5F5);border:.5px solid var(--color-border-secondary,#DDD);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:500;flex-shrink:0;color:var(--color-text-secondary,#666)}}
.aviso-ia{{font-size:11px;color:#B0A2F9;padding:.6rem 1.25rem;border-top:.5px solid var(--color-border-tertiary,#E5E5E5);margin-top:4px}}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <span style="font-size:20px">☀️</span>
    <h2>Triagem — {hoje}</h2>
  </div>
  <div class="header-meta">{total} e-mail{"s" if total != 1 else ""} não lido{"s" if total != 1 else ""} nas últimas 18h · {nome}</div>
  <div class="badges">
    {badge_n(len(urgentes),   "badge-red",    "🔴")}
    {badge_n(len(importantes),"badge-yellow", "🟡")}
    {badge_n(len(atencao),    "badge-green",  "🟢")}
    {badge_n(len(baixa),      "badge-grey",   "⚪")}
  </div>
</div>

{sec_urgentes}
{sec_importantes}
{sec_atencao}
{sec_baixa}

<div class="plano">
  <div class="plano-title">✅ Plano de ação</div>
  {plano_items}
</div>

<div class="aviso-ia">✦ Sugestões de resposta detalhadas disponíveis — peça ao Claude: "sugere resposta para [remetente]"</div>

</body>
</html>'''

    return html


# ── Ferramentas MCP ────────────────────────────────────────────────────────────

@mcp.tool()
def ler_meus_emails(token: str) -> str:
    """
    Lê e classifica os e-mails não lidos das últimas 18h do usuário autenticado.
    Retorna widget HTML completo pronto para ser apresentado como artifact.

    Args:
        token: Token de autenticação do usuário (gerado em https://meudia.up.railway.app)
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

    if not emails:
        nome = usuario.split('@')[0].replace('.', ' ').title().split()[0]
        return (
            f"✅ Nenhum e-mail não lido nas últimas 18h para {nome}.\n"
            f"Caixa em dia — dia livre para focar nas pautas!"
        )

    return gerar_html_briefing(emails, usuario, empresa)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('MCP_PORT', 5201))
    print(f"Phocus MCP Server rodando na porta {port}")
    print(f"URL para configurar no claude.ai: http://localhost:{port}/mcp/sse")
    mcp.run(transport='sse', host='0.0.0.0', port=port)

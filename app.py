"""
Phocus Meu Dia — Triagem diária de e-mails
Standalone Flask app · Locaweb (Phocus) + GoDaddy (Maximize)
Banco: PostgreSQL (produção Railway) | SQLite (desenvolvimento local)
"""

import imaplib
import email
import json
import os
import re
import sqlite3
from email.header import decode_header
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, request, session, redirect, jsonify
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'phocus-meudia-2026-secret')
app.config['TEMPLATES_AUTO_RELOAD'] = True

@app.after_request
def no_cache(r):
    r.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    r.headers['Pragma'] = 'no-cache'
    return r


# ── Camada de banco de dados (Postgres ou SQLite) ──────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL')  # Railway define automaticamente
DB_PATH      = Path(__file__).parent / 'meudia.db'

def _conn():
    """Retorna conexão ativa para o banco configurado."""
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(str(DB_PATH))

def _ph(n=1):
    """Placeholder SQL: %s (Postgres) ou ? (SQLite)."""
    p = '%s' if DATABASE_URL else '?'
    return ', '.join([p] * n) if n > 1 else p

def db_execute(sql, params=(), fetchall=False, fetchone=False):
    con = _conn()
    cur = con.cursor()
    cur.execute(sql, params)
    result = None
    if fetchall:
        result = cur.fetchall()
    elif fetchone:
        result = cur.fetchone()
    con.commit()
    con.close()
    return result


def init_db():
    if DATABASE_URL:
        db_execute('''CREATE TABLE IF NOT EXISTS resolvidos (
            id           SERIAL PRIMARY KEY,
            usuario      TEXT NOT NULL,
            message_id   TEXT NOT NULL,
            resolvido_em TEXT NOT NULL,
            UNIQUE(usuario, message_id)
        )''')
        db_execute('''CREATE TABLE IF NOT EXISTS sessoes (
            id        SERIAL PRIMARY KEY,
            usuario   TEXT NOT NULL UNIQUE,
            senha     TEXT NOT NULL,
            empresa   TEXT NOT NULL,
            token     TEXT NOT NULL UNIQUE,
            criado_em TEXT NOT NULL
        )''')
    else:
        db_execute('''CREATE TABLE IF NOT EXISTS resolvidos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario      TEXT NOT NULL,
            message_id   TEXT NOT NULL,
            resolvido_em TEXT NOT NULL,
            UNIQUE(usuario, message_id)
        )''')
        db_execute('''CREATE TABLE IF NOT EXISTS sessoes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario   TEXT NOT NULL UNIQUE,
            senha     TEXT NOT NULL,
            empresa   TEXT NOT NULL,
            token     TEXT NOT NULL UNIQUE,
            criado_em TEXT NOT NULL
        )''')

init_db()


def gerar_token():
    import secrets
    return secrets.token_urlsafe(32)

def salvar_sessao(email_addr, senha, empresa):
    row = db_execute(
        f'SELECT token FROM sessoes WHERE usuario={_ph()}',
        (email_addr,), fetchone=True
    )
    if row:
        token = row[0]
        db_execute(
            f'UPDATE sessoes SET senha={_ph()}, empresa={_ph()} WHERE usuario={_ph()}',
            (senha, empresa, email_addr)
        )
    else:
        token = gerar_token()
        db_execute(
            f'INSERT INTO sessoes (usuario, senha, empresa, token, criado_em) VALUES ({_ph(5)})',
            (email_addr, senha, empresa, token, datetime.now().isoformat())
        )
    return token

def get_sessao_por_token(token):
    return db_execute(
        f'SELECT usuario, senha, empresa FROM sessoes WHERE token={_ph()}',
        (token,), fetchone=True
    )

def get_resolvidos(usuario):
    rows = db_execute(
        f'SELECT message_id FROM resolvidos WHERE usuario={_ph()}',
        (usuario,), fetchall=True
    )
    return {r[0] for r in rows} if rows else set()

def resolver_email(usuario, message_id):
    if DATABASE_URL:
        db_execute(
            f'INSERT INTO resolvidos (usuario, message_id, resolvido_em) VALUES ({_ph(3)}) ON CONFLICT DO NOTHING',
            (usuario, message_id, datetime.now().isoformat())
        )
    else:
        db_execute(
            f'INSERT OR IGNORE INTO resolvidos (usuario, message_id, resolvido_em) VALUES ({_ph(3)})',
            (usuario, message_id, datetime.now().isoformat())
        )

def desmarcar_email(usuario, message_id):
    db_execute(
        f'DELETE FROM resolvidos WHERE usuario={_ph()} AND message_id={_ph()}',
        (usuario, message_id)
    )


# ── Configuração IMAP por domínio ──────────────────────────────────────────────

IMAP_CONFIG = {
    'phocuspropaganda.com.br': {
        'host': 'mail.phocuspropaganda.com.br',
        'port': 993,
        'empresa': 'phocus',
    },
    'maximize': {
        'host': 'imap.secureserver.net',
        'port': 993,
        'empresa': 'maximize',
    },
}

def get_imap_config(email_addr):
    domain = email_addr.split('@')[-1].lower()
    if domain in IMAP_CONFIG:
        return IMAP_CONFIG[domain]
    return IMAP_CONFIG['maximize']


# ── Classificação de e-mails ───────────────────────────────────────────────────

URGENTE_KEYWORDS = [
    'urgente', 'urgent', 'asap', 'hoje', 'vence hoje', 'prazo hoje',
    'aprovação urgente', 'responder hoje', 'para aprovação urgente',
]
BAIXA_SENDERS = [
    'iclips-mail.com.br', 'noreply@', 'no-reply@', 'transfernow.net',
    'google.com', 'anthropic.com', 'drive-shares', 'notification',
    'donotreply', 'automatico', 'suporte@',
]
IMPORTANTE_KEYWORDS = [
    'para aprovação', 'para avaliação', 're:', 'res:', 'resposta',
    'solicitação', 'pedido', 'orçamento', 'proposta', 'retorno',
    'follow', 'aguardando', 'prazo',
]

def classificar(assunto, remetente, corpo=''):
    s = assunto.lower()
    r = remetente.lower()
    c = (corpo or '').lower()

    for b in BAIXA_SENDERS:
        if b in r:
            return 'baixa'

    for k in URGENTE_KEYWORDS:
        if k in s or k in c:
            return 'urgente'

    for k in IMPORTANTE_KEYWORDS:
        if k in s:
            return 'importante'

    if '@' in remetente and 'noreply' not in r and 'no-reply' not in r:
        return 'atencao'

    return 'baixa'


# ── Leitura IMAP ───────────────────────────────────────────────────────────────

def ler_emails(email_addr, senha, horas=18):
    cfg = get_imap_config(email_addr)
    emails = []
    erros = None

    try:
        mail = imaplib.IMAP4_SSL(cfg['host'], cfg['port'])
        mail.login(email_addr, senha)
        mail.select('INBOX')

        desde = (datetime.now() - timedelta(hours=horas)).strftime('%d-%b-%Y')
        _, msgs = mail.search(None, f'UNSEEN SINCE "{desde}"')
        ids = msgs[0].split()

        for eid in ids[-40:]:
            _, dados = mail.fetch(eid, '(BODY.PEEK[])')
            msg = email.message_from_bytes(dados[0][1])

            raw, enc = decode_header(msg['Subject'] or '')[0]
            assunto = raw.decode(enc or 'utf-8', errors='replace') if isinstance(raw, bytes) else (raw or '')

            remetente = msg.get('From', '')

            corpo = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        try:
                            corpo = part.get_payload(decode=True).decode('utf-8', 'replace')[:400]
                        except Exception:
                            pass
                        break
            else:
                try:
                    corpo = msg.get_payload(decode=True).decode('utf-8', 'replace')[:400]
                except Exception:
                    pass

            data_str = msg.get('Date', '')
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(data_str)
                data_fmt = dt.strftime('%d/%m %H:%M')
            except Exception:
                data_fmt = data_str[:16]

            prioridade = classificar(assunto, remetente, corpo)
            message_id = msg.get('Message-ID', '').strip()

            emails.append({
                'assunto':    assunto,
                'remetente':  remetente,
                'data':       data_fmt,
                'corpo':      corpo.strip(),
                'prioridade': prioridade,
                'message_id': message_id,
            })

        mail.logout()

    except imaplib.IMAP4.error as e:
        erros = f'Credenciais inválidas ou servidor inacessível: {e}'
    except Exception as e:
        erros = f'Erro ao conectar: {e}'

    ordem = {'urgente': 0, 'importante': 1, 'atencao': 2, 'baixa': 3}
    emails.sort(key=lambda x: ordem.get(x['prioridade'], 4))

    return emails, erros, cfg['empresa']


# ── Autenticação ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'email' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'email' in session:
        return redirect('/meu-dia')
    return redirect('/login')


@app.route('/login', methods=['GET', 'POST'])
def login():
    erro = None
    if request.method == 'POST':
        email_addr = request.form.get('email', '').strip().lower()
        senha      = request.form.get('senha', '')
        if not email_addr or not senha:
            erro = 'Preencha e-mail e senha.'
        else:
            _, erros, empresa = ler_emails(email_addr, senha, horas=1)
            if erros:
                erro = erros
            else:
                token = salvar_sessao(email_addr, senha, empresa)
                session['email']   = email_addr
                session['senha']   = senha
                session['empresa'] = empresa
                session['token']   = token
                nome = email_addr.split('@')[0].replace('.', ' ').title().split()[0]
                session['nome']    = nome
                return redirect('/meu-dia')

    return render_template('login.html', erro=erro)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/meu-dia')
@login_required
def meu_dia():
    emails, erros, empresa = ler_emails(
        session['email'], session['senha'], horas=18
    )
    contadores = {
        'urgente':    sum(1 for e in emails if e['prioridade'] == 'urgente'),
        'importante': sum(1 for e in emails if e['prioridade'] == 'importante'),
        'atencao':    sum(1 for e in emails if e['prioridade'] == 'atencao'),
        'baixa':      sum(1 for e in emails if e['prioridade'] == 'baixa'),
    }
    hoje = datetime.now().strftime('%A, %d de %B de %Y').capitalize()
    dias  = {'monday':'Segunda','tuesday':'Terça','wednesday':'Quarta',
             'thursday':'Quinta','friday':'Sexta','saturday':'Sábado','sunday':'Domingo'}
    meses = {'january':'janeiro','february':'fevereiro','march':'março',
             'april':'abril','may':'maio','june':'junho','july':'julho',
             'august':'agosto','september':'setembro','october':'outubro',
             'november':'novembro','december':'dezembro'}
    for en, pt in {**dias, **meses}.items():
        hoje = hoje.replace(en.capitalize(), pt.capitalize()).replace(en, pt)

    if not session.get('token'):
        token = salvar_sessao(session['email'], session['senha'], empresa)
        session['token'] = token
        session.modified = True

    resolvidos = get_resolvidos(session['email'])

    return render_template('meu_dia.html',
        emails=emails,
        contadores=contadores,
        hoje=hoje,
        nome=session.get('nome',''),
        empresa=empresa,
        erros=erros,
        resolvidos=resolvidos,
        token=session.get('token',''),
    )


@app.route('/api/resolver', methods=['POST'])
@login_required
def api_resolver():
    data = request.get_json() or {}
    message_id = data.get('message_id', '').strip()
    acao = data.get('acao', 'resolver')

    if not message_id:
        return jsonify({'ok': False, 'erro': 'message_id ausente'})

    if acao == 'resolver':
        resolver_email(session['email'], message_id)
    else:
        desmarcar_email(session['email'], message_id)

    return jsonify({'ok': True})


@app.route('/api/atualizar')
@login_required
def atualizar():
    emails, erros, _ = ler_emails(session['email'], session['senha'], horas=18)
    contadores = {
        'urgente':    sum(1 for e in emails if e['prioridade'] == 'urgente'),
        'importante': sum(1 for e in emails if e['prioridade'] == 'importante'),
        'atencao':    sum(1 for e in emails if e['prioridade'] == 'atencao'),
        'baixa':      sum(1 for e in emails if e['prioridade'] == 'baixa'),
    }
    return jsonify({'emails': emails, 'contadores': contadores, 'erros': erros})


# ── Briefing ao vivo (para MCP / Claude) ──────────────────────────────────────

TEMA = {
    'phocus': {
        'bg_header':  '#191818',
        'cor_acento': '#B0A2F9',
        'logo_src':   '/static/logo-phocus-branca.png',
        'logo_alt':   'Phocus Propaganda',
    },
    'maximize': {
        'bg_header':  '#1A1A2E',
        'cor_acento': '#4A6FE3',
        'logo_src':   '/static/logo-maxi-branca.png',
        'logo_alt':   'Maximize',
    },
}

def gerar_html_briefing(emails: list, usuario: str, empresa: str) -> str:
    """Página HTML completa com triagem ao vivo — retornada diretamente pelo Flask."""
    meses = {1:'janeiro',2:'fevereiro',3:'março',4:'abril',5:'maio',6:'junho',
             7:'julho',8:'agosto',9:'setembro',10:'outubro',11:'novembro',12:'dezembro'}
    agora = datetime.now()
    hoje  = f"{agora.day} de {meses[agora.month]} de {agora.year}"
    hora  = agora.strftime('%H:%M')
    nome  = usuario.split('@')[0].replace('.', ' ').title().split()[0]

    tema = TEMA.get(empresa, TEMA['phocus'])

    urgentes    = [e for e in emails if e['prioridade'] == 'urgente']
    importantes = [e for e in emails if e['prioridade'] == 'importante']
    atencao     = [e for e in emails if e['prioridade'] == 'atencao']
    baixa       = [e for e in emails if e['prioridade'] == 'baixa']
    total       = len(emails)

    def _esc(s):
        return (s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

    def card(e, cls, tag_cls, tag_label):
        corpo = _esc(e.get('corpo','')[:280])
        corpo_html = f'<div class="preview">{corpo}{"…" if len(e.get("corpo",""))>280 else ""}</div>' if corpo else ''
        return f'''<div class="ecard {cls}">
          <div class="ecard-head">
            <div class="subject">{_esc(e.get("assunto","(sem assunto)"))}</div>
            <span class="tag {tag_cls}">{tag_label}</span>
          </div>
          <div class="from">{_esc(e.get("remetente","?"))} · {_esc(e.get("data",""))}</div>
          {corpo_html}
        </div>'''

    def section(items, cls, tag_cls, tag, title):
        if not items: return ''
        cards = ''.join(card(e, cls, tag_cls, tag) for e in items)
        return f'<div class="section"><div class="sec-title">{title}</div>{cards}</div><hr class="div">'

    def badge(n, cls, icon):
        op = 'opacity:.3' if n == 0 else ''
        return f'<span class="badge {cls}" style="{op}">{icon} {n}</span>'

    acoes = [f'Responder <b>{_esc(e.get("remetente","?").split("<")[0].strip())}</b> — {_esc(e.get("assunto","?"))}' for e in urgentes[:3]]
    acoes += [f'Tratar: {_esc(e.get("assunto","?"))}' for e in importantes[:2]]
    if not acoes:
        acoes = ['Caixa em dia — foque nas pautas 🎯']
    plano = ''.join(f'<div class="step"><div class="num">{i+1}</div><div>{a}</div></div>' for i,a in enumerate(acoes[:6]))

    secs = (
        section(urgentes,    'ecard-red',    'tag-red',    '🔴 Urgente',    '🔴 Urgentes — agir agora') +
        section(importantes, 'ecard-yellow', 'tag-yellow', '🟡 Importante', '🟡 Importantes — resolver hoje') +
        section(atencao,     'ecard-green',  'tag-green',  '🟢 Atenção',    '🟢 Atenção — acompanhar') +
        section(baixa,       'ecard-grey',   'tag-grey',   '⚪ Baixa',       '⚪ Baixa prioridade')
    )

    empty = '' if emails else '''<div class="empty">
      <div style="font-size:2.5rem;margin-bottom:.5rem">✅</div>
      <div style="font-weight:600;font-size:1.05rem;margin-bottom:.25rem">Caixa em dia</div>
      <div style="font-size:.875rem;color:#888">Nenhum e-mail não lido nas últimas 18h</div>
    </div>'''

    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>☀️ Triagem · {nome}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#F2F2F2;color:#191818;min-height:100vh}}
.wrap{{max-width:680px;margin:0 auto;padding:1.5rem 1rem 3rem}}
.header{{border-radius:12px;padding:1.25rem 1.5rem;margin-bottom:1rem;color:#fff}}
.logo{{font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;opacity:.5;margin-bottom:.5rem}}
.header h1{{font-size:1.1rem;font-weight:600;margin-bottom:.25rem}}
.header-sub{{font-size:.8rem;opacity:.6}}
.badges{{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.75rem}}
.badge{{font-size:.75rem;font-weight:500;padding:3px 10px;border-radius:20px}}
.badge-red{{background:#FCEBEB;color:#A32D2D}}
.badge-yellow{{background:#FAEEDA;color:#854F0B}}
.badge-green{{background:#EAF3DE;color:#3B6D11}}
.badge-grey{{background:#F1EFE8;color:#5F5E5A}}
.refresh-bar{{display:flex;align-items:center;justify-content:space-between;font-size:.75rem;color:#888;margin-bottom:1rem;padding:.5rem .75rem;background:#fff;border-radius:8px;border:.5px solid #E5E5E5}}
.refresh-bar button{{font-size:.75rem;background:none;border:.5px solid #DDD;border-radius:6px;padding:3px 10px;cursor:pointer;color:#555}}
.refresh-bar button:hover{{background:#F5F5F5}}
.section{{background:#fff;border-radius:12px;padding:1rem 1.25rem;margin-bottom:.75rem}}
.sec-title{{font-size:.7rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#888;margin-bottom:.75rem}}
.ecard{{border-radius:8px;border:.5px solid #E8E8E8;border-left-width:3px;padding:.85rem 1rem;margin-bottom:.5rem;background:#FAFAFA}}
.ecard:last-child{{margin-bottom:0}}
.ecard-red{{border-left-color:#E24B4A}}
.ecard-yellow{{border-left-color:#EF9F27}}
.ecard-green{{border-left-color:#639922}}
.ecard-grey{{border-left-color:#CCC}}
.ecard-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:.3rem}}
.subject{{font-size:.875rem;font-weight:500;line-height:1.35}}
.tag{{font-size:.65rem;font-weight:600;padding:2px 8px;border-radius:10px;white-space:nowrap;flex-shrink:0}}
.tag-red{{background:#FCEBEB;color:#A32D2D}}
.tag-yellow{{background:#FAEEDA;color:#854F0B}}
.tag-green{{background:#EAF3DE;color:#3B6D11}}
.tag-grey{{background:#F1EFE8;color:#5F5E5A}}
.from{{font-size:.75rem;color:#888;margin-bottom:.35rem}}
.preview{{font-size:.75rem;color:#AAA;line-height:1.5;margin-top:.25rem}}
hr.div{{border:none;border-top:.5px solid #E8E8E8;margin:.75rem 0}}
.plano{{background:#fff;border-radius:12px;padding:1rem 1.25rem;margin-bottom:.75rem}}
.plano-title{{font-size:.7rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#888;margin-bottom:.75rem}}
.step{{display:flex;align-items:flex-start;gap:.6rem;margin-bottom:.5rem;font-size:.825rem;line-height:1.5}}
.num{{width:20px;height:20px;border-radius:50%;background:#F2F2F2;border:.5px solid #DDD;display:flex;align-items:center;justify-content:center;font-size:.65rem;font-weight:600;flex-shrink:0;color:#888}}
.empty{{text-align:center;padding:3rem 1rem;background:#fff;border-radius:12px}}
.footer{{text-align:center;font-size:.7rem;color:#BBB;margin-top:1.5rem}}
.footer a{{color:{tema['cor_acento']};text-decoration:none}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header" style="background:{tema['bg_header']}">
    <img src="{tema['logo_src']}" alt="{tema['logo_alt']}" style="height:28px;margin-bottom:.75rem;display:block">
    <h1>☀️ Triagem — {hoje}</h1>
    <div class="header-sub">{total} e-mail{"s" if total!=1 else ""} não lido{"s" if total!=1 else ""} nas últimas 18h · {nome}</div>
    <div class="badges">
      {badge(len(urgentes),"badge-red","🔴")}
      {badge(len(importantes),"badge-yellow","🟡")}
      {badge(len(atencao),"badge-green","🟢")}
      {badge(len(baixa),"badge-grey","⚪")}
    </div>
  </div>

  <div class="refresh-bar">
    <span id="ts">Atualizado às {hora}</span>
    <button onclick="window.location.reload()">↻ Atualizar agora</button>
  </div>

  {empty}
  {secs}

  {'<div class="plano"><div class="plano-title">✅ Plano de ação</div>' + plano + '</div>' if emails else ''}

  <div class="footer">Auto-atualiza a cada 5 min · <a href="https://meudia.up.railway.app">Painel completo</a></div>
</div>
<script>
  setTimeout(function(){{ window.location.reload(); }}, 5 * 60 * 1000);
</script>
</body>
</html>'''


@app.route('/briefing/<token>')
def briefing(token):
    """Página de triagem ao vivo acessível via token (para Claude / MCP)."""
    sessao = get_sessao_por_token(token)
    if not sessao:
        return '<p style="font-family:sans-serif;padding:2rem;color:#c00">❌ Token inválido ou expirado.<br>Acesse <a href="/login">meudia.up.railway.app</a> e faça login novamente.</p>', 403

    usuario, senha, empresa = sessao
    emails, erros, _ = ler_emails(usuario, senha, horas=18)

    if erros:
        return f'<p style="font-family:sans-serif;padding:2rem;color:#c00">❌ {erros}</p>', 500

    return gerar_html_briefing(emails, usuario, empresa)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5200))
    app.run(debug=False, host='0.0.0.0', port=port)

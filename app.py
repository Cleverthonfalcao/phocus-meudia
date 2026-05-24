"""
Phocus Meu Dia — Triagem diária de e-mails
Standalone Flask app · Locaweb (Phocus) + GoDaddy (Maximize)
"""

import imaplib
import email
import json
import os
import re
from email.header import decode_header
from datetime import datetime, timedelta
from flask import Flask, render_template, request, session, redirect, jsonify
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'phocus-meudia-2026-secret')

# ── Configuração IMAP por domínio ──────────────────────────────────────────────

IMAP_CONFIG = {
    'phocuspropaganda.com.br': {
        'host': 'mail.phocuspropaganda.com.br',
        'port': 993,
        'empresa': 'phocus',
    },
    'maximize': {  # fallback para domínios Maximize
        'host': 'imap.secureserver.net',
        'port': 993,
        'empresa': 'maximize',
    },
}

def get_imap_config(email_addr):
    domain = email_addr.split('@')[-1].lower()
    if domain in IMAP_CONFIG:
        return IMAP_CONFIG[domain]
    # GoDaddy para outros domínios (Maximize)
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

    # Baixa primeiro (automáticos)
    for b in BAIXA_SENDERS:
        if b in r:
            return 'baixa'

    # Urgente
    for k in URGENTE_KEYWORDS:
        if k in s or k in c:
            return 'urgente'

    # Importante
    for k in IMPORTANTE_KEYWORDS:
        if k in s:
            return 'importante'

    # Atenção (e-mails de pessoas reais sem marcadores claros)
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

            # Assunto
            raw, enc = decode_header(msg['Subject'] or '')[0]
            assunto = raw.decode(enc or 'utf-8', errors='replace') if isinstance(raw, bytes) else (raw or '')

            # Remetente
            remetente = msg.get('From', '')

            # Corpo (plain text, primeiros 400 chars)
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

            # Data
            data_str = msg.get('Date', '')
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(data_str)
                data_fmt = dt.strftime('%d/%m %H:%M')
            except Exception:
                data_fmt = data_str[:16]

            prioridade = classificar(assunto, remetente, corpo)

            emails.append({
                'assunto':    assunto,
                'remetente':  remetente,
                'data':       data_fmt,
                'corpo':      corpo.strip(),
                'prioridade': prioridade,
            })

        mail.logout()

    except imaplib.IMAP4.error as e:
        erros = f'Credenciais inválidas ou servidor inacessível: {e}'
    except Exception as e:
        erros = f'Erro ao conectar: {e}'

    # Ordenar por prioridade
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
            # Valida conectando no IMAP
            _, erros, empresa = ler_emails(email_addr, senha, horas=1)
            if erros:
                erro = erros
            else:
                session['email']   = email_addr
                session['senha']   = senha  # criptografado pelo Flask session
                session['empresa'] = empresa
                nome = email_addr.split('@')[0].replace('.', ' ').title()
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
        'urgente':   sum(1 for e in emails if e['prioridade'] == 'urgente'),
        'importante':sum(1 for e in emails if e['prioridade'] == 'importante'),
        'atencao':   sum(1 for e in emails if e['prioridade'] == 'atencao'),
        'baixa':     sum(1 for e in emails if e['prioridade'] == 'baixa'),
    }
    hoje = datetime.now().strftime('%A, %d de %B de %Y').capitalize()
    # Tradução simples
    dias  = {'monday':'Segunda','tuesday':'Terça','wednesday':'Quarta',
             'thursday':'Quinta','friday':'Sexta','saturday':'Sábado','sunday':'Domingo'}
    meses = {'january':'janeiro','february':'fevereiro','march':'março',
             'april':'abril','may':'maio','june':'junho','july':'julho',
             'august':'agosto','september':'setembro','october':'outubro',
             'november':'novembro','december':'dezembro'}
    for en, pt in {**dias, **meses}.items():
        hoje = hoje.replace(en.capitalize(), pt.capitalize()).replace(en, pt)

    return render_template('meu_dia.html',
        emails=emails,
        contadores=contadores,
        hoje=hoje,
        nome=session.get('nome',''),
        empresa=empresa,
        erros=erros,
    )


@app.route('/api/atualizar')
@login_required
def atualizar():
    """Endpoint AJAX para refresh sem recarregar a página."""
    emails, erros, _ = ler_emails(session['email'], session['senha'], horas=18)
    contadores = {
        'urgente':   sum(1 for e in emails if e['prioridade'] == 'urgente'),
        'importante':sum(1 for e in emails if e['prioridade'] == 'importante'),
        'atencao':   sum(1 for e in emails if e['prioridade'] == 'atencao'),
        'baixa':     sum(1 for e in emails if e['prioridade'] == 'baixa'),
    }
    return jsonify({'emails': emails, 'contadores': contadores, 'erros': erros})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5200))
    app.run(debug=False, host='0.0.0.0', port=port)

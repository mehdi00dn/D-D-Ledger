"""Outgoing email (verification links today; password reset and notifications later).

Provider-neutral on purpose: it speaks plain SMTP, which every transactional provider (Resend, Postmark, Brevo,
Amazon SES, Mailgun, ...) offers, so choosing or switching provider is a settings change, not a code change.
Mail is sent while the request waits, because a serverless function cannot finish work after it has answered;
the connection timeout is short and a failure is reported to the caller instead of hanging the page.

Settings (environment):
    MAIL_BACKEND    smtp | console | off      default: smtp when MAIL_HOST is set, else console locally, else off
    MAIL_HOST, MAIL_PORT (587), MAIL_USER, MAIL_PASSWORD
    MAIL_SECURITY   starttls (default) | ssl | none
    MAIL_FROM       e.g. "Campaign Ledger <no-reply@example.com>"  (required for smtp)
    MAIL_TIMEOUT    seconds, default 8
The console backend only logs a short note (never the message body: it contains one-time links); the tests use
the `outbox` list instead.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

log = logging.getLogger('app.mail')

outbox = []          # filled by the 'memory' backend (tests only)


class MailError(Exception):
    """The message could not be handed to the provider; str(exc) is safe to show a user."""


def backend():
    explicit = os.environ.get('MAIL_BACKEND', '').strip().lower()
    if explicit in ('smtp', 'console', 'off', 'memory'):
        return explicit
    if os.environ.get('MAIL_HOST'):
        return 'smtp'
    return 'off' if os.environ.get('VERCEL') else 'console'


def enabled():
    return backend() != 'off'


def _from_address():
    value = os.environ.get('MAIL_FROM', '').strip()
    if not value:
        raise MailError('Email is not set up on this server yet.')
    return value


def build(to, subject, text, html=None, sender=None):
    name, addr = parseaddr(to)
    if not addr or '@' not in addr or any(c in to for c in '\r\n'):
        raise MailError('That email address is not valid.')
    msg = EmailMessage()
    msg['To'] = formataddr((name, addr)) if name else addr
    msg['From'] = sender or _from_address()
    msg['Subject'] = ' '.join(subject.split())          # no header injection through newlines
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    return msg


def send(to, subject, text, html=None):
    kind = backend()
    if kind == 'off':
        raise MailError('Email is not set up on this server yet.')
    if kind == 'memory':
        outbox.append({'to': to, 'subject': subject, 'text': text, 'html': html})
        return
    if kind == 'console':
        log.info('mail (console backend, body not logged): to=%s subject=%r', to, subject)
        return
    msg = build(to, subject, text, html)
    host = os.environ.get('MAIL_HOST', '')
    if not host:
        raise MailError('Email is not set up on this server yet.')
    port = int(os.environ.get('MAIL_PORT', '465' if os.environ.get('MAIL_SECURITY') == 'ssl' else '587'))
    security = os.environ.get('MAIL_SECURITY', 'starttls').lower()
    timeout = float(os.environ.get('MAIL_TIMEOUT', '8'))
    try:
        if security == 'ssl':
            conn = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
        else:
            conn = smtplib.SMTP(host, port, timeout=timeout)
        with conn:
            if security == 'starttls':
                conn.starttls(context=ssl.create_default_context())
            if os.environ.get('MAIL_USER'):
                conn.login(os.environ['MAIL_USER'], os.environ.get('MAIL_PASSWORD', ''))
            conn.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.error('mail send failed (%s): %s', type(exc).__name__, exc)
        raise MailError('The email could not be sent right now. Please try again in a few minutes.') from exc

"""
Email service for processing notifications (completion and failure).

This module provides email functionality using Python's built-in smtplib.
All email features are opt-in via environment variables.
"""

import html
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
from datetime import datetime
from typing import Optional

from src.utils.localization import match_language, translate

logger = logging.getLogger(__name__)




def get_email_config():
    """Get email configuration from environment variables."""
    return {
        'smtp_host': os.environ.get('SMTP_HOST', ''),
        'smtp_port': int(os.environ.get('SMTP_PORT', '587')),
        'smtp_username': os.environ.get('SMTP_USERNAME', ''),
        'smtp_password': os.environ.get('SMTP_PASSWORD', ''),
        'smtp_use_tls': os.environ.get('SMTP_USE_TLS', 'true').lower() == 'true',
        'smtp_use_ssl': os.environ.get('SMTP_USE_SSL', 'false').lower() == 'true',
        'from_address': os.environ.get('SMTP_FROM_ADDRESS', 'noreply@yourdomain.com'),
        'from_name': os.environ.get('SMTP_FROM_NAME', 'Speakr'),
    }


def is_smtp_configured() -> bool:
    """Check if SMTP settings are properly configured."""
    config = get_email_config()
    return bool(config['smtp_host'] and config['smtp_username'] and config['smtp_password'])



# The header logo is attached to the message and referenced by Content-ID
# rather than linked. Three reasons, in order of how often they bite:
# most mail clients block remote images until the reader clicks "show
# images"; a notification is sent from a worker with no request context, so
# url_for(_external=True) raises there and used to fall back to src="",
# which renders as a broken image; and plenty of self-hosted instances are
# not reachable from wherever the recipient reads their mail.
LOGO_CID = 'speakr-logo'
_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'static', 'img', 'icon-192x192.png')


def _load_logo_bytes() -> Optional[bytes]:
    """Read the logo once per process; None if it cannot be read."""
    global _logo_cache
    if _logo_cache is not _LOGO_UNREAD:
        return _logo_cache
    try:
        with open(os.path.normpath(_LOGO_PATH), 'rb') as f:
            _logo_cache = f.read()
    except OSError as e:
        logger.warning(f"Could not read the email logo, sending without it: {e}")
        _logo_cache = None
    return _logo_cache


_LOGO_UNREAD = object()
_logo_cache = _LOGO_UNREAD


def _send_email(to_email: str, subject: str, html_body: str, text_body: str = None) -> bool:
    """
    Send an email using SMTP.

    Returns True if successful, False otherwise.
    """
    config = get_email_config()

    if not is_smtp_configured():
        logger.error("SMTP is not configured. Cannot send email.")
        return False

    try:
        # related( alternative( text, html ), image ) so the HTML part can
        # point at the attached logo by cid: and clients that show only text
        # still get a clean message.
        logo_bytes = _load_logo_bytes()
        body = MIMEMultipart('alternative')
        msg = MIMEMultipart('related') if logo_bytes else body
        msg['Subject'] = subject
        msg['From'] = f"{config['from_name']} <{config['from_address']}>"
        msg['To'] = to_email
        msg['Date'] = formatdate(localtime=True)
        from_domain = config['from_address'].rsplit('@', 1)[-1] if '@' in config['from_address'] else None
        msg['Message-ID'] = make_msgid(domain=from_domain)

        # Add plain text version
        if text_body:
            body.attach(MIMEText(text_body, 'plain'))

        # Add HTML version
        body.attach(MIMEText(html_body, 'html'))

        if logo_bytes:
            msg.attach(body)
            logo = MIMEImage(logo_bytes, _subtype='png')
            # The angle brackets are what the cid: reference resolves against.
            logo.add_header('Content-ID', f'<{LOGO_CID}>')
            logo.add_header('Content-Disposition', 'inline', filename='speakr.png')
            msg.attach(logo)

        # Connect to SMTP server
        if config['smtp_use_ssl']:
            server = smtplib.SMTP_SSL(config['smtp_host'], config['smtp_port'])
        else:
            server = smtplib.SMTP(config['smtp_host'], config['smtp_port'])
            if config['smtp_use_tls']:
                server.starttls()

        server.login(config['smtp_username'], config['smtp_password'])
        server.sendmail(config['from_address'], to_email, msg.as_string())
        server.quit()

        # The address at DEBUG only; the callers log the user id.
        logger.info("Email sent successfully")
        logger.debug(f"Email sent successfully to {to_email}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending email: {e}")
        return False
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False


def _get_email_template(content_html: str, content_text: str, subject: str,
                        language: str = 'en') -> tuple[str, str]:
    """
    Wrap content in the Speakr email template, its own text in ``language``.

    Returns (html_body, text_body)
    """
    tagline = translate('server.email.common.tagline', language)
    # Points at the image _send_email attaches. When the logo cannot be read
    # the attachment is skipped and this src resolves to nothing, so the alt
    # text carries the brand instead of a broken-image icon.
    logo_url = f'cid:{LOGO_CID}'

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #1f2937; margin: 0; padding: 0; background-color: #e8eaed;">
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #e8eaed;">
        <tr>
            <td style="padding: 40px 20px;">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600" style="max-width: 600px; margin: 0 auto;">
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #2563eb; padding: 32px 40px; border-radius: 12px 12px 0 0;">
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                <tr>
                                    <td>
                                        <!-- Logo and Brand -->
                                        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                                            <tr>
                                                <td style="vertical-align: middle; padding-right: 12px;">
                                                    <img src="{logo_url}" alt="Speakr" width="44" height="44" style="display: block; border-radius: 8px;">
                                                </td>
                                                <td style="vertical-align: middle;">
                                                    <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: 700; letter-spacing: -0.5px;">Speakr</h1>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding-top: 8px;">
                                        <p style="color: rgba(255,255,255,0.85); margin: 0; font-size: 14px;">{_h(tagline)}</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Content -->
                    <tr>
                        <td style="background-color: #ffffff; padding: 40px; border-left: 1px solid #e5e7eb; border-right: 1px solid #e5e7eb;">
                            {content_html}
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 24px 40px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none;">
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                <tr>
                                    <td style="text-align: center;">
                                        <p style="color: #6b7280; font-size: 12px; margin: 0 0 8px 0;">
                                            {_h(translate('server.email.common.footer', language))}
                                        </p>
                                        <p style="color: #9ca3af; font-size: 11px; margin: 0;">
                                            &copy; {datetime.utcnow().year} Speakr &middot; {_h(tagline)}
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

    text_body = f"""
{subject}
{'=' * len(subject)}

{content_text}

---
{translate('server.email.common.textFooter', language, tagline=tagline)}
{translate('server.email.common.contactAdmin', language)}
"""

    return html_body, text_body


def _recipient_language(user) -> str:
    """The recipient's interface language, for mail sent outside their own session."""
    return match_language(getattr(user, 'ui_language', None)) or 'en'


# --- Processing notifications (#386) -------------------------------------
#
# These are sent from a job-queue
# worker thread with no request context, so `url_for(_external=True)` is not
# available: it needs SERVER_NAME, which Speakr does not set. The deep link
# therefore comes from APP_BASE_URL, and when that is unset the mail is sent
# without a link rather than with a broken one.

# An SSO login whose provider withheld an address gets a synthetic one (see
# src/auth/sso.py). Mailing it would hard-bounce on every finished recording,
# which is a good way to get a sending domain blocked.
_UNDELIVERABLE_EMAIL_SUFFIXES = ('@placeholder.local',)


def _h(value) -> str:
    """Escape a value for interpolation into an email's HTML body.

    Every value these templates interpolate is user-controlled: usernames have
    only a length validator, and recording titles are set from the API or
    generated by the LLM from transcript content, which can originate from
    somebody other than the recipient (a watch-folder drop, a file handed over
    to be transcribed). Mail clients do not run scripts, so the risk is not
    XSS; it is content injection into a genuine, correctly-addressed Speakr
    email, which is what would make an injected link convincing.

    Only the HTML alternative is escaped. Doing the same to the text/plain
    twin would show the reader literal "&amp;".
    """
    return html.escape(str(value if value is not None else ''), quote=True)



def get_app_base_url() -> Optional[str]:
    """External base URL of this instance, e.g. https://speakr.example.com."""
    base = (os.environ.get('APP_BASE_URL') or '').split('#')[0].strip()
    return base.rstrip('/') or None


def _recording_url(recording_id: int) -> Optional[str]:
    base = get_app_base_url()
    return f'{base}/recordings/{recording_id}' if base else None


def can_email_user(user) -> bool:
    """Whether Speakr is able to send this user mail at all.

    Kept separate from the user's preference so that "they asked for it" and
    "we can actually deliver it" stay distinct in the logs.
    """
    if not is_smtp_configured():
        return False
    email = (getattr(user, 'email', '') or '').strip().lower()
    if not email or '@' not in email:
        return False
    return not email.endswith(_UNDELIVERABLE_EMAIL_SUFFIXES)


def wants_completion_email(user) -> bool:
    """Whether this user has opted in and can be reached."""
    return bool(getattr(user, 'notify_email_on_completion', False)) and can_email_user(user)


def _button_html(url: str, label: str) -> str:
    if not url:
        return ''
    return f"""
<div style="text-align: center; margin: 32px 0;">
    <a href="{url}" style="display: inline-block; background-color: #2563eb; color: #ffffff; text-decoration: none; padding: 14px 32px; border-radius: 8px; font-weight: 600; font-size: 16px;">{label}</a>
</div>
"""


def _detail_rows_html(rows) -> str:
    """A small label/value table, matching the body type scale."""
    cells = ''.join(f"""
    <tr>
        <td style="padding: 6px 16px 6px 0; color: #6b7280; font-size: 14px; white-space: nowrap; vertical-align: top;">{_h(label)}</td>
        <td style="padding: 6px 0; color: #374151; font-size: 14px;">{_h(value)}</td>
    </tr>""" for label, value in rows)
    return f"""
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 24px 0; background-color: #f8f9fa; border-radius: 8px; padding: 8px 16px;">
    {cells}
</table>
"""


def _format_duration(seconds) -> Optional[str]:
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f'{hours}h {minutes}m'
    if minutes:
        return f'{minutes}m {secs}s'
    return f'{secs}s'


def send_transcription_complete_email(user, recording) -> bool:
    """Tell a user their recording finished transcribing.

    Returns True only if a message was actually handed to the SMTP server.
    """
    if not wants_completion_email(user):
        return False

    title = (getattr(recording, 'title', None) or 'Untitled recording').strip()
    url = _recording_url(recording.id)
    subject = f'Transcription ready: {title}'

    rows = [('Recording', title)]
    duration = _format_duration(getattr(recording, 'audio_duration_seconds', None))
    if duration:
        rows.append(('Length', duration))
    took = _format_duration(getattr(recording, 'transcription_duration_seconds', None))
    if took:
        rows.append(('Transcribed in', took))

    content_html = f"""
<h2 style="color: #1f2937; margin: 0 0 24px 0; font-size: 24px; font-weight: 600;">Your transcription is ready</h2>

<p style="color: #374151; margin: 0 0 16px 0; font-size: 16px;">Hi {_h(user.username)},</p>

<p style="color: #374151; margin: 0 0 8px 0; font-size: 16px;">
    Speakr has finished transcribing <strong>{_h(title)}</strong>. It is waiting for you in your library.
</p>

{_detail_rows_html(rows)}
{_button_html(url, 'Open recording')}

<div style="margin-top: 32px; padding-top: 24px; border-top: 1px solid #e5e7eb;">
    <p style="color: #9ca3af; font-size: 13px; margin: 0;">
        You are receiving this because you turned on email notifications in your Speakr account settings. You can turn them off there at any time.
    </p>
</div>
"""

    detail_lines = '\n'.join(f'{label}: {value}' for label, value in rows)
    link_line = f'\nOpen it here: {url}\n' if url else ''
    content_text = f"""Hi {user.username},

Speakr has finished transcribing "{title}". It is waiting for you in your library.

{detail_lines}
{link_line}
You are receiving this because you turned on email notifications in your Speakr
account settings. You can turn them off there at any time."""

    html_body, text_body = _get_email_template(content_html, content_text, subject)
    return _send_email(user.email, subject, html_body, text_body)


def send_transcription_failed_email(user, recording, error: str = None) -> bool:
    """Tell a user their recording could not be transcribed."""
    if not wants_completion_email(user):
        return False

    title = (getattr(recording, 'title', None) or 'Untitled recording').strip()
    url = _recording_url(recording.id)
    subject = f'Transcription failed: {title}'

    # The worker's error text can carry an upstream URL or key, so it is
    # summarised rather than forwarded in full.
    # next(iter(...), '') rather than [0]: a whitespace-only error is truthy
    # but strips to nothing, and ''.splitlines() is an empty list.
    reason = next(iter((error or '').strip().splitlines()), '')[:200]
    reason_html = f"""
<p style="color: #6b7280; font-size: 14px; margin: 0 0 24px 0; padding: 12px 16px; background-color: #f8f9fa; border-left: 3px solid #d1d5db; border-radius: 4px;">{_h(reason)}</p>
""" if reason else ''

    content_html = f"""
<h2 style="color: #1f2937; margin: 0 0 24px 0; font-size: 24px; font-weight: 600;">A transcription did not finish</h2>

<p style="color: #374151; margin: 0 0 16px 0; font-size: 16px;">Hi {_h(user.username)},</p>

<p style="color: #374151; margin: 0 0 24px 0; font-size: 16px;">
    Speakr was not able to transcribe <strong>{_h(title)}</strong>. The recording itself is safe and you can retry it from your library.
</p>

{reason_html}
{_button_html(url, 'Open recording')}

<div style="margin-top: 32px; padding-top: 24px; border-top: 1px solid #e5e7eb;">
    <p style="color: #9ca3af; font-size: 13px; margin: 0;">
        You are receiving this because you turned on email notifications in your Speakr account settings. You can turn them off there at any time.
    </p>
</div>
"""

    reason_text = f'\nReason: {reason}\n' if reason else ''
    link_line = f'\nOpen it here: {url}\n' if url else ''
    content_text = f"""Hi {user.username},

Speakr was not able to transcribe "{title}". The recording itself is safe and you
can retry it from your library.
{reason_text}{link_line}
You are receiving this because you turned on email notifications in your Speakr
account settings. You can turn them off there at any time."""

    html_body, text_body = _get_email_template(content_html, content_text, subject)
    return _send_email(user.email, subject, html_body, text_body)

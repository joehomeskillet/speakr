"""
Authentication and user management routes.

This blueprint handles login, logout, account management, and password
changes.
"""

import os
import re
import mimetypes
from time import time
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session, current_app, make_response
from markupsafe import escape
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Email, ValidationError
from email_validator import validate_email, EmailNotValidError
from werkzeug.security import generate_password_hash, check_password_hash

from src.database import db
from src.clinic import identity
from src.clinic.config import clinic_mode_enabled
from src.models import User, SystemSetting, GroupMembership
from src.utils import password_check, is_safe_url
from src.services import audit, login_lock
from src.utils.localization import format_file_size, format_number, translate, api_error, request_language, default_language, LazyTranslation
from src.auth.sso import (
    init_sso_client,
    is_sso_enabled,
    get_sso_config,
    get_sso_client,
    create_or_update_sso_user,
    is_domain_allowed,
    link_sso_to_existing_user,
    resolve_userinfo,
    update_user_profile_from_claims,
)
from src.services.email import can_email_user

# Create blueprint
auth_bp = Blueprint('auth', __name__)


@auth_bp.context_processor
def inject_page_translation():
    """Server-side text for the sign-in pages (login).

    These pages are plain forms that work without JavaScript, so their text
    is translated here rather than by i18n.js: ``t('key', name=value)`` in
    the viewer's language (their interface language when signed in, else
    the browser's Accept-Language), and ``page_language`` for <html lang>.
    """
    language = request_language()
    return {
        't': lambda key, **params: translate(key, language, **params),
        'page_language': language,
    }

# Import these from app after initialization
bcrypt = None
csrf = None
limiter = None

def init_auth_extensions(_bcrypt, _csrf, _limiter):
    """Initialize extensions after app creation."""
    global bcrypt, csrf, limiter
    bcrypt = _bcrypt
    csrf = _csrf
    limiter = _limiter


def rate_limit(limit_string):
    """Decorator that applies a per-endpoint Flask-Limiter limit.

    The limiter is injected via init_auth_extensions AFTER this module is
    imported (and after the routes below are decorated), so we can't wrap the
    view with limiter.limit() at decoration time. Instead we apply and cache
    it lazily on the first request. Previously this decorator was a no-op that
    only stashed the limit string, so login and password change ran with
    only the loose global default — a brute-force exposure.
    """
    def decorator(f):
        from functools import wraps
        state = {'limited': None}

        @wraps(f)
        def wrapper(*args, **kwargs):
            if limiter is not None and getattr(limiter, 'enabled', True):
                if state['limited'] is None:
                    state['limited'] = limiter.limit(limit_string)(f)
                return state['limited'](*args, **kwargs)
            return f(*args, **kwargs)

        wrapper._rate_limit = limit_string
        return wrapper
    return decorator


# --- Forms ---

# Trim + lowercase an email field before validators run. Fixes the mobile
# autofill case where a trailing space makes Email() reject an otherwise-valid
# address, and normalizes what gets stored so accounts stay case-consistent.
_normalize_email_filter = lambda x: x.strip().lower() if x else x

# Validator messages in the language of the request that shows them (#477).
_REQUIRED = LazyTranslation('server.form.required')
_INVALID_EMAIL = LazyTranslation('server.form.invalidEmail')

_DOMAIN_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$')


class AccountEmail:
    """WTForms Email(), plus addresses on special-use domains such as .local (#551).

    email_validator refuses special-use and reserved domains (.local, .home.arpa
    and others) whatever its options, so an internal mail domain cannot pass
    Email(). Sign-in accepts any well-formed one, since it only looks the
    account up. For these addresses only the part before the @ is checked by
    email_validator.
    """

    def __init__(self):
        self.email = Email(message=_INVALID_EMAIL)

    def __call__(self, form, field):
        try:
            self.email(form, field)
            return
        except ValidationError:
            local, at, domain = (field.data or '').rpartition('@')
            if not (at and local and _DOMAIN_RE.match(domain)):
                raise
            try:
                validate_email(f'{local}@example.com', check_deliverability=False)
            except EmailNotValidError:
                raise ValidationError(_INVALID_EMAIL) from None


class LoginForm(FlaskForm):
    email = StringField('Email', filters=[_normalize_email_filter], validators=[
        DataRequired(message=_REQUIRED), AccountEmail()])
    password = PasswordField('Password', validators=[DataRequired(message=_REQUIRED)])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')


class ClinicLoginForm(LoginForm):
    email = StringField('Loginname oder E-Mail')


# --- Helper Functions ---

def _email_notifications_available(user) -> bool:
    """Whether to offer this user the completion-email toggle (#386).

    Offering a switch that cannot do anything is worse than hiding it, so the
    account page asks the same question the sender will ask: is SMTP set up,
    and is this user's address one we can actually deliver to.
    """
    return can_email_user(user)


def _admin_disabled_setting(key, default=False):
    """Whether a global admin switch is on, normalising the strings
    SystemSetting stores for boolean rows."""
    value = SystemSetting.get_setting(key, default)
    return value if isinstance(value, bool) else str(value).lower() == 'true'


# --- Routes ---

@auth_bp.route('/api/user/timezone', methods=['POST'])
@login_required
def record_timezone():
    """Save the browser's timezone for a user in 'auto' mode (#412).

    Sent by static/js/timezone-sync.js once per browser session. A user who
    chose a zone in Account settings keeps it.
    """
    from src.utils.timezones import record_browser_timezone, user_timezone, MODE_AUTO
    data = request.get_json(silent=True) or {}
    if record_browser_timezone(current_user, data.get('timezone')):
        db.session.commit()
    return jsonify({
        'timezone': user_timezone(current_user),
        'mode': current_user.timezone_mode or MODE_AUTO,
    })


def _password_accepted(user, password):
    """Check a sign-in password against the account and the login lock (#529).

    The hash is always checked, so a locked account answers as slowly as a
    wrong password. While the account is locked for this client address, a
    correct password is refused too, and the count does not change.
    """
    return _password_result(user, password)[0]


def _password_result(user, password):
    """(accepted, reason): reason is the audit code of a refusal, else None."""
    matches = bcrypt.check_password_hash(user.password, password)
    max_attempts, minutes = login_lock.lock_settings()
    if max_attempts == 0:
        return matches, (None if matches else 'bad_password')
    key = login_lock.client_key(request.remote_addr)
    if login_lock.is_locked(user.id, key):
        return False, 'locked'
    if matches:
        login_lock.clear_failures(user.id, key)
        db.session.commit()
        return True, None
    login_lock.record_failure(user.id, key, max_attempts, minutes)
    return False, 'bad_password'


def _audit_login_failed(user, reason, method='password'):
    """One row per minute per address and account. The typed address is never stored."""
    audit.record('auth.login.failed', outcome='failed',
                 target_user_id=user.id if user is not None else None,
                 details={'reason': reason, 'method': method}, dedup=audit.DEDUP_LOGIN_FAILED)


_dummy_password_hash = None


def _dummy_password_check(password):
    """Run a bcrypt check of the same cost as a real one, for a sign-in with no
    local password to compare against (#534), so the response time does not
    show whether the address is registered. Always returns False."""
    global _dummy_password_hash
    if _dummy_password_hash is None:
        _dummy_password_hash = bcrypt.generate_password_hash(os.urandom(16).hex()).decode('utf-8')
    bcrypt.check_password_hash(_dummy_password_hash, password or '')
    return False


def clinic_session_matches(policy):
    return (session.get('_user_id') == current_user.get_id()
            and session.get('clinic_security_version') == policy.security_version
            and session.get('clinic_provider') == 'local')


def clinic_recent_auth(policy):
    authenticated_at = session.get('clinic_reauth_at')
    return (clinic_session_matches(policy) and isinstance(authenticated_at, (float, int))
            and 0 <= time() - authenticated_at <= 300)


def _clinic_sign_in(user, security_version):
    session.clear()
    session['_remember'] = 'clear'
    login_user(user, remember=False)
    session.update(clinic_provider='local', clinic_security_version=security_version, clinic_reauth_at=time())


def _clear_clinic_session():
    logout_user()
    session.clear()
    session['_remember'] = 'clear'


def _clinic_password_error(exc):
    if isinstance(exc, identity.HashBusy):
        return 'hash_busy', 'Bitte später erneut versuchen.', 503
    if isinstance(exc, identity.PasswordPolicyError):
        return exc.code, 'Das Passwort muss 15–128 Zeichen lang sein und darf nicht auf der Sperrliste stehen.', 400
    return 'invalid_recovery_code', 'Der Einmalcode ist ungültig, abgelaufen oder wurde bereits verwendet.', 400


def _clinic_recovery_failed(code):
    """Audit a failed redemption and count it against the account the code belongs to, if any."""
    owner = None
    if isinstance(code, str) and 20 <= len(code) <= 128:
        row = identity.ClinicLocalRecovery.query.filter_by(token_hash=identity._hash_code(code)).first()
        owner = row.user_id if row else None
    audit.record('auth.login.failed', outcome='failed', target_user_id=owner,
                 details={'reason': 'bad_recovery_code', 'method': 'recovery'}, dedup=audit.DEDUP_LOGIN_FAILED)
    if owner is not None and not identity.account_locked(owner):
        identity.record_account_failure(owner)


def _clinic_redeem(data):
    password = data.get('new_password')
    if not password or password != data.get('confirmation', data.get('confirm_password')):
        return jsonify(code='password_mismatch', error='Bitte das neue Passwort eingeben und bestätigen.'), 400
    try:
        identity.complete_recovery(data.get('code'), password, purpose=data.get('purpose'))
    except identity.IdentityError as exc:
        db.session.rollback()
        if isinstance(exc, identity.InvalidRecoveryCode):
            _clinic_recovery_failed(data.get('code'))
        code, message, status = _clinic_password_error(exc)
        return jsonify(code=code, error=message), status
    _clear_clinic_session()
    response = make_response('', 204)
    return _mark_signed_out(response)


def _clinic_login():
    if request.method == 'POST':
        # This is a browser entry point, even if API credentials were also sent.
        csrf.protect()
        data = request.get_json(silent=True) if request.is_json else request.form
        if data is not None and hasattr(data, 'get') and data.get('purpose') in ('activate_local', 'reset_local'):
            if current_user.is_authenticated:
                return jsonify(code='sign_out_first', error='Bitte zuerst abmelden.'), 409
            return _clinic_redeem(data)
    if current_user.is_authenticated:
        return redirect(url_for('recordings.index'))
    stale_session = '_user_id' in session
    if stale_session:
        _clear_clinic_session()

    form = ClinicLoginForm()
    if form.validate_on_submit():
        login_id = request.form.get('login_id', form.email.data or '').strip()
        candidate = identity.find_login_user(login_id)
        maximum, minutes = login_lock.lock_settings()
        locked = candidate is not None and identity.account_locked(candidate.id)
        try:
            result = identity.authenticate_local(login_id, form.password.data)
        except identity.AuthenticationFailed:
            db.session.rollback()
            if candidate is not None and not locked:
                identity.record_account_failure(candidate.id)
            result = None
        except identity.HashBusy:
            return jsonify(code='hash_busy', error='Bitte später erneut versuchen.'), 503
        if result is not None and not locked and not result.must_change_password:
            if maximum:
                identity.clear_account_failures(result.user_id)
                db.session.commit()
            user = db.session.get(User, result.user_id, populate_existing=True)
            policy = db.session.get(identity.ClinicAccountPolicy, result.user_id, populate_existing=True)
            if user and policy and policy.security_version == result.security_version:
                _clinic_sign_in(user, result.security_version)
                audit.record('auth.login.succeeded', actor_kind='session', actor_user_id=user.id,
                             details={'method': 'password', 'remember': False})
                next_page = request.args.get('next')
                return redirect(next_page if next_page and is_safe_url(next_page) else url_for('recordings.index'))
        _audit_login_failed(candidate, 'locked' if locked else 'bad_password')
        flash('Anmeldung fehlgeschlagen.', 'danger')

    response = make_response(render_template('login.html', title='Anmelden', form=form, sso_enabled=False,
                                             password_login_disabled=False))
    return _mark_signed_out(response) if stale_session else response


@auth_bp.route('/login', methods=['GET', 'POST'])
@rate_limit("10 per minute")
def login():
    if clinic_mode_enabled():
        return _clinic_login()
    if current_user.is_authenticated:
        return redirect(url_for('recordings.index'))

    sso_enabled = is_sso_enabled()
    sso_config = get_sso_config()
    if sso_enabled:
        init_sso_client(current_app)

    password_login_disabled = sso_enabled and sso_config.get('disable_password_login', False)

    form = LoginForm()
    if form.validate_on_submit():
        user = User.find_by_email(form.email.data)
        # Every attempt pays for one bcrypt check and gets the same message,
        # whether or not the account exists or has a local password (#534).
        if user and user.password:
            accepted, refusal = _password_result(user, form.password.data)
        else:
            accepted = _dummy_password_check(form.password.data)
            refusal = 'no_local_password' if user else 'unknown_account'
        if password_login_disabled and not (accepted and user.is_admin):
            # Only an admin with the correct password gets past this.
            _audit_login_failed(user, 'password_login_disabled')
            flash(translate('server.flash.passwordLoginDisabled'), 'warning')
        elif not accepted:
            _audit_login_failed(user, refusal)
            flash(translate('server.flash.loginFailed'), 'danger')
        else:
            login_user(user, remember=form.remember.data)
            audit.record('auth.login.succeeded', actor_kind='session', actor_user_id=user.id,
                         details={'method': 'password', 'remember': bool(form.remember.data)})
            next_page = request.args.get('next')
            if not is_safe_url(next_page):
                return redirect(url_for('recordings.index'))
            return redirect(next_page) if next_page else redirect(url_for('recordings.index'))

    return render_template(
        'login.html',
        title='Login',
        form=form,
        sso_enabled=sso_enabled,
        sso_provider_name=sso_config.get('provider_name', 'SSO'),
        password_login_disabled=password_login_disabled
    )


@auth_bp.route('/auth/sso/login')
@rate_limit("10 per minute")
def sso_login():
    if clinic_mode_enabled() or not is_sso_enabled():
        flash(translate('server.flash.ssoNotConfigured'), 'danger')
        return redirect(url_for('auth.login'))

    oauth = get_sso_client() or init_sso_client(current_app)
    if not oauth:
        flash(translate('server.flash.ssoClientFailed'), 'danger')
        return redirect(url_for('auth.login'))

    next_url = request.args.get('next')
    if next_url and is_safe_url(next_url):
        session['sso_next'] = next_url
    else:
        session.pop('sso_next', None)
    # "Remember me" on the sign-in page, as for a password sign-in (#437)
    session['sso_remember'] = request.args.get('remember') == '1'

    return _leave_for_provider(oauth.sso.authorize_redirect(redirect_uri=get_sso_config().get('redirect_uri')))


def _leave_for_provider(provider_redirect):
    """Send the browser on to the identity provider from a page, not with a 302.

    The sign-in and link buttons are forms, and browsers apply the CSP
    form-action directive to every redirect that follows a form submission,
    so a 302 to the provider is blocked by form-action 'self' (#496).
    """
    location = escape(provider_redirect.headers['Location'])
    label = escape(translate('authPages.signInWith', provider=get_sso_config().get('provider_name') or 'SSO'))
    response = make_response(
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={location}"></head>'
        f'<body><a href="{location}">{label}</a></body></html>'
    )
    response.headers['Cache-Control'] = 'no-store'
    return response


@auth_bp.route('/auth/sso/callback')
@rate_limit("20 per minute")
def sso_callback():
    if clinic_mode_enabled() or not is_sso_enabled():
        flash(translate('server.flash.ssoNotConfigured'), 'danger')
        return redirect(url_for('auth.login'))

    oauth = get_sso_client() or init_sso_client(current_app)
    if not oauth:
        flash(translate('server.flash.ssoClientFailed'), 'danger')
        return redirect(url_for('auth.login'))

    try:
        token = oauth.sso.authorize_access_token()
        userinfo = resolve_userinfo(oauth, token)
    except Exception as e:
        current_app.logger.warning(f"SSO callback error: {e}")
        _audit_login_failed(None, 'sso_error', method='sso')
        flash(translate('server.flash.ssoLoginFailed'), 'danger')
        return redirect(url_for('auth.login'))

    subject = userinfo.get('sub')
    if not subject:
        _audit_login_failed(None, 'sso_error', method='sso')
        flash(translate('server.flash.ssoMissingSubject'), 'danger')
        return redirect(url_for('auth.login'))

    link_user_id = session.pop('sso_link_user_id', None)
    next_url = session.pop('sso_next', None)
    remember = bool(session.pop('sso_remember', False))
    cfg = get_sso_config()

    if link_user_id:
        target_user = db.session.get(User, int(link_user_id))
        if not target_user:
            flash(translate('server.flash.ssoLinkUserNotFound'), 'danger')
            return redirect(url_for('auth.account'))

        existing = User.query.filter_by(sso_subject=subject).first()
        if existing and existing.id != target_user.id:
            flash(translate('server.flash.ssoAlreadyLinked'), 'danger')
            return redirect(url_for('auth.account'))

        update_user_profile_from_claims(target_user, userinfo)
        target_user.sso_provider = cfg.get('provider_name', 'SSO')
        target_user.sso_subject = subject
        audit.record_in_tx('auth.sso.linked', target_user_id=target_user.id)
        db.session.commit()
        flash(translate('server.flash.ssoLinked'), 'success')
        return redirect(url_for('auth.account'))

    try:
        user = create_or_update_sso_user(userinfo)
    except PermissionError as e:
        _audit_login_failed(None, 'sso_error', method='sso')
        flash(str(e), 'danger')
        return redirect(url_for('auth.login'))
    except ValueError as e:
        _audit_login_failed(None, 'sso_error', method='sso')
        flash(str(e), 'danger')
        return redirect(url_for('auth.login'))
    except Exception as e:
        current_app.logger.warning(f"SSO login error: {e}")
        _audit_login_failed(None, 'sso_error', method='sso')
        flash(translate('server.flash.ssoLoginIncomplete'), 'danger')
        return redirect(url_for('auth.login'))

    login_user(user, remember=remember)
    audit.record('auth.login.succeeded', actor_kind='session', actor_user_id=user.id,
                 details={'method': 'sso', 'remember': remember})

    if next_url and is_safe_url(next_url):
        return redirect(next_url)
    return redirect(url_for('recordings.index'))


@auth_bp.route('/auth/sso/link', methods=['POST'])
@login_required
def sso_link():
    if clinic_mode_enabled() or not is_sso_enabled():
        flash(translate('server.flash.ssoNotConfigured'), 'danger')
        return redirect(url_for('auth.account'))

    session['sso_link_user_id'] = current_user.id
    session['sso_next'] = url_for('auth.account')

    return redirect(url_for('auth.sso_login'))


@auth_bp.route('/auth/sso/unlink', methods=['POST'])
@login_required
def sso_unlink():
    if clinic_mode_enabled():
        return jsonify(code='provider_unavailable', error='Dieser Anmeldeanbieter ist hier nicht verfügbar.'), 403
    if not current_user.sso_subject:
        flash(translate('server.flash.ssoNotLinked'), 'warning')
        return redirect(url_for('auth.account'))

    if not current_user.password:
        flash(translate('server.flash.ssoUnlinkNoPassword'), 'danger')
        return redirect(url_for('auth.account'))

    current_user.sso_provider = None
    current_user.sso_subject = None
    audit.record_in_tx('auth.sso.unlinked')
    db.session.commit()
    flash(translate('server.flash.ssoUnlinked'), 'success')
    return redirect(url_for('auth.account'))


SIGNED_OUT_COOKIE = 'speakr_signed_out'


def signed_out_redirect():
    """The answer to a sign-out: the sign-in page, with a mark for its script.

    The cookie speakr_signed_out tells static/js/service-worker-client.js on
    the page that follows to clear what the account left in the browser
    (sessionStorage, the IndexedDB stores with recordings, Cache Storage).
    It holds no data, is readable by scripts on purpose, and is removed by
    that script; without it, it ends with the browser session.
    """
    return _mark_signed_out(redirect(url_for('auth.login')))


def _mark_signed_out(response):
    response.set_cookie(SIGNED_OUT_COOKIE, '1', secure=request.is_secure, httponly=False,
                        samesite='Lax', path='/')
    return response


@auth_bp.route('/logout', methods=['POST'])
def logout():
    # POST-only so the global CSRF check applies: a cross-site GET (e.g.
    # <img src="/logout">) can no longer force-log-out a victim. The menu
    # "Sign out" controls are CSRF-token-carrying POST forms.
    if clinic_mode_enabled():
        csrf.protect()
    if current_user.is_authenticated:
        audit.record('auth.logout')
    if clinic_mode_enabled():
        _clear_clinic_session()
    else:
        logout_user()
    return signed_out_redirect()


def _storage_usage(user):
    try:
        from src.services.storage_quota import usage
        return usage(user)
    except Exception as e:
        current_app.logger.warning(f"Could not read storage usage for user {user.id}: {e}")
        return None


def _usage_limits(user):
    """AI tokens and transcription minutes this month, and storage, each
    against its limit when one is set, for the Account page (#413). The
    numbers are in the interface language's notation (#481)."""

    def row(key, label_key, icon, used, limit, fmt):
        pct = round(used / limit * 100, 1) if limit else None
        return {'key': key, 'label_key': label_key, 'icon': icon, 'used': fmt(used),
                'limit': fmt(limit) if limit else None, 'percentage': pct}

    rows = []
    try:
        from src.services.token_tracking import token_tracker
        rows.append(row('tokens', 'account.aiTokensMonth', 'fa-coins',
                        int(token_tracker.get_monthly_usage(user.id) or 0), user.monthly_token_budget,
                        lambda n: format_number(int(n))))
    except Exception as e:
        current_app.logger.warning(f"Could not read token usage for user {user.id}: {e}")
    try:
        from src.services.transcription_tracking import transcription_tracker
        seconds = int(transcription_tracker.get_monthly_usage(user.id) or 0)
        budget = user.monthly_transcription_budget
        rows.append(row('transcription', 'account.transcriptionMonth', 'fa-microphone',
                        seconds // 60, (budget // 60) if budget else None,
                        lambda n: translate('adminDashboard.statsPage.minutesShort', count=format_number(int(n)))))
    except Exception as e:
        current_app.logger.warning(f"Could not read transcription usage for user {user.id}: {e}")
    storage = _storage_usage(user)
    if storage:
        rows.append(row('storage', 'account.storage', 'fa-hdd', storage['used_bytes'], storage['quota_bytes'], format_file_size))
    return rows


@auth_bp.route('/api/account/storage', methods=['GET'])
@login_required
def account_storage():
    """The signed-in user's storage use and quota (#413)."""
    from src.services.storage_quota import usage
    return jsonify(usage(current_user))


@auth_bp.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    # Import here to avoid circular imports
    from flask import current_app

    if request.method == 'POST':
        # Only update fields that are present in the form submission
        # This prevents clearing data when switching between tabs

        # Check if this is the account information form (has user_name field)
        if 'user_name' in request.form:
            # Handle personal information updates only -- language preferences
            # have moved to the Preferences tab and are handled below.
            user_name = request.form.get('user_name')
            user_job_title = request.form.get('user_job_title')
            user_company = request.form.get('user_company')

            current_user.name = user_name if user_name else None
            current_user.job_title = user_job_title if user_job_title else None
            current_user.company = user_company if user_company else None

        # Check if this is the preferences form (UI/display + language settings)
        elif 'preferences_form' in request.form:
            current_user.show_timestamps_simple_view = 'show_timestamps_simple_view' in request.form
            current_user.editor_autosave = 'editor_autosave' in request.form
            # Keyboard shortcuts: only when the form rendered the checkbox, so a
            # form without it cannot turn them off.
            if 'keyboard_shortcuts_shown' in request.form:
                current_user.keyboard_shortcuts = 'keyboard_shortcuts' in request.form
            # Auto-translation: only when the section rendered, and only if
            # the administrator allows it globally. The account page hides the
            # checkbox otherwise, so an absent field must not clear a value the
            # user set while the feature was available.
            if 'auto_translation_shown' in request.form:
                current_user.auto_translation = (
                    'auto_translation' in request.form
                    and not _admin_disabled_setting('disable_auto_translation', True)
                )
            # Only honour the notification toggle when the form actually
            # rendered it. The checkbox is hidden when Speakr cannot mail this
            # user, and an absent checkbox must not silently clear a
            # preference they set while SMTP was working.
            if 'email_notifications_shown' in request.form:
                current_user.notify_email_on_completion = 'notify_email_on_completion' in request.form
            # Audio player position — only update if the field is
            # actually present in the submission. The Preferences tab
            # (which doesn't expose the radio) submits without this
            # key and must not clobber the value the user set from the
            # Display tab. Only 'top' / 'bottom' are accepted; anything
            # else falls back to the default 'bottom'.
            if 'audio_player_position' in request.form:
                pos = request.form.get('audio_player_position', 'bottom')
                current_user.audio_player_position = pos if pos in ('bottom', 'top') else 'bottom'
            # Default recording sort: same "only if submitted" rule as above. Anything
            # other than the two known sorts is stored as NULL, which means 'created_at'.
            # The assistant's name: same "only if submitted" rule. A name
            # outside the rule (src/utils/assistant_name.py) is refused.
            if 'assistant_name' in request.form:
                from src.utils.assistant_name import clean_assistant_name
                try:
                    current_user.assistant_name = clean_assistant_name(request.form.get('assistant_name'))
                except ValueError:
                    message = translate('server.apiError.invalid_assistant_name')
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json':
                        return jsonify({'success': False, 'error': message}), 400
                    flash(message, 'error')
                    return redirect(url_for('auth.account'))
            if 'default_recording_sort' in request.form:
                sort = request.form.get('default_recording_sort')
                current_user.default_recording_sort = sort if sort in ('created_at', 'meeting_date') else None

            ui_lang = request.form.get('ui_language')
            transcription_lang = request.form.get('transcription_language')
            output_lang = request.form.get('output_language')
            current_user.ui_language = ui_lang if ui_lang else default_language()
            # Normalize transcription_language so legacy display-name values
            # ("Français", "Deutsch", ...) can't reach the ASR call (issue #256).
            from src.utils.language import normalize_language_code
            normalized_lang = normalize_language_code(transcription_lang)
            # A stored value outside the list is shown as its own option on
            # the account page. Posted back unchanged, it is kept (#613);
            # only a different choice replaces it.
            if not (normalized_lang is None and transcription_lang
                    and transcription_lang == current_user.transcription_language):
                current_user.transcription_language = normalized_lang
            current_user.output_language = output_lang if output_lang else None

            # Timezone for generated titles (#412). Gated on the field being
            # present so tabs that submit 'preferences_form' without it leave the
            # setting alone. 'auto' keeps the detected zone and resumes updating
            # it from the browser; a zone name fixes it.
            if 'timezone' in request.form:
                from src.utils.timezones import is_valid_timezone, MODE_AUTO, MODE_FIXED
                tz_value = (request.form.get('timezone') or '').strip()
                if tz_value == MODE_AUTO or not tz_value:
                    current_user.timezone_mode = MODE_AUTO
                    detected = (request.form.get('detected_timezone') or '').strip()
                    if is_valid_timezone(detected):
                        current_user.timezone = detected
                elif is_valid_timezone(tz_value):
                    current_user.timezone_mode = MODE_FIXED
                    current_user.timezone = tz_value

            # Filename date parsing (#342). Gated on the select being present
            # so other tabs that submit 'preferences_form' without this
            # section cannot clobber the settings (same reasoning as
            # audio_player_position above).
            if 'filename_date_pattern' in request.form:
                from src.utils.filename_dates import VALID_PATTERN_KEYS, validate_custom_regex
                pattern = request.form.get('filename_date_pattern', 'auto')
                if pattern not in VALID_PATTERN_KEYS:
                    pattern = 'auto'
                custom_regex = (request.form.get('filename_date_regex') or '').strip() or None
                if pattern == 'custom':
                    regex_error = validate_custom_regex(custom_regex or '')
                    if regex_error:
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json':
                            return jsonify({'success': False, 'error': regex_error}), 400
                        flash(regex_error, 'error')
                        return redirect(url_for('auth.account'))
                current_user.parse_filename_dates = 'parse_filename_dates' in request.form
                current_user.filename_date_pattern = pattern
                current_user.filename_date_regex = custom_regex

        # Check if this is the custom prompts form (has summary_prompt field)
        elif 'summary_prompt' in request.form:
            # Handle custom prompt updates
            summary_prompt_text = request.form.get('summary_prompt')
            current_user.summary_prompt = summary_prompt_text if summary_prompt_text else None
            # Gated on its own field so a form without it cannot clear it (#400).
            if 'title_prompt' in request.form:
                title_prompt_text = (request.form.get('title_prompt') or '').strip()
                current_user.title_prompt = title_prompt_text or None
            # Inquire content availability (agentic inquire). Gated on the
            # marker field so form submissions without this section cannot
            # clobber the settings (same reasoning as audio_player_position).
            if 'inquire_privacy_section' in request.form:
                current_user.inquire_allow_summaries = 'inquire_allow_summaries' in request.form
                current_user.inquire_allow_notes = 'inquire_allow_notes' in request.form
            # Handle transcription hints
            hotwords = request.form.get('transcription_hotwords')
            current_user.transcription_hotwords = hotwords if hotwords else None
            initial_prompt = request.form.get('transcription_initial_prompt')
            current_user.transcription_initial_prompt = initial_prompt if initial_prompt else None

            # Per-feature transcript timestamp settings (#304). A template id is
            # only accepted if it belongs to the user; otherwise fall back to the
            # built-in default format (None).
            def _ts_template_id(field):
                raw = (request.form.get(field) or '').strip()
                if not raw.isdigit():
                    return None
                from src.models import TranscriptTemplate
                tpl = TranscriptTemplate.query.filter_by(id=int(raw), user_id=current_user.id).first()
                return tpl.id if tpl else None

            current_user.summary_include_timestamps = 'summary_include_timestamps' in request.form
            # Gated on its marker field so another form cannot clear it (#440)
            if 'summary_notes_section' in request.form:
                current_user.summary_include_notes = 'summary_include_notes' in request.form
            current_user.summary_timestamp_template_id = _ts_template_id('summary_timestamp_template_id')

        # Check if this is the export filename template form (#348)
        elif 'export_filename_template' in request.form:
            template_value = (request.form.get('export_filename_template') or '').strip()
            # Path separators would let a template escape the per-user export
            # directory; reject them outright.
            if '/' in template_value or '\\' in template_value:
                error_msg = translate('server.flash.exportTemplateSeparators')
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json':
                    return jsonify({'success': False, 'error': error_msg}), 400
                flash(error_msg, 'danger')
                return redirect(url_for('auth.account'))
            # Empty string = use the default template (recording_{{id}})
            current_user.export_filename_template = template_value if template_value else None

        db.session.commit()

        # Return JSON response for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json':
            return jsonify({'success': True, 'message': translate('server.apiMessage.accountUpdated')})

        # Regular form submission with redirect
        flash(translate('server.flash.accountUpdated'), 'success')

        # Preserve the active tab when redirecting
        if 'summary_prompt' in request.form:
            return redirect(url_for('auth.account') + '#prompts')
        else:
            return redirect(url_for('auth.account'))

    from src.models import SystemSetting
    # Get admin default prompt from system settings
    from src.clinic.prompts import admin_summary_prompt, shipped_summary_prompt
    admin_default_prompt = admin_summary_prompt(SystemSetting.get_setting('admin_default_summary_prompt', None))
    if admin_default_prompt:
        default_summary_prompt_text = admin_default_prompt
    else:
        # Fallback to the shipped default if admin hasn't set one.
        default_summary_prompt_text = shipped_summary_prompt()

    # The title instructions a user gets when they leave their own blank.
    from src.services.titling import user_title_instructions
    default_title_prompt_text = user_title_instructions(None)[0]

    USE_ASR_ENDPOINT = os.environ.get('USE_ASR_ENDPOINT', 'false').lower() == 'true'
    USE_NEW_TRANSCRIPTION_ARCHITECTURE = os.environ.get('USE_NEW_TRANSCRIPTION_ARCHITECTURE', 'true').lower() == 'true'
    ENABLE_AUTO_DELETION = bool(SystemSetting.get_setting('enable_auto_deletion', os.environ.get('ENABLE_AUTO_DELETION', 'false').lower() == 'true'))
    ASR_RETURN_SPEAKER_EMBEDDINGS = os.environ.get('ASR_RETURN_SPEAKER_EMBEDDINGS', 'false').lower() == 'true'
    ENABLE_AUTO_EXPORT = os.environ.get('ENABLE_AUTO_EXPORT', 'false').lower() == 'true'

    # Get connector capabilities (new architecture)
    connector_supports_diarization = USE_ASR_ENDPOINT  # Default to USE_ASR_ENDPOINT for backwards compat
    connector_supports_hotwords = USE_ASR_ENDPOINT
    connector_supports_initial_prompt = USE_ASR_ENDPOINT
    connector_supports_speaker_embeddings = ASR_RETURN_SPEAKER_EMBEDDINGS
    if USE_NEW_TRANSCRIPTION_ARCHITECTURE:
        try:
            from src.services.transcription import get_registry
            from src.services.transcription.base import TranscriptionCapability
            registry = get_registry()
            connector = registry.get_active_connector()
            if connector:
                connector_supports_diarization = connector.supports_diarization
                connector_supports_hotwords = connector.supports_hotwords
                connector_supports_initial_prompt = connector.supports_initial_prompt
                # Query the live instance: the asr_endpoint connector adds the
                # SPEAKER_EMBEDDINGS capability conditionally (only when its env
                # flag is on), so the class-level set would be wrong.
                connector_supports_speaker_embeddings = connector.supports(
                    TranscriptionCapability.SPEAKER_EMBEDDINGS
                )
        except Exception as e:
            current_app.logger.warning(f"Could not get connector capabilities: {e}")

    # Speaker auto-labelling comes in two flavours. When the connector returns
    # voice embeddings, matching is biometric and tuned by a confidence
    # threshold. When it diarizes but returns no embeddings, we fall back to a
    # contextual LLM match; the toggle should still be offered in that case (the
    # threshold does not apply). Surfacing both lets the account page show the
    # toggle for embedding-less connectors, where it used to be hidden.
    contextual_labelling_available = (
        connector_supports_diarization and not connector_supports_speaker_embeddings
    )

    # Check if user is a team admin and get their admin groups
    admin_memberships = GroupMembership.query.filter_by(
        user_id=current_user.id,
        role='admin'
    ).all()

    is_team_admin = len(admin_memberships) > 0

    # Build list of groups where user is admin (for tag assignment)
    user_admin_groups = []
    for membership in admin_memberships:
        if membership.group:
            user_admin_groups.append({
                'id': membership.group.id,
                'name': membership.group.name
            })

    sso_config = get_sso_config()
    sso_enabled = not clinic_mode_enabled() and is_sso_enabled()
    if sso_enabled:
        init_sso_client(current_app)
    sso_linked = bool(current_user.sso_subject)

    password_login_disabled = sso_enabled and sso_config.get('disable_password_login', False)

    # Check if admin has globally disabled auto-summarization or auto-translation
    admin_disabled_auto_summarization = _admin_disabled_setting('disable_auto_summarization', False)
    auto_translation_available = not _admin_disabled_setting('disable_auto_translation', True)
    from src.models import SystemSetting as _SS
    r=_SS.query.filter_by(key='disable_auto_translation').first()
    from src.models.system import _SYSTEM_SETTING_CACHE_GENERATION

    # Get user's UI language preference
    user_language = current_user.ui_language if current_user.ui_language else default_language()

    from src.services.inquire_agent import get_availability as _get_inquire_availability
    _inquire_availability = _get_inquire_availability(current_user)

    return render_template('account.html',
                           title='Account',
                           storage_usage=_storage_usage(current_user),
                           usage_limits=_usage_limits(current_user),
                           default_summary_prompt_text=default_summary_prompt_text,
                           default_title_prompt_text=default_title_prompt_text,
                           use_asr_endpoint=USE_ASR_ENDPOINT,
                           connector_supports_diarization=connector_supports_diarization,
                           connector_supports_hotwords=connector_supports_hotwords,
                           connector_supports_initial_prompt=connector_supports_initial_prompt,
                           enable_auto_deletion=ENABLE_AUTO_DELETION,
                           user_admin_groups=user_admin_groups,
                           is_team_admin=is_team_admin,
                           sso_enabled=sso_enabled,
                           sso_provider_name=sso_config.get('provider_name', 'SSO'),
                           sso_linked=sso_linked,
                           sso_subject=current_user.sso_subject,
                           has_password=bool(current_user.password),
                           password_login_disabled=password_login_disabled,
                           speaker_embeddings_enabled=connector_supports_speaker_embeddings,
                           email_notifications_available=_email_notifications_available(current_user),
                           contextual_labelling_available=contextual_labelling_available,
                           auto_speaker_labelling=current_user.auto_speaker_labelling,
                           auto_speaker_labelling_threshold=current_user.auto_speaker_labelling_threshold or 'medium',
                           admin_disabled_auto_summarization=admin_disabled_auto_summarization,
                           auto_translation_available=auto_translation_available,
                           auto_summarization=current_user.auto_summarization if current_user.auto_summarization is not None else True,
                           inquire_allow_summaries=_inquire_availability['summaries'],
                           inquire_allow_notes=_inquire_availability['notes'],
                           user_language=user_language,
                           enable_auto_export=ENABLE_AUTO_EXPORT,
                           enable_auto_processing=os.environ.get('ENABLE_AUTO_PROCESSING', 'false').lower() == 'true')


@auth_bp.route('/api/user/auto-speaker-labelling', methods=['POST'])
@login_required
def update_auto_speaker_labelling():
    """Update user's auto speaker labelling settings."""
    data = request.get_json()

    if data is None:
        return jsonify({'success': False, **api_error('invalid_json')}), 400

    # Update enabled state
    if 'enabled' in data:
        current_user.auto_speaker_labelling = bool(data['enabled'])

    # Update threshold (validate value)
    if 'threshold' in data:
        threshold = data['threshold']
        if threshold in ('low', 'medium', 'high'):
            current_user.auto_speaker_labelling_threshold = threshold
        else:
            return jsonify({'success': False, **api_error('invalid_threshold')}), 400

    db.session.commit()

    return jsonify({
        'success': True,
        'auto_speaker_labelling': current_user.auto_speaker_labelling,
        'auto_speaker_labelling_threshold': current_user.auto_speaker_labelling_threshold
    })


@auth_bp.route('/api/user/auto-summarization', methods=['POST'])
@login_required
def update_auto_summarization():
    """Update user's auto summarization setting."""
    data = request.get_json()

    if data is None:
        return jsonify({'success': False, **api_error('invalid_json')}), 400

    if 'enabled' in data:
        current_user.auto_summarization = bool(data['enabled'])
        db.session.commit()

    return jsonify({
        'success': True,
        'auto_summarization': current_user.auto_summarization
    })


@auth_bp.route('/change_password', methods=['POST'])
@login_required
@rate_limit("10 per minute")
def change_password():
    if clinic_mode_enabled():
        csrf.protect()
        data = request.form
        password = data.get('new_password')
        if not password or password != data.get('confirm_password'):
            flash('Bitte das neue Passwort eingeben und bestätigen.', 'danger')
            return redirect(url_for('auth.account'))
        user = current_user._get_current_object()
        policy = db.session.get(identity.ClinicAccountPolicy, user.id)
        if not policy or not clinic_session_matches(policy):
            return jsonify(code='local_password_not_permitted', error='Kein lokaler Passwortwechsel möglich.'), 403
        try:
            user_id = user.id
            version = identity.change_password(user_id, data.get('current_password'), password,
                                               expected_security_version=policy.security_version)
        except identity.AuthenticationFailed:
            db.session.rollback()
            return jsonify(code='local_password_not_permitted', error='Kein lokaler Passwortwechsel möglich.'), 403
        except identity.IdentityError as exc:
            db.session.rollback()
            code, message, status = _clinic_password_error(exc)
            return jsonify(code=code, error=message), status
        user = db.session.get(User, user_id, populate_existing=True)
        policy = db.session.get(identity.ClinicAccountPolicy, user_id, populate_existing=True)
        if not user or not policy or policy.security_version != version:
            _clear_clinic_session()
            return signed_out_redirect()
        _clinic_sign_in(user, version)
        flash('Das Passwort wurde geändert.', 'success')
        return redirect(url_for('auth.account'))
    # Check if password management is disabled for non-admins
    sso_config = get_sso_config()
    password_login_disabled = is_sso_enabled() and sso_config.get('disable_password_login', False)
    if password_login_disabled and not current_user.is_admin:
        flash(translate('server.flash.passwordManagementDisabled'), 'warning')
        return redirect(url_for('auth.account'))

    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    # Check if user has an existing password
    has_existing_password = bool(current_user.password)

    # GHSA-x4q4-3ww4-h329 (Irina Iarlykanova) — SSO-only account takeover.
    # When an SSO-provisioned user has no local password, the old current-
    # password check was bypassed unconditionally, so anyone with a valid
    # session (including via the chained CSRF bypass that was the primary
    # finding) could silently set a password on an SSO-only account and
    # then log in directly with the new credentials, bypassing SSO.
    #
    # This endpoint therefore refuses to set a password on an account that
    # has none; an administrator issues the credential instead.
    if not has_existing_password:
        flash(translate('server.flash.noLocalPassword'), 'warning')
        return redirect(url_for('auth.account'))

    # Validate form data - current password only required if user has one
    if has_existing_password and not current_password:
        flash(translate('server.flash.currentPasswordRequired'), 'danger')
        return redirect(url_for('auth.account'))

    if not new_password or not confirm_password:
        flash(translate('server.flash.newPasswordRequired'), 'danger')
        return redirect(url_for('auth.account'))

    if new_password != confirm_password:
        flash(translate('server.flash.newPasswordMismatch'), 'danger')
        return redirect(url_for('auth.account'))

    # Custom validation for new password
    try:
        password_check(None, type('obj', (object,), {'data': new_password}))
    except ValidationError as e:
        flash(str(e), 'danger')
        return redirect(url_for('auth.account'))

    # Verify current password only if user has one
    if has_existing_password:
        if not bcrypt.check_password_hash(current_user.password, current_password):
            flash(translate('server.flash.currentPasswordIncorrect'), 'danger')
            return redirect(url_for('auth.account'))

    # Update password
    hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
    current_user.password = hashed_password
    # Sign the account out of every other browser (#437), and sign this one
    # in again with the new login id.
    remembered = current_app.config.get('REMEMBER_COOKIE_NAME', 'remember_token') in request.cookies
    current_user.end_all_sessions()
    audit.record_in_tx('auth.password.changed')
    db.session.commit()
    # The real object, not the proxy: login_user stores what it is given as
    # the request's user, and a stored proxy resolves to itself, so any later
    # current_user access (the translated flash below) would recurse forever.
    login_user(current_user._get_current_object(), remember=remembered)

    flash(translate('server.flash.passwordUpdated'), 'success')
    return redirect(url_for('auth.account'))

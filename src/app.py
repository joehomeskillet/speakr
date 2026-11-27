# Speakr - Audio Transcription and Summarization App
if __name__ == '__main__':
    # Started as a script (python src/app.py). The file is loaded once, as the
    # module src.app. Left to run on as __main__, it would be loaded a second
    # time as soon as another module imports src.app: a second application
    # object, a second run of the startup steps and two logger names (app and
    # src.app) in one log.
    import argparse as _argparse
    import os as _os
    import sys as _sys
    _parser = _argparse.ArgumentParser()
    _parser.add_argument('--debug', action='store_true', help='Run in debug mode')
    _args = _parser.parse_args()
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from src.app import main as _main
    _main(debug=_args.debug)
    raise SystemExit(0)

import os
import sys
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash, Response, make_response, session
from urllib.parse import urlparse, urljoin, quote
from email.utils import encode_rfc2231
from markupsafe import Markup
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from openai import OpenAI # Keep using the OpenAI library
import json
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from sqlalchemy import select
from sqlalchemy.orm import joinedload
import threading
from dotenv import load_dotenv # Import load_dotenv
import httpx 
import re
import subprocess
import mimetypes
import bleach

# Add common audio MIME type mappings that might be missing
from src.utils.mime import SPEAKR_MEDIA_TYPES
for _media_type, _media_ext in SPEAKR_MEDIA_TYPES:
    mimetypes.add_type(_media_type, _media_ext)
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect, CSRFError
from wtforms import StringField, PasswordField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import ast
import logging
import secrets
import time
from src.audio_chunking import AudioChunkingService, ChunkProcessingError, ChunkingNotSupportedError
from src.utils.localization import api_error, format_number, translate

# Optional imports for embedding functionality
try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from sklearn.metrics.pairwise import cosine_similarity
    EMBEDDINGS_AVAILABLE = True
except ImportError as e:
    EMBEDDINGS_AVAILABLE = False
    # Create dummy classes to prevent import errors
    class SentenceTransformer:
        def __init__(self, *args, **kwargs):
            pass
        def encode(self, *args, **kwargs):
            return []
    
    np = None
    cosine_similarity = None

# Load environment variables from .env file
load_dotenv()

# Hot-applied System Settings (high priority) with environment fallback.
from src.utils.system_settings import apply_hot_settings
_SYSTEM_SETTING_MAP = {
    "ENABLE_AUTO_DELETION": ("enable_auto_deletion", "ENABLE_AUTO_DELETION", False),
    "GLOBAL_RETENTION_DAYS": ("global_retention_days", "GLOBAL_RETENTION_DAYS", 0),
    "DELETION_MODE": ("deletion_mode", "DELETION_MODE", "full_recording"),
    "USERS_CAN_DELETE": ("users_can_delete", "USERS_CAN_DELETE", True),
    "VIDEO_RETENTION": ("video_retention", "VIDEO_RETENTION", False),
}
_self, __getattr__ = apply_hot_settings(__name__, _SYSTEM_SETTING_MAP)

# Early check for Inquire Mode configuration (needed for startup message)
ENABLE_INQUIRE_MODE = os.environ.get('ENABLE_INQUIRE_MODE', 'false').lower() == 'true'

# Auto-deletion and retention configuration

# Permission-based deletion control

# Video retention - when enabled, video files keep their video stream for playback

# Log embedding status on startup. Two paths can power Inquire mode: a local
# sentence-transformers model (full image) or an OpenAI-compatible HTTP
# provider (works in the lite image too, as long as scikit-learn is present
# for cosine similarity, which it is in both images).
_inquire_uses_api = bool(os.environ.get('EMBEDDING_BASE_URL', '').strip())
try:
    from sklearn.metrics.pairwise import cosine_similarity as _has_sklearn  # noqa: F401
    _SKLEARN_AVAILABLE = True
except ImportError:
    _SKLEARN_AVAILABLE = False

if ENABLE_INQUIRE_MODE and EMBEDDINGS_AVAILABLE:
    print("✅ Inquire Mode: Full semantic search enabled (local embedding model)")
elif ENABLE_INQUIRE_MODE and _inquire_uses_api and _SKLEARN_AVAILABLE:
    print("✅ Inquire Mode: Full semantic search enabled (API embedding provider)")
elif ENABLE_INQUIRE_MODE and _inquire_uses_api and not _SKLEARN_AVAILABLE:
    print("⚠️  Inquire Mode: Basic text search only (scikit-learn missing, cannot rank API embeddings)")
elif ENABLE_INQUIRE_MODE and not EMBEDDINGS_AVAILABLE:
    print("⚠️  Inquire Mode: Basic text search only (embedding dependencies not available)")
    print("   To enable semantic search, install sentence-transformers locally OR set EMBEDDING_BASE_URL to use an OpenAI-compatible API.")
elif not ENABLE_INQUIRE_MODE:
    print("ℹ️  Inquire Mode: Disabled (set ENABLE_INQUIRE_MODE=true to enable)")

# Log auto-deletion status
if _self.ENABLE_AUTO_DELETION:
    if _self.GLOBAL_RETENTION_DAYS > 0:
        print(f"✅ Auto-deletion: Enabled (global retention: {_self.GLOBAL_RETENTION_DAYS} days, mode: {_self.DELETION_MODE})")
    else:
        print("⚠️  Auto-deletion: Enabled but no global retention period set (configure GLOBAL_RETENTION_DAYS)")
else:
    print("ℹ️  Auto-deletion: Disabled (set ENABLE_AUTO_DELETION=true to enable)")

# Log deletion permissions
if _self.USERS_CAN_DELETE:
    print("ℹ️  User deletion: Enabled (all users can delete their recordings)")
else:
    print("🔒 User deletion: Restricted (only admins can delete recordings)")

# Log video retention status
if _self.VIDEO_RETENTION:
    print("✅ Video retention: Enabled (video files preserve video stream for playback)")
else:
    print("ℹ️  Video retention: Disabled (video uploads extract audio only)")

# Configure logging
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(log_level)
# File names are taken out of tracebacks above DEBUG (#620).
from src.utils.log_filters import FileNamesOutOfTracebacks as _LogFormatter
formatter = _LogFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

# Get the root logger and clear any existing handlers to avoid duplicates
root_logger = logging.getLogger()
root_logger.handlers.clear()
root_logger.setLevel(log_level)
root_logger.addHandler(handler)

# httpx logs each request URL at INFO: without its query string, which holds
# the initial prompt and the hotwords of a transcription request.
from src.utils.log_filters import install as _install_log_filters
_install_log_filters()

# Silence noisy markdown extension debug logs
markdown_logger = logging.getLogger('MARKDOWN')
markdown_logger.setLevel(logging.WARNING)

# --- Rate Limiting Setup (will be configured after app creation) ---
# RATELIMIT_ENABLED (default true) lets the test suite and any deployment that
# fronts its own rate limiting turn Flask-Limiter off. The per-endpoint auth
# limits (login, password change) are applied via the rate_limit
# decorator in src/api/auth.py and src/api/tokens.py.
_RATELIMIT_ENABLED = os.environ.get('RATELIMIT_ENABLED', 'true').lower() != 'false'
def _token_request_exempt():
    # Token traffic has per-token limits (src/utils/token_auth.require_scope);
    # the app-wide per-IP defaults apply to session traffic only (mailr spec G1).
    from src.utils.token_auth import is_token_request
    return is_token_request()


limiter = Limiter(
    get_remote_address,
    app=None,  # Defer initialization
    default_limits=["5000 per day", "1000 per hour"],
    default_limits_exempt_when=_token_request_exempt,
    enabled=_RATELIMIT_ENABLED,
)

# --- Utility Functions ---
# Utility functions (JSON parsing, markdown, datetime, security) have been extracted
# to src/utils/ and imported at the top of this file

def has_recording_access(recording, user, require_edit=False):
    """
    Check if a user has access to a recording.

    Only the owner has access here; there is no sharing between users. The
    clinic policy (src/clinic/) decides access in clinic mode.

    Args:
        recording: Recording object to check access for
        user: User object (typically current_user)
        require_edit: Kept for the callers; the owner may always edit

    Returns:
        Boolean indicating if user has the required access level
    """
    return recording.user_id == user.id


def get_user_recording_status(recording, user):
    """
    Get the inbox and highlighted status for a recording.

    Args:
        recording: Recording object
        user: User object (typically current_user)

    Returns:
        Tuple of (is_inbox, is_highlighted)
    """
    return (recording.is_inbox, recording.is_highlighted)


def set_user_recording_status(recording, user, is_inbox=None, is_highlighted=None, commit=True):
    """
    Set the inbox and/or highlighted status for a recording.

    Args:
        recording: Recording object
        user: User object (typically current_user)
        is_inbox: Boolean or None (None means don't change)
        is_highlighted: Boolean or None (None means don't change)
        commit: False leaves the commit to the caller (the API v1 PATCH routes)

    Returns:
        Tuple of (is_inbox, is_highlighted) after update
    """
    if is_inbox is not None:
        recording.is_inbox = is_inbox
    if is_highlighted is not None:
        recording.is_highlighted = is_highlighted
    if commit:
        db.session.commit()
    return (recording.is_inbox, recording.is_highlighted)


def enrich_recording_dict_with_user_status(recording_dict, recording, user):
    """
    Enrich a recording dictionary with per-user status (inbox, highlighted).

    This should be called after recording.to_dict() or recording.to_list_dict()
    to replace the owner's status with the current user's per-user status.

    Args:
        recording_dict: Dictionary from recording.to_dict() or recording.to_list_dict()
        recording: Recording object
        user: User object (typically current_user)

    Returns:
        The enriched recording_dict (modified in place, but also returned for convenience)
    """
    user_inbox, user_highlighted = get_user_recording_status(recording, user)
    recording_dict['is_inbox'] = user_inbox
    recording_dict['is_highlighted'] = user_highlighted
    from src.services.recording_state import get_user_archived
    recording_dict['is_archived'] = get_user_archived(recording, user)
    return recording_dict


app = Flask(__name__, 
            template_folder='../templates',
            static_folder='../static')
# Use environment variables or default paths for Docker compatibility
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('SQLALCHEMY_DATABASE_URI', 'sqlite:////data/instance/transcriptions.db')
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', '/data/uploads')

# Static files ship with no Cache-Control by default, which lets browsers
# apply heuristic freshness (often days/weeks for rarely-changed files).
# After an image upgrade that served users a mix of new templates and stale
# JS/locale JSON — a blank UI with "Translation not found" spam (issue #357).
# max-age=0 forces a cheap conditional revalidation (304 unless changed).
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# SQLite concurrency settings for multi-worker job queue
if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'timeout': 30,  # Wait up to 30 seconds for locked database
            'check_same_thread': False  # Allow multi-threaded access
        },
        'pool_pre_ping': True  # Verify connections before use
    }
# MAX_CONTENT_LENGTH will be set dynamically after database initialization
# Set a secret key for session management and CSRF protection.

# The old code fell back to a hardcoded constant when SECRET_KEY was unset.
# That key signs the session cookie AND itsdangerous password-reset tokens,
# so a publicly-known value lets anyone forge a session cookie for any
# user_id (instant admin takeover) and mint valid reset tokens. resolve_secret_key
# refuses the known-bad default and, when nothing is configured, auto-generates
# a strong per-deployment key and persists it — so a self-hoster who never sets
# the env var is secure by default instead of silently exploitable. (The key
# only signs sessions/tokens; it does not encrypt stored data, so a lost key
# means a one-time re-login, never data loss.)
from src.clinic.config import web_only as _clinic_web_only
from src.utils.security import resolve_secret_key as _resolve_secret_key

try:
    _secret_key, _secret_key_action = _resolve_secret_key(
        os.environ.get('SECRET_KEY'),
        app.config['SQLALCHEMY_DATABASE_URI'],
        key_file=os.environ.get('SECRET_KEY_FILE') or None,
        allow_generate=not _clinic_web_only(),
    )
except ValueError as _sk_exc:
    raise RuntimeError(str(_sk_exc))
app.config['SECRET_KEY'] = _secret_key
# The file digests of the log are keyed by a value derived from the secret, so
# that a guessed file name cannot be confirmed from a log line (#620).
from src.utils.log_filters import configure as _configure_log_labels
_configure_log_labels(secret_key=_secret_key, private_dirs=[
    app.config['UPLOAD_FOLDER'],
    os.environ.get('FILE_STORAGE_STAGING_DIR', ''),
    os.environ.get('AUTO_PROCESS_WATCH_DIR', '/data/auto-process'),
    os.environ.get('AUTO_EXPORT_DIR', '/data/exports'),
])
if _secret_key_action == 'generated':
    app.logger.warning(
        "SECRET_KEY was not set; generated a strong random key and persisted "
        "it under the instance directory. It is included in a normal data-volume "
        "backup; set SECRET_KEY explicitly for multi-host or key-rotation setups."
    )
elif _secret_key_action == 'ephemeral':
    app.logger.error(
        "SECRET_KEY not set and an auto-generated key could not be persisted. "
        "Using an ephemeral key: sessions will not survive a restart. Set "
        "SECRET_KEY to fix this."
    )

# Apply ProxyFix to handle headers from a reverse proxy (like Nginx or Caddy)
# This is crucial for request.is_secure to work correctly behind an SSL-terminating proxy.
# TRUSTED_PROXY_HOPS is the hop count; TRUSTED_PROXIES, when set, limits the
# forwarded headers to requests from those peers (#498, #501).
from src.utils.proxy import TrustedProxyMiddleware, log_proxy_trust
app.wsgi_app = TrustedProxyMiddleware(app.wsgi_app)
log_proxy_trust()

# --- Secure Session Cookie Configuration ---
# Default is False so http://localhost / LAN deployments continue to
# work out of the box. Production HTTPS deployments should set
# SESSION_COOKIE_SECURE=true so the session cookie is only sent over
# TLS. Default-on would silently log users out on a fresh HTTP install,
# which is a worse failure mode for the most common self-hosted setup.
app.config['SESSION_COOKIE_SECURE'] = (
    os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
)
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Still protect against XSS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection

# Remember-me cookie (#437). Flask-Login's defaults are a 365-day cookie with
# no Secure flag and no SameSite attribute. It now follows the session
# cookie's Secure setting, is SameSite=Lax, and lasts REMEMBER_COOKIE_DAYS
# (default 30) from the sign-in. It is set only when "Remember me" is ticked:
# REMEMBER_COOKIE_REFRESH_EACH_REQUEST stays off, because with it Flask-Login
# sets the cookie on every response for every signed-in user.
app.config['REMEMBER_COOKIE_SECURE'] = app.config['SESSION_COOKIE_SECURE']
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
try:
    _remember_days = max(1, int(os.environ.get('REMEMBER_COOKIE_DAYS', '30')))
except ValueError:
    _remember_days = 30
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=_remember_days)

# --- Security response headers ---
# Set at the app level so a deployment WITHOUT a hardening reverse proxy is
# still protected (previously the app set only X-Robots-Tag, leaving direct /
# plain-proxy deployments with no clickjacking, MIME-sniffing, or CSP defense).
# All are `setdefault`-applied in add_security_headers so an operator's proxy
# can still override them.
#
# CSP note: the frontend loads Vue's full build (in-DOM template compiler, so
# script execution needs 'unsafe-eval') and bootstraps from inline <script>
# blocks in every template ('unsafe-inline'), so the script-src cannot be
# locked down without a frontend build step that precompiles templates and
# adds per-response nonces. The policy still meaningfully constrains object-src,
# base-uri, form-action, frame-ancestors, and the connect/img/media/font
# origins. Override the whole policy via CONTENT_SECURITY_POLICY, or disable
# the block with SECURITY_HEADERS_ENABLED=false.
SECURITY_HEADERS_ENABLED = os.environ.get('SECURITY_HEADERS_ENABLED', 'true').lower() == 'true'
_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob: data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "manifest-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)
CONTENT_SECURITY_POLICY = os.environ.get('CONTENT_SECURITY_POLICY', _DEFAULT_CSP).strip()
# HSTS is only meaningful (and only emitted) over HTTPS. Configurable so
# operators terminating TLS elsewhere can tune or disable it.
HSTS_HEADER_VALUE = os.environ.get(
    'HSTS_HEADER', 'max-age=63072000; includeSubDomains'
).strip()
if app.config['SESSION_COOKIE_SECURE']:
    app.logger.info("Session cookies marked Secure (HTTPS-only)")
else:
    app.logger.info(
        "Session cookies are NOT marked Secure. Set "
        "SESSION_COOKIE_SECURE=true for production HTTPS deployments."
    )

# Import database instance from extracted module
from src.database import db
db.init_app(app)

# updated_at and tombstones for API clients (mailr spec G2)
from src.services.recording_changes import register_change_tracking
register_change_tracking()

# Import all models from extracted modules
from src.models import (
    User, Speaker, Recording, TranscriptChunk,
    Group, GroupMembership, Tag, RecordingTag,
    TranscriptTemplate, InquireSession, SystemSetting, PushSubscription,
    APIToken, NamingTemplate, Folder, SpeakerSnippet,
    ProcessingJob, TokenUsage, TranscriptionUsage
)

# Import utility functions from extracted modules
from src.utils import (
    auto_close_json, safe_json_loads, preprocess_json_escapes, extract_json_object,
    md_to_html, sanitize_html, password_check,
    add_column_if_not_exists, is_safe_url
)

# Import service layer functions
from src.services.embeddings import (
    get_embedding_model, generate_embeddings,
    serialize_embedding, deserialize_embedding, get_accessible_recording_ids,
    process_recording_chunks, basic_text_search_chunks, semantic_search_chunks
)
from src.transcript_chunking import chunk_transcription
from src.services.llm import (
    is_gpt5_model, is_using_openai_api, call_llm_completion, format_api_error_message
)
from src.services.document import process_markdown_to_docx
from src.services.retention import (
    is_recording_exempt_from_deletion, get_retention_days_for_recording, process_auto_deletion
)

# Import background task functions
from src.tasks.processing import (
    generate_title_task, generate_summary_only_task,
    extract_audio_from_video, transcribe_audio_task, transcribe_with_connector,
    transcribe_chunks_with_connector
)

# Import configuration helpers
from src.clinic.asset_version import clinic_asset_version
from src.clinic.config import clinic_mode_enabled as _clinic_mode_enabled
from src.config.version import get_version

# Initialize Flask-Login and other extensions
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'
bcrypt = Bcrypt()
bcrypt.init_app(app)
# Rate-limit headers, including Retry-After on 429 (mailr spec G1).
app.config.setdefault("RATELIMIT_HEADERS_ENABLED", True)
# The default in-memory storage counts in each gunicorn worker separately, so
# every limit is in effect multiplied by the number of workers. A shared
# backend (redis:// with the bundled client; memcached:// needs pymemcache)
# makes the limits exact (#484).
# While that backend is unreachable, counting falls back to memory instead of
# failing the request.
if os.environ.get("RATELIMIT_STORAGE_URI"):
    app.config.setdefault("RATELIMIT_STORAGE_URI", os.environ["RATELIMIT_STORAGE_URI"])
    app.config.setdefault("RATELIMIT_IN_MEMORY_FALLBACK_ENABLED", True)
limiter.init_app(app)


from flask_limiter.errors import RateLimitExceeded as _RateLimitExceeded


@app.errorhandler(_RateLimitExceeded)
def _rate_limit_exceeded(e):
    """The limiter's own 429 response, plus Retry-After in seconds (mailr spec G1)."""
    import time as _time
    resp = e.get_response()
    retry = None
    try:
        current = limiter.current_limit
        if current is not None:
            retry = max(1, int(current.reset_at - _time.time()))
    except Exception:
        retry = None
    if retry is None:
        try:
            retry = int(e.limit.limit.get_expiry())
        except Exception:
            retry = 60
    from src.utils.token_auth import is_token_request, user_rate_key
    if is_token_request():
        # API clients get JSON that says which budget ran out: the token's own
        # or the one all tokens of the account share (#484).
        from flask import jsonify
        account = getattr(e.limit, 'key_func', None) is user_rate_key
        resp = jsonify({
            'error': e.description,
            'code': 'account_rate_limit_exceeded' if account else 'token_rate_limit_exceeded',
            'limit': str(e.limit.limit),
        })
        resp.status_code = 429
    resp.headers["Retry-After"] = str(retry)
    return resp

_AUDIO_PATH = re.compile(r'/audio/\d+')

# Exempt frequently-polled status endpoints from rate limiting
@limiter.request_filter
def exempt_status_endpoints():
    """Exempt status polling endpoints from rate limiting."""
    from flask import request
    # Exempt status endpoints that are polled frequently during processing.
    # /api/v1/sso/status needs no authentication and keeps its per-IP limit.
    if request.path == '/api/v1/sso/status':
        return False
    if '/status' in request.path and request.method == 'GET':
        return True
    if request.path.endswith('/batch-status') and request.method == 'POST':
        return True
    # Exempt job queue status polling (polled every 5-30 seconds during processing)
    if request.path == '/api/recordings/job-queue-status' and request.method == 'GET':
        return True
    # Exempt server-side recording-session endpoints (#287). These are the
    # RECOMMENDED path for multi-hour recordings, which stream one chunk every
    # few seconds — at the default 5s cadence a recording emits ~720 chunks/hour,
    # and the global IP limit (1000/hour, 5000/day) is shared with all the
    # user's other requests, so a long session would otherwise start getting
    # 429'd partway through and lose its streaming guarantee. The endpoints are
    # login-protected, ownership-checked, and already bounded per user by
    # RECORDING_SESSION_MAX_BYTES_PER_USER / RECORDING_SESSION_MAX_CHUNK_BYTES,
    # so IP-based rate limiting is the wrong control here.
    if request.path.startswith('/upload/session'):
        return True
    # Exempt media playback for signed-in users (#539). A browser reads a
    # recording through HTTP range requests, and a fragmented MP4 (the format
    # OBS writes) costs one request per fragment on load and on every seek:
    # a 30-minute video used 700+ requests, enough to exhaust the hourly IP
    # limit and block playback of every recording for up to an hour. The
    # route is login-protected and access-checked.
    if request.method == 'GET' and _AUDIO_PATH.fullmatch(request.path):
        from flask_login import current_user
        if current_user.is_authenticated:
            return True
    return False

csrf = CSRFProtect(app)

# Disable Flask-WTF's automatic CSRF check so we can run our own
# token-aware version below. The replacement still calls csrf.protect()
# for browser/session requests; the bypass only fires when a valid
# API token is presented via header (never query string).
#
# GHSA-x4q4-3ww4-h329 (Irina Iarlykanova): the previous
# ``csrf_exempt_for_api_tokens`` hook called ``csrf.exempt(view_func)``
# from a request handler. That mutates Flask-WTF's process-global
# ``_exempt_views`` set permanently, so any cross-origin request with
# ``?token=anything`` would silently disable CSRF on the targeted
# endpoint for the rest of the worker's lifetime, allowing follow-up
# CSRF attacks. Replaced with a per-request decision below.
app.config['WTF_CSRF_CHECK_DEFAULT'] = False


# Clinic mode (CLINIC_MODE): central route policy. Registered first so that a
# refused request does nothing else, not even a last-active write. Inert when
# clinic mode is off.
from src.clinic.guard import install_clinic_guard
from src.clinic.policy import install_pool_policy

install_pool_policy(app)
install_clinic_guard(app)


@app.before_request
def record_last_active():
    """Note the time of this use of the signed-in account (admin user list).

    A request of an account counts, from a browser session or with an API
    token, unless its route is one that an open tab requests on a timer
    (src/services/last_active.py holds the list). This runs before the checks
    below, so a request that one of them refuses (a CSRF rejection, a token
    scope the route does not allow) still counts. The write is made outside
    the session of the request, waits a fraction of a second at most and
    swallows its errors, so it can neither delay nor fail the request.
    """
    from src.services.last_active import counts_as_activity, record_activity
    if not counts_as_activity(request.endpoint):
        return
    if not current_user.is_authenticated:
        return
    record_activity(current_user)


@app.before_request
def refuse_query_token_writes():
    """?token= is deprecated and read-only (#500).

    resolve_request_token() ignores a token supplied only as ?token= on any
    method other than GET and HEAD. Such a request is answered here with 401
    and a clear error, before the CSRF check and before any view runs. A
    browser session takes precedence over a token, as in Flask-Login.
    """
    if not request.endpoint or request.endpoint == 'static' or session.get('_user_id'):
        return
    from src.utils.token_auth import query_token_disabled, query_token_refused
    if query_token_disabled():
        # api_query_tokens_enabled is off (privacy review A4).
        resp = jsonify(api_error('query_token_disabled'))
        resp.status_code = 401
        resp.headers['WWW-Authenticate'] = 'Bearer error="invalid_request"'
        return resp
    if query_token_refused():
        resp = jsonify(api_error('query_token_read_only'))
        resp.status_code = 401
        resp.headers['WWW-Authenticate'] = 'Bearer error="invalid_request"'
        return resp


@app.before_request
def csrf_token_aware_check():
    """Per-request CSRF check that allows valid header-only API tokens
    to bypass CSRF, without ever mutating Flask-WTF's exemption set.

    The check honours all the registration-time exemptions Flask-WTF
    already knows about (``csrf.exempt(blueprint)`` and
    ``csrf.exempt(view_func)`` called at module load time), and only
    skips CSRF for the specific request currently being handled.

    Tokens are accepted only from the Authorization, X-API-Token, and
    API-Token headers. The query-string token is intentionally NOT
    honoured here because a Simple Cross-Origin Request can carry one
    without triggering CORS preflight, which is exactly the attack
    vector in GHSA-x4q4-3ww4-h329.
    """
    if not app.config.get('WTF_CSRF_ENABLED', True):
        return
    if request.method not in app.config.get(
        'WTF_CSRF_METHODS', {'POST', 'PUT', 'PATCH', 'DELETE'}
    ):
        return
    if not request.endpoint:
        return

    # Blueprint-level registration-time exemptions (e.g. api_v1_bp).
    # Note that csrf._exempt_blueprints holds Blueprint OBJECTS, so we
    # resolve the request's blueprint name through app.blueprints, the
    # same way Flask-WTF's own protect() hook does.
    #
    # These exemptions exist for token clients. A request that carries a
    # browser session or remember-me cookie is signed in by that cookie,
    # so it still needs a CSRF token here, which the web UI sends (#478).
    if app.blueprints.get(request.blueprint) in csrf._exempt_blueprints:
        remember_cookie = app.config.get('REMEMBER_COOKIE_NAME', 'remember_token')
        if not session.get('_user_id') and remember_cookie not in request.cookies:
            return

    # View-level registration-time exemptions (e.g. share_target).
    # Flask-WTF identifies exempt views by f"{__module__}.{__name__}";
    # match that exactly so any view registered via csrf.exempt() at
    # import time is honoured here.
    view = app.view_functions.get(request.endpoint)
    if view is not None:
        dest = f"{view.__module__}.{view.__name__}"
        if dest in csrf._exempt_views:
            return

    # If a valid header API token is presented, this request is from a
    # programmatic client and CSRF does not apply. The token is validated
    # against the DB by load_user_from_token_headers_only().
    from src.utils.token_auth import load_user_from_token_headers_only
    if load_user_from_token_headers_only() is not None:
        return

    # Otherwise, fall through to the standard CSRF check. If validation
    # fails, csrf.protect() raises a CSRFError which Flask-WTF's error
    # handler turns into a 400 response.
    csrf.protect()


@app.before_request
def enforce_token_scopes():
    """Scoped API tokens (mailr spec G1), checked before any view runs.

    Sessions and full tokens (scopes NULL, every token made before scopes) pass
    unchanged. A scoped token must come from a header, may only reach routes
    marked with require_scope, and must hold every scope the route needs.
    A refused request therefore has no side effect and fires no webhook.
    """
    if not request.endpoint or request.endpoint == 'static':
        return
    if session.get('_user_id'):
        return  # a browser session takes precedence over a token, as in Flask-Login
    from src.utils.token_auth import resolve_request_token, scope_error_response
    api_token, via_query = resolve_request_token()
    if api_token is None or api_token.scope_set is None:
        return
    if via_query:
        resp = jsonify({'error': translate('server.apiError.scoped_token_header_only'),
                        'code': 'invalid_token'})
        resp.status_code = 401
        return resp
    view = app.view_functions.get(request.endpoint)
    required = getattr(view, '_required_scopes', None)
    if required is None:
        return scope_error_response(None, api_token)
    missing = required - api_token.scope_set
    if missing:
        return scope_error_response(missing, api_token)



def _csrf_rejection_message(reason):
    """Turn a Flask-WTF CSRFError description into something an admin can act on.

    Token failures (missing, expired, mismatched) are fixed by reloading the
    page. The two referrer failures are not: Flask-WTF checks the Referer
    header on every HTTPS request, so a proxy that strips it or rewrites the
    Host header makes every POST fail, however often the token is refreshed
    (issue #388). Those get a message that names the proxy setting.
    """
    if 'referrer header is missing' in reason:
        return False, translate('server.apiError.csrf_referrer_missing')
    if 'referrer does not match' in reason:
        referrer_host = urlparse(request.referrer or '').netloc
        if not referrer_host:
            return False, translate('server.apiError.csrf_referrer_mismatch_unknown', host=request.host)
        return False, translate('server.apiError.csrf_referrer_mismatch',
                                referrerHost=referrer_host, host=request.host)
    return True, translate('server.apiError.csrf_session_expired')


def _same_origin_request():
    """True when the Origin header, or the Referer when there is no Origin,
    names this host. Browsers send Origin on every cross-site POST."""
    origin = request.headers.get('Origin')
    if origin and origin != 'null':
        return urlparse(origin).netloc == request.host
    if origin == 'null':
        return False
    referrer = request.referrer
    return bool(referrer) and urlparse(referrer).netloc == request.host


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    """Answer fetch/XHR callers with JSON that says why CSRF validation failed.

    Flask-WTF's default is an HTML 400 page. The frontend cannot tell a
    referrer failure from an expired token in that page, so it refreshes the
    token, retries, fails again and shows a generic error, while the server
    logs nothing. Browser form posts still get the HTML page.
    """
    reason = error.description or ''
    retryable, message = _csrf_rejection_message(reason.lower())
    app.logger.warning(
        'CSRF validation failed for %s %s: %s (Referer host: %s, request host: %s, scheme: %s)',
        request.method, request.path, reason,
        urlparse(request.referrer or '').netloc or '-', request.host, request.scheme,
    )
    is_form_navigation = (
        request.mimetype in ('application/x-www-form-urlencoded', 'multipart/form-data')
        and 'text/html' in request.headers.get('Accept', '')
    )
    if is_form_navigation:
        same_origin = _same_origin_request()
        # Sign out from a page whose token has expired. Only a request from
        # Speakr's own pages is accepted, so a cross-site form still cannot
        # sign anyone out.
        if request.endpoint == 'auth.logout' and same_origin:
            from flask_login import logout_user
            from src.api.auth import signed_out_redirect
            logout_user()
            return signed_out_redirect()
        # Any other form with an expired or missing token: back to the page
        # it was sent from, with a message, in place of a bare 400 page.
        if retryable and same_origin and request.referrer:
            flash(translate('server.flash.pageExpired'), 'warning')
            return redirect(request.referrer)
        return error
    return jsonify({
        'error': message,
        'csrf_reason': reason,
        'csrf_retryable': retryable,
    }), 400


@app.errorhandler(RequestEntityTooLarge)
def handle_request_entity_too_large(_error):
    """Keep oversized requests JSON-shaped even when parsing fails early."""
    limit_mb = float(app.config['MAX_CONTENT_LENGTH']) / (1024 * 1024)
    return jsonify({
        **api_error('file_too_large_max', maxSize=format_number(limit_mb)),
        'max_size_mb': limit_mb,
        'effective_limit_mb': limit_mb,
        'audio_only_mode': False,
    }), 413


# Add context processor to make 'now' available to all templates
@app.context_processor
def inject_now():
    return {'now': datetime.now()}

# Templates use this value for static URLs and the service worker cache. Clinic images
# can change those assets between upstream releases. The visible product version stays in get_version().
_app_version = get_version()

@app.context_processor
def inject_app_version():
    cache_version = clinic_asset_version(_app_version, app.static_folder) if _clinic_mode_enabled() else _app_version
    return {'app_version': cache_version}

from src.utils.i18n_bootstrap import i18n_bootstrap as _i18n_bootstrap

@app.context_processor
def inject_i18n_bootstrap():
    """Translations embedded in pages that load i18n.js (includes/i18n_bootstrap.html)."""
    return {'i18n_bootstrap': _i18n_bootstrap}

@app.context_processor
def inject_account_theme():
    """The theme saved with the signed-in user's account (D#419), for
    includes/theme_account.html. None for a visitor who is not signed in.

    theme_defaults is the color scheme and UI theme a page shows when neither
    the account nor the browser has one saved: for a signed-in account, the
    meaning of NULL (blue, the default theme); for a visitor, the look for new accounts
    (src/config/ui_themes.py new_user_theme), so a fresh install's sign-in
    page looks like a new account."""
    from flask import has_request_context
    from flask_login import current_user
    from src.config import ui_themes
    if not has_request_context() or not current_user.is_authenticated:
        return {'account_theme': None,
                'theme_defaults': {'colorScheme': ui_themes.new_user_color_scheme(),
                                   'uiTheme': ui_themes.new_user_theme()}}
    return {'account_theme': {'dark': current_user.theme_dark_mode,
                              'scheme': current_user.theme_color_scheme,
                              'ui_theme': current_user.ui_theme},
            'theme_defaults': {'colorScheme': ui_themes.DEFAULT_COLOR_SCHEME,
                               'uiTheme': ui_themes.DEFAULT_THEME}}


@app.context_processor
def inject_ui_themes():
    """UI themes (src/config/ui_themes.py) for includes/ui_theme.html, the
    theme cards (includes/ui_theme_picker.html) and the template slots
    (themes/slot.html). Only available themes are listed."""
    from flask import has_request_context, url_for
    from flask_login import current_user
    from src.config import ui_themes
    from src.utils.localization import translate

    def static_url(path):
        return url_for('static', filename=path) if has_request_context() else '/static/' + path

    available = ui_themes.available_themes()
    signed_in = has_request_context() and current_user.is_authenticated
    return {
        'ui_theme_assets': {t.id: (static_url(t.stylesheet) if t.stylesheet else '') for t in available},
        'ui_theme_choices': [{'id': t.id, 'name': translate(t.name_key),
                              'description': translate(t.description_key),
                              'preview_url': static_url(t.preview)} for t in available],
        'ui_theme_labels': {key: translate(f'uiTheme.{key}')
                            for key in ('title', 'help', 'appearance', 'colorSchemeLink')},
        'account_ui_theme': ui_themes.resolve_theme(current_user.ui_theme) if signed_in
                            else ui_themes.DEFAULT_THEME,
        'ui_theme_slot_overrides': ui_themes.slot_overrides,
    }


@app.context_processor
def inject_assistant_name():
    """The assistant's name (src/utils/assistant_name.py) for the templates,
    and for scripts through includes/i18n_bootstrap.html
    (window.speakrAssistantName). The default for a visitor."""
    from flask import has_request_context
    from flask_login import current_user
    from src.utils.assistant_name import assistant_name_of, assistant_initial
    user = current_user if has_request_context() and current_user.is_authenticated else None
    name = assistant_name_of(user)
    return {'assistant_name': name, 'assistant_initial': assistant_initial(name)}


@app.context_processor
def inject_inquire_mode_flag():
    """The shared header shows the Inquire button on every page."""
    return {'inquire_mode_enabled': ENABLE_INQUIRE_MODE}


@app.context_processor
def inject_help_center_link():
    """Optional custom help link in the user menu (#371).

    Admins hosting their own FAQ/tutorial page set HELP_CENTER_URL (and
    optionally HELP_CENTER_LABEL) to surface it prominently for users.
    """
    from src.models import SystemSetting
    return {
        'help_center_url': (SystemSetting.get_setting('help_center_url', os.environ.get('HELP_CENTER_URL', '')) or '').strip(),
        'help_center_label': (SystemSetting.get_setting('help_center_label', os.environ.get('HELP_CENTER_LABEL', '')) or '').strip(),
    }

@app.context_processor
def inject_output_languages():
    """Shared list of output/translation target language names (#translation)."""
    from src.utils.language import OUTPUT_LANGUAGES
    return {'output_languages': OUTPUT_LANGUAGES}

@app.context_processor
def inject_group_admin_status():
    """Inject is_group_admin flag and the admin-group list into all templates.

    user_admin_groups feeds the shared tag/folder modals (group assignment
    dropdown), which render on the account page AND the main app now that
    tags/folders can be created inline from the upload dialog.
    """
    from flask_login import current_user
    from src.models.organization import GroupMembership

    user_admin_groups = []
    can_create_groups = False
    if current_user.is_authenticated:
        # Users who may create groups reach the group management page (#576)
        from src.api.groups import can_create_groups as _can_create_groups
        can_create_groups = _can_create_groups(current_user)
        memberships = GroupMembership.query.filter_by(
            user_id=current_user.id,
            role='admin'
        ).all()
        user_admin_groups = [
            {'id': m.group.id, 'name': m.group.name}
            for m in memberships if m.group
        ]

    return {
        'is_group_admin': bool(user_admin_groups),
        'user_admin_groups': user_admin_groups,
        'can_create_groups': can_create_groups,
    }

# A temporary web worker shares the initialized deployment's upload directory.
if _clinic_web_only():
    if not os.path.isdir(app.config['UPLOAD_FOLDER']):
        raise RuntimeError('The clinic web-only worker requires the existing upload directory.')
else:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# --- User loader for Flask-Login ---
@login_manager.user_loader
def load_user(user_id):
    # "<id>" or "<id>:<session_version>" (see User.get_id). A sign-in made
    # before the password was last set carries an older version and is
    # refused (#437).
    uid, _, version = str(user_id).partition(':')
    try:
        user = db.session.get(User, int(uid))
        version = int(version or 0)
    except ValueError:
        return None
    if user is None or (user.session_version or 0) != version:
        return None
    return user


@login_manager.request_loader
def load_user_from_request(request):
    """
    Load user from API token in the request.

    This enables token-based authentication for API access
    (e.g., from curl, n8n, Zapier, etc.).
    """
    from src.utils.token_auth import load_user_from_token
    return load_user_from_token()


@login_manager.unauthorized_handler
def handle_unauthorized():
    """API requests get a JSON 401; browser page loads keep the login redirect.

    Flask-Login's default is a 302 to /login for everything. Nearly every
    HTTP client follows redirects and treats the resulting 200 text/html
    login page as success, so integrations with a missing/invalid/expired
    token "pass" their connection checks and fail later with confusing
    JSON parse errors — or worse, report an upload as successful after the
    redirect silently dropped the multipart body (issue #333).

    A request is API-shaped when it targets an /api/ path or presented an
    API token in any of the forms token_auth accepts; a token client
    deserves a 401 whichever path it hits.
    """
    presented_token = bool(
        request.headers.get('Authorization', '').startswith('Bearer ')
        or request.headers.get('X-API-Token')
        or request.headers.get('API-Token')
        or request.args.get('token')
    )
    if request.path.startswith('/api/') or presented_token:
        response = jsonify(api_error('authentication_required'))
        response.status_code = 401
        response.headers['WWW-Authenticate'] = 'Bearer'
        return response

    # Browser page load: replicate Flask-Login's default behavior
    # (flash the login message, redirect to the login view with ?next=).
    from flask_login.utils import login_url as _login_url
    if login_manager.login_message:
        from src.utils.localization import translate
        flash(translate('server.flash.loginRequired'), category=login_manager.login_message_category)
    return redirect(_login_url(url_for(login_manager.login_view), request.url))


# --- Embedding and Chunking Utilities ---

from src.api.auth import auth_bp, init_auth_extensions
from src.api.tokens import tokens_bp, init_tokens_helpers
from src.api.recordings import recordings_bp, init_recordings_helpers
from src.api.tags import tags_bp, init_tags_helpers
from src.api.folders import folders_bp, init_folders_helpers
from src.api.groups import groups_bp, init_groups_helpers
from src.api.admin import admin_bp, init_admin_helpers
from src.api.speakers import speakers_bp, init_speakers_helpers
from src.api.inquire import inquire_bp, init_inquire_helpers
from src.api.templates import templates_bp, init_templates_helpers
from src.api.naming_templates import naming_templates_bp
from src.api.export_templates import export_templates_bp
from src.api.initial_prompt_templates import initial_prompt_templates_bp
from src.api.system import system_bp, init_system_helpers
from src.api.push_notifications import push_bp
from src.api.notifications import notifications_bp
from src.api.api_v1 import api_v1_bp, init_api_v1_helpers
from src.api.recording_sessions import recording_sessions_bp
from src.api.webhooks import webhooks_bp

# Database initialization (extracted to src/init_db.py)
from src.init_db import initialize_database
with app.app_context():
    from src.models.system import register_system_setting_cache_invalidation
    register_system_setting_cache_invalidation()
    if _clinic_web_only():
        # Verify that this is a provisioned clinic DB without running DDL or the original-file sweep.
        from src.clinic.models import ClinicDictation
        db.session.execute(select(ClinicDictation.id).limit(0))
    else:
        initialize_database(app)

# Application configuration (extracted to src/config/app_config.py)
from src.config.app_config import initialize_config
client, chunking_service, version = initialize_config(app)

# Initialize blueprint helpers (inject extensions and utility functions)
init_auth_extensions(bcrypt, csrf, limiter)
init_tokens_helpers(bcrypt, csrf, limiter)
from src.services import audit as _audit_log
_audit_log.init_access_check(has_recording_access)
init_recordings_helpers(has_recording_access=has_recording_access, get_user_recording_status=get_user_recording_status, set_user_recording_status=set_user_recording_status, enrich_recording_dict_with_user_status=enrich_recording_dict_with_user_status, bcrypt=bcrypt, csrf=csrf, limiter=limiter, chunking_service=chunking_service)
init_tags_helpers(has_recording_access=has_recording_access, bcrypt=bcrypt, csrf=csrf, limiter=limiter)
init_folders_helpers(has_recording_access=has_recording_access, bcrypt=bcrypt, csrf=csrf, limiter=limiter)
init_groups_helpers(has_recording_access=has_recording_access, bcrypt=bcrypt, csrf=csrf, limiter=limiter)
init_admin_helpers(has_recording_access=has_recording_access, bcrypt=bcrypt, csrf=csrf, limiter=limiter)
init_speakers_helpers(has_recording_access=has_recording_access, bcrypt=bcrypt, csrf=csrf, limiter=limiter)
init_inquire_helpers(has_recording_access=has_recording_access, bcrypt=bcrypt, csrf=csrf, limiter=limiter)
init_templates_helpers(has_recording_access=has_recording_access, bcrypt=bcrypt, csrf=csrf, limiter=limiter)
init_system_helpers(has_recording_access=has_recording_access, bcrypt=bcrypt, csrf=csrf, limiter=limiter, chunking_service=chunking_service)
init_api_v1_helpers(has_recording_access=has_recording_access, get_user_recording_status=get_user_recording_status, set_user_recording_status=set_user_recording_status, enrich_recording_dict_with_user_status=enrich_recording_dict_with_user_status, bcrypt=bcrypt, csrf=csrf, limiter=limiter, chunking_service=chunking_service)

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(tokens_bp)
app.register_blueprint(recordings_bp)
app.register_blueprint(tags_bp)
app.register_blueprint(folders_bp)
app.register_blueprint(groups_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(speakers_bp)
app.register_blueprint(inquire_bp)
app.register_blueprint(templates_bp)
app.register_blueprint(naming_templates_bp)
app.register_blueprint(export_templates_bp)
app.register_blueprint(initial_prompt_templates_bp)
app.register_blueprint(system_bp)
app.register_blueprint(push_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(api_v1_bp)
csrf.exempt(api_v1_bp)  # token clients only; browser sessions still need CSRF (#478)

# Recording sessions (#287 c/d): the streaming client POSTs binary chunk
# bodies via fetch with the session cookie. CSRF tokens are easy to attach
# from JS, so we keep the protection on by default — but each individual
# chunk request is a fetch call from our own SPA, which already carries
# the token, so no exemption is needed here.
app.register_blueprint(recording_sessions_bp)

# Webhooks (#275): under /api/v1, CSRF-exempt so API-token clients can
# manage their endpoints programmatically. SPA callers still attach the
# X-CSRFToken header for cookie-auth requests.
app.register_blueprint(webhooks_bp)
csrf.exempt(webhooks_bp)


def register_clinic_resources(flask_app):
    """Mount the clinic resource handlers (vocabulary, review, commands, parity).

    Called at startup only in clinic mode, so upstream URL rules are unchanged
    with it off. Every endpoint is classified in src/clinic/route_policy.py and
    the handlers keep their own CSRF and session checks behind the clinic guard.
    The parity dispatcher is mounted once; modules only register adapters.
    """
    from src.clinic import routes_asr_profiles, routes_commands, routes_vocabulary, routes_vocabulary_review
    from src.clinic.parity_dispatch import ParityDispatch
    from src.clinic.parity_receipts import run_once

    dispatch = ParityDispatch(run_once)
    routes_vocabulary_review.register_parity_review_handlers(dispatch)
    routes_commands.register_commands_adapters(dispatch)
    flask_app.register_blueprint(dispatch.blueprint)
    flask_app.register_blueprint(routes_vocabulary.VocabularyApi(
        lambda user_id, pool_id: routes_vocabulary_review.can_review_pool(user_id, pool_id)).blueprint)
    flask_app.register_blueprint(routes_vocabulary_review.vocabulary_review_bp)
    flask_app.register_blueprint(routes_commands.commands_blueprint)
    flask_app.register_blueprint(routes_asr_profiles.create_asr_profiles_blueprint())
    flask_app.extensions['clinic_parity_dispatch'] = dispatch
    flask_app.extensions['clinic_run_once'] = run_once


def register_clinic_worklist(flask_app):
    """Mount the worklist, workflow, history, template, category, regex and column handlers.

    Called at startup only in clinic mode, after register_clinic_resources, and reuses its
    parity dispatcher and receipt runner. Object decisions go through pool_policy.decide.
    Stores are process-local; release stays fail-closed until a trusted-context source and
    the review-flag count (W11) exist, and the acceptance ledger starts as "not required, no approver".
    """
    from datetime import datetime, timezone

    from sqlalchemy import or_

    from src.clinic import (policy, pool_policy, routes_categories, routes_columns, routes_dictation_detail,
                            routes_history, routes_regex, routes_templates, routes_workflow, routes_worklist,
                            workflow, workflow_metadata, worklist_entries)
    from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger, PolicyError
    from src.clinic import finalize_gates
    from src.clinic.archive_policy import ArchiveBook, archive_available
    from src.clinic.metadata_changes import ChangeError
    from src.clinic.models import ClinicDictation
    from src.clinic.parity_dispatch import CommandProblem
    from src.clinic.worklist_filters import WorklistFilterStore
    from src.clinic.worklist_layout import LayoutStore
    from src.models import Recording
    from src.services import audit

    dispatch = flask_app.extensions['clinic_parity_dispatch']
    run_once = flask_app.extensions['clinic_run_once']
    # Permission or action name -> central policy actions that must all be allowed.
    needs = {
        'clinic.metadata': ('edit',), 'clinic.archive': ('archive',), 'clinic.delete': ('edit', 'delete'),
        'clinic.release': ('release',),
        'clinic.review': ('edit', 'release'), 'clinic.accept': ('release',),
        'clinic.reopen': ('release',), 'clinic.dictate': ('edit',), 'history': ('view',),
        'review': ('edit',), 'listen': ('play',), 'regex_preview': ('edit',), 'regex_apply': ('edit',),
        'rename': ('edit',), 'priority': ('edit',), 'reassign': ('edit', 'release'),
    }

    def may(user_id, name, dictation):
        actions = needs.get(name)
        if not actions or dictation is None:
            return False
        subject = policy.build_subject(user_id)
        return (all(pool_policy.decide(subject, action, dictation).allowed for action in actions)
                and (name != 'clinic.archive' or archive_available(dictation)))

    def allowed_actions(user_id, dictation):
        return {name for name in ('rename', 'priority', 'reassign') if may(user_id, name, dictation)}

    def record_audit(rows):
        changed = {}
        for row in rows:
            changed.setdefault((row['dictation_id'], row['actor_id']), []).append(row['field'])
        for (dictation_id, actor), fields in changed.items():
            dictation = db.session.get(ClinicDictation, dictation_id)
            audit.record_in_tx('recording.edited', recording_id=dictation.recording_id,
                               actor_kind='session', actor_user_id=actor, details={'fields': fields})

    ledger = AcceptanceLedger()

    def acceptance_policy(dictation):
        try:
            return ledger.current(SCOPE_DICTATION, dictation.id)
        except PolicyError:
            return ledger.publish(SCOPE_DICTATION, dictation.id, required=False,
                                  approver_user_ids=(), now=datetime.now(timezone.utc))

    def stamp(value):
        if value is None:
            return None
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    def load_entries(actor):
        return worklist_entries.load_entries(actor)

    def assign_regex(actor, dictation_id, fields):
        dictation = db.session.query(ClinicDictation).filter_by(id=dictation_id).with_for_update().first()
        try:
            applied = workflow_metadata.apply_metadata(
                dictation_id=dictation.id, workflow_id=dictation.id, actor_user_id=actor,
                expected_version=dictation.version_counter,
                lease_generation=routes_workflow._generation(dictation.id), requested=fields,
                allowed_actions=allowed_actions(actor, dictation), now=datetime.now(timezone.utc))
            record_audit(applied.audit_rows)
            db.session.commit()
        except ChangeError as error:
            db.session.rollback()
            code = str(error)
            raise CommandProblem('POLICY_DENIED' if code == 'forbidden'
                                 else 'REVISION_CONFLICT' if code == 'stale_version' or code.startswith('lease_')
                                 else 'INVALID_PAYLOAD') from error
        except BaseException:
            db.session.rollback()
            raise
        return {'dictation_id': dictation.id, 'version': applied.version}

    layouts, filters = LayoutStore(), WorklistFilterStore()

    def workflow_authorize(actor, permission, dictation):
        return may(actor, permission, dictation)

    def context_is_trusted(context):
        # Configured adapter: app.extensions['clinic_kis_adapter'] if set, MockKIS (synthetic)
        # on the staging profile, else DisabledAdapter (production keeps finalize closed).
        from src.clinic.kis import DisabledAdapter, MockKisAdapter
        adapter = flask_app.extensions.get('clinic_kis_adapter')
        if adapter is None:
            staging = os.environ.get('CLINIC_PROFILE', '').strip().lower() == 'staging'
            adapter = flask_app.extensions.setdefault(
                'clinic_kis_adapter', MockKisAdapter() if staging else DisabledAdapter())
        return finalize_gates.trusted_context(context, adapter)

    def acceptance_decision(dictation, audio_revision_id, context_revision_id):
        return routes_workflow.bound_decision(ledger, dictation, audio_revision_id, context_revision_id)

    routes_workflow.register_commands(dispatch, workflow_authorize, acceptance_policy, ledger)
    routes_worklist.register_save_filter(dispatch, filters.create)
    flask_app.register_blueprint(routes_worklist.create_blueprint(
        load_entries, filters, layouts, load_case_displays=worklist_entries.case_displays))
    flask_app.register_blueprint(routes_columns.create_columns_blueprint(
        layouts, lambda _actor: (routes_worklist._DEFAULT_LAYOUT, routes_worklist.LAYOUT_COLUMNS), run_once))
    flask_app.register_blueprint(routes_workflow.create_blueprint(
        authorize=workflow_authorize, run_once=run_once, allowed_actions=allowed_actions,
        record_audit=record_audit, trusted_context=context_is_trusted,
        open_review_flags=finalize_gates.open_review_flags, acceptance_policy=acceptance_policy,
        acceptance_decision=acceptance_decision, acceptance_ledger=ledger,
        job_type='clinic_finalize', book=ArchiveBook()))
    flask_app.register_blueprint(routes_history.create_blueprint(
        authorize=lambda user, action, dictation: may(user.id, action, dictation), run_once=run_once,
        dictation_snapshot=worklist_entries.dictation_snapshot))
    flask_app.register_blueprint(routes_dictation_detail.create_blueprint(
        authorize=lambda user, action, dictation: may(user.id, action, dictation),
        dictation_snapshot=worklist_entries.dictation_snapshot,
        acceptance_policy=lambda row: ledger.current(SCOPE_DICTATION, row.id)))
    flask_app.register_blueprint(routes_templates.blueprint)
    flask_app.register_blueprint(routes_categories.blueprint)
    flask_app.register_blueprint(routes_regex.RegexApi(
        lambda actor, action, dictation_id: may(actor, action, db.session.get(ClinicDictation, dictation_id)),
        assign_regex).blueprint)

    # --- v8-131: staging Fall-ID context ---
    from src.clinic import routes_context, routes_output

    flask_app.register_blueprint(routes_context.create_blueprint(
        authorize=workflow_authorize, adapter_for=lambda: routes_output.configured_adapter(flask_app)))

    # --- v8-74: acceptance policy publication (start) ---
    # An active site admin publishes the approvers (W12: full access, no extra step); every other user
    # is refused with an audited 403. Read needs the shared policy action 'view' or an active admin.
    from src.clinic import routes_acceptance_policy
    from src.clinic.admin_dictation_access import can_open_content
    from src.clinic.identity import ClinicAccountPolicy
    from src.models import User

    def policy_admin(user_id, dictation):
        user = db.session.get(User, user_id)
        return (user is not None
                and can_open_content(db.session, user, dictation.recording_id, 'audio').reason == 'admin_full')

    def policy_authorize(user_id, permission, dictation):
        if permission == routes_acceptance_policy.PERM_WRITE:
            return policy_admin(user_id, dictation)
        return policy_admin(user_id, dictation) or may(user_id, 'history', dictation)

    def is_acceptance_member(user_id):
        # An approver is an existing user with an active clinic account; what the approver may do
        # to one dictation is still decided by the central policy when the accept command runs.
        account = db.session.get(ClinicAccountPolicy, user_id)
        return db.session.get(User, user_id) is not None and account is not None and account.status == 'active'

    flask_app.register_blueprint(routes_acceptance_policy.create_blueprint(
        authorize=policy_authorize, ledger=ledger, default_required=lambda _dictation: True,
        is_acceptance_member=is_acceptance_member, record_audit=record_audit))
    # --- v8-74: acceptance policy publication (end) ---

    # --- v8-132: assignment (start) ---
    from src.clinic import routes_assignment
    flask_app.register_blueprint(routes_assignment.create_blueprint(run_once))
    # --- v8-132: assignment (end) ---


def register_clinic_identity(flask_app):
    """Mount the identity, directory, pool, grant and preference handlers.

    Called at startup only in clinic mode, right after register_clinic_resources. None of
    these modules registers a parity command, so the stored ParityDispatch is only checked
    to be present (one dispatcher, mounted once). Identity providers stay disabled (default
    EntraConfig, IWA off): their routes answer the inert 422 and the exchange never runs.
    The directory has no provider, so it answers its capability-unavailable problem.
    Preference stores are process-local; a durable store is a later package.
    """
    from src.clinic import (app_wiring, entra, pool_policy, routes_admin_dictations, routes_directory, routes_grants,
                            routes_identity_providers, routes_pool_admin, routes_pools, routes_preferences)
    from src.clinic.last_selection import Allowed, SelectionStore
    from src.clinic.routes_metadata_fields import create_metadata_fields_blueprint
    from src.clinic.workstation_preferences import WorkstationPreferences

    assert 'clinic_parity_dispatch' in flask_app.extensions, 'register_clinic_resources must run first'

    def disabled_exchange(_pending, _code):
        raise entra.EntraDisabled()

    def author_pool_for(user_id):
        # One author pool per user is the clinic rule; a user in none has no author recipient default.
        # The model has no active-pool flag: first pool by name, then id (app_wiring.choose_author_pool).
        return app_wiring.choose_author_pool(user_id)

    def allowed_for(user_id):
        pool_ids = pool_policy.author_pool_ids(user_id)
        return Allowed(roles=frozenset({'author'}) if pool_ids else frozenset(),
                       author_ids=frozenset({user_id}) if pool_ids else frozenset(),
                       pool_ids=pool_ids, own_author_id=user_id if pool_ids else None)

    flask_app.register_blueprint(
        routes_identity_providers.IdentityProvidersApi(entra.EntraConfig(), disabled_exchange).blueprint)
    flask_app.register_blueprint(routes_directory.blueprint)
    flask_app.register_blueprint(routes_admin_dictations.blueprint)
    flask_app.register_blueprint(routes_pools.blueprint)
    flask_app.register_blueprint(routes_pool_admin.blueprint)
    from src.clinic import routes_task_models
    flask_app.register_blueprint(routes_task_models.blueprint)
    flask_app.register_blueprint(routes_grants.GrantsApi().blueprint)
    flask_app.register_blueprint(create_metadata_fields_blueprint())
    flask_app.register_blueprint(routes_preferences.PreferenceRoutes(
        allowed_for=allowed_for, author_pool_for=author_pool_for,
        selections=SelectionStore(), workstations=WorkstationPreferences()).blueprint)


def register_clinic_audio(flask_app):
    """Mount the audio, dictation-property, bundle, live, correction and TTS handlers.

    Called at startup only in clinic mode, after register_clinic_worklist, and reuses the one
    ParityDispatch (start/stop live session are registered on it). Object decisions go through
    pool_policy.decide; an active site admin may open audio of any user (W12, audited by the
    audio handler). Capabilities that do not exist yet answer "unavailable" and are never faked:
    no bundle persistence (503), no local TTS engine or declared capability
    (CAPABILITY_UNAVAILABLE), no live-draft coordinator (CAPABILITY_UNAVAILABLE). The derived
    rendering reads the stored originals (app_wiring.make_source_loader); the protection store is
    durable (clinic_dictation_protection). The mounted sdict export uses that same current
    revision. A source that cannot be read stays empty and is never replaced by synthetic audio.
    """
    from src.clinic import (app_wiring, audio_render, audio_revisions, config, local_tts, policy, pool_policy,
                            revisions, routes_audio, routes_bundle, routes_clipboard, routes_correction,
                            routes_dictation_properties, routes_live, routes_output, routes_tts, startup)
    from src.clinic.admin_dictation_access import can_open_content
    from src.clinic.ingest import IngestService
    from src.clinic.models import ClinicDictation, ClinicTranscriptRevision

    dispatch = flask_app.extensions['clinic_parity_dispatch']
    run_once = flask_app.extensions['clinic_run_once']
    # Handler action name -> central policy actions that must all be allowed.
    needs = {
        'play': ('play',), 'edit': ('edit',), 'export': ('export',), 'import': ('edit',),
        'correction_read': ('view',), 'correction_edit': ('edit',), 'clipboard': ('export',),
    }

    def may(user_id, name, dictation):
        actions = needs.get(name)
        if not actions or dictation is None:
            return False
        subject = policy.build_subject(user_id)
        return all(pool_policy.decide(subject, action, dictation).allowed for action in actions)

    def authorize(user, name, dictation):
        # W12: an active admin opens audio of any user; the handler writes the audit row.
        if name == 'play' and can_open_content(db.session, user, dictation.recording_id, 'audio').reason == 'admin_full':
            return True
        return may(user.id, name, dictation)

    def text_version(dictation):
        return db.session.query(ClinicTranscriptRevision).filter_by(dictation_id=dictation.id).count()

    load_sources = app_wiring.make_source_loader(startup.originals_root(flask_app))
    protection_store = app_wiring.DurableProtectionStore(
        lambda user_id, dictation_id: may(user_id, 'play', db.session.get(ClinicDictation, dictation_id)),
        lambda user_id, dictation_id: may(user_id, 'edit', db.session.get(ClinicDictation, dictation_id)),
        lambda dictation_id: db.session.get(ClinicDictation, dictation_id) is not None)

    def bundle_audio(dictation):
        # Missing source stays empty so the archive builder refuses it. Never invent audio.
        revision = audio_revisions.current_audio_revision(dictation)
        if revision is None:
            return b''
        try:
            edl = audio_revisions.get_revision_edl(dictation, revision)
            return audio_render.render_current_revision(
                dictation, revision.id, load_sources(dictation, edl)).wav
        except audio_revisions.AudioRevisionError:
            return b''

    def bundle_load(actor, dictation_id):
        dictation = db.session.get(ClinicDictation, dictation_id)
        head = None if dictation is None else revisions.current_revision(dictation)
        if head is None:
            return None
        return routes_bundle.BundleSession(
            doc=routes_bundle.text_to_document(head.body.encode('utf-8')),
            audio=bundle_audio(dictation), version=text_version(dictation))

    def bundle_persist(_actor, _dictation_id, _doc, _audio, _expected):
        raise OSError('no durable bundle store is wired yet')

    def managed_client():
        return config.client_capabilities()['profiles'][config.client_profile()].get('document_export') is not False

    def tts_load(actor, kind, object_id):
        dictation = db.session.get(ClinicDictation, object_id) if kind == routes_tts.SELECTION_KIND else None
        head = None if dictation is None else revisions.current_revision(dictation)
        if head is None or not may(actor, 'play', dictation):
            return None
        return routes_tts.Loaded(
            document=local_tts.CanonicalText(text=head.body, version=text_version(dictation)),
            subject=policy.build_subject(actor),
            recording=policy.build_facts(dictation.recording_id, dictation.author_user_id))

    routes_live.register_live_commands(
        dispatch, lambda actor, dictation_id: may(actor, 'edit', db.session.get(ClinicDictation, dictation_id)))
    flask_app.register_blueprint(routes_audio.create_blueprint(
        authorize=authorize, run_once=run_once, load_sources=load_sources))
    flask_app.register_blueprint(routes_dictation_properties.DictationPropertyRoutes(protection_store).blueprint())
    flask_app.register_blueprint(routes_bundle.create_blueprint(
        bundle_load, lambda actor, dictation_id, action: may(actor, action, db.session.get(ClinicDictation, dictation_id)),
        bundle_persist, run_once, managed_client))
    flask_app.register_blueprint(routes_live.create_blueprint(
        lambda actor, dictation_id: may(actor, 'edit', db.session.get(ClinicDictation, dictation_id)),
        IngestService(startup.originals_root(flask_app))))
    flask_app.register_blueprint(routes_correction.create_blueprint(
        authorize=lambda user, name, dictation: may(user.id, name, dictation),
        run_once=run_once, require_if_match=True))
    # V8 alignment (v8-86)
    from src.clinic import alignment_job, routes_alignment
    from src.services.job_queue import register_clinic_handler
    flask_app.register_blueprint(routes_alignment.create_blueprint(
        authorize=lambda user, name, dictation: may(user.id, name, dictation), run_once=run_once))
    register_clinic_handler('clinic_alignment', alignment_job.handle)
    flask_app.register_blueprint(routes_tts.TtsApi(tts_load, lambda: None, None).blueprint)
    flask_app.register_blueprint(routes_clipboard.create_blueprint(
        authorize=lambda user, name, dictation: may(user.id, name, dictation),
        source_for=routes_output.output_source, ledger={}))
    flask_app.register_blueprint(routes_output.create_blueprint(
        authorize=lambda user, name, dictation: may(user.id, name, dictation),
        adapter_for=lambda: routes_output.configured_adapter(flask_app)))


def register_clinic_operations(flask_app):
    """Mount the recordings list, processing overview, statistics, ASR training and import-agent handlers.

    Called at startup only in clinic mode, after register_clinic_audio, and reuses the one
    receipt runner. The import agent authenticates with a machine credential (proxy secret
    plus certificate fingerprint) and never with a session, so its blueprint is CSRF-exempt.
    CLINIC_AGENT_PROXY_SECRET and CLINIC_AGENT_MASTER_SECRET need at least 32 bytes each.
    The machine channel uses random per-process values for missing or invalid secrets.
    Browser enrollment and rotation require the configured master secret.
    """
    import secrets

    from src.clinic import (
        routes_agent, routes_agent_admin, routes_jobs, routes_recordings, routes_statistics, routes_training, startup,
    )
    from src.clinic.agent_channel import ChannelPolicy
    from src.clinic.ingest import IngestService
    from src.clinic.worklist_entries import load_entries

    def secret_from_env(name):
        value = os.environ.get(name, '').strip().encode('utf-8')
        if len(value) < routes_agent.MIN_PROXY_SECRET_BYTES:
            flask_app.logger.warning('Clinic import agent: %s is not set or shorter than %d bytes',
                                     name, routes_agent.MIN_PROXY_SECRET_BYTES)
            return b''
        return value

    run_once = flask_app.extensions['clinic_run_once']
    master_secret = secret_from_env('CLINIC_AGENT_MASTER_SECRET')
    proxy_secret = secret_from_env('CLINIC_AGENT_PROXY_SECRET')
    agent_bp = routes_agent.create_agent_blueprint(
        ChannelPolicy(master_secret=master_secret or secrets.token_bytes(routes_agent.MIN_PROXY_SECRET_BYTES)),
        IngestService(startup.originals_root(flask_app)),
        proxy_secret or secrets.token_bytes(routes_agent.MIN_PROXY_SECRET_BYTES))
    flask_app.register_blueprint(routes_recordings.create_blueprint(load_entries))
    flask_app.register_blueprint(routes_jobs.create_blueprint(run_once=run_once))
    flask_app.register_blueprint(routes_statistics.blueprint)
    flask_app.register_blueprint(routes_training.blueprint)
    flask_app.register_blueprint(agent_bp)
    csrf.exempt(agent_bp)
    flask_app.register_blueprint(routes_agent_admin.create_blueprint(master_secret))

    # --- v8-120: browser import reads ---
    from src.clinic import routes_imports_web
    flask_app.register_blueprint(routes_imports_web.blueprint)
    # --- end v8-120 ---


# --- v8-75: voice consent and voice enrollment routes ---
def register_clinic_voice_consent(flask_app):
    """Mount the own-user voice consent and voice enrollment blueprints.

    Called at startup only in clinic mode. Voice profiles are opt-in: enrollment is refused
    without active consent and again after revocation (enforced in voice_consent).
    Both modules authenticate the browser session and check CSRF themselves.
    """
    from src.clinic import routes_consent, routes_voice_enrollment
    flask_app.register_blueprint(routes_consent.blueprint)
    flask_app.register_blueprint(routes_voice_enrollment.blueprint)
# --- end v8-75 ---


if _clinic_mode_enabled():
    register_clinic_resources(app)
    register_clinic_identity(app)
    register_clinic_worklist(app)
    register_clinic_audio(app)
    register_clinic_operations(app)
    # --- v8-90: clinic job handlers (start) ---
    from src.clinic.job_handlers import register_clinic_job_handlers
    register_clinic_job_handlers(app)
    # --- end v8-90 ---
    register_clinic_voice_consent(app)  # v8-75
    # --- v8-152: managed offline capability ---
    from src.clinic import routes_offline
    app.register_blueprint(routes_offline.blueprint)
    # --- end v8-152 ---

# PWA Web Share Target (issue #285): the native share sheet cannot round-trip
# a CSRF token, so the share-target endpoint is exempted. Authentication still
# happens via the session cookie carried by the browser; @login_required
# bounces unauthenticated visitors to /login.
from src.api.recordings import share_target as _share_target_view
csrf.exempt(_share_target_view)

# File monitor and scheduler initialization functions below

# Startup functions (extracted to src/config/startup.py)
from src.config.startup import initialize_file_monitor, get_file_monitor_functions, initialize_auto_deletion_scheduler, run_startup_tasks

# Run startup tasks
run_startup_tasks(app)

# Session idle timeout, and warnings about cookies and plain-http services.
from src.services import session_policy as _session_policy
_session_policy.init_app(app)
# init_app puts its idle hook at index 0: put the clinic guard back in front.
from src.clinic.guard import ensure_guard_first as _ensure_guard_first
_ensure_guard_first(app)
from src.config.outbound_warnings import log_plain_http_warnings as _log_plain_http_warnings
_log_plain_http_warnings(app)

# --- No-Crawl System: HTTP Headers ---
@app.after_request
def add_no_crawl_headers(response):
    """
    Add HTTP headers to discourage search engine crawling and indexing.
    This provides defense-in-depth alongside robots.txt and meta tags.
    """
    response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive, nosnippet, noimageindex'
    return response


@app.after_request
def add_query_token_deprecation_header(response):
    """Mark responses to requests that sent an API token as ?token= (#500)."""
    from src.utils.token_auth import query_token_used
    if query_token_used():
        response.headers['Deprecation'] = 'true'
    return response


@app.after_request
def add_security_headers(response):
    """Set baseline security headers so the app is safe even without a
    hardening reverse proxy. Uses setdefault so a proxy that already sets a
    header wins (no duplicates). See the SECURITY_HEADERS config block above.
    """
    if not SECURITY_HEADERS_ENABLED:
        return response
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    # Deny sensors the app never uses; allow mic + screen capture (recording).
    response.headers.setdefault(
        'Permissions-Policy',
        'geolocation=(), camera=(), microphone=(self), display-capture=(self), interest-cohort=()'
    )
    if CONTENT_SECURITY_POLICY:
        response.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
    # HSTS only over HTTPS (request.is_secure reflects X-Forwarded-Proto via ProxyFix).
    if HSTS_HEADER_VALUE and request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', HSTS_HEADER_VALUE)
    return response

# --- No-Crawl System: Serve robots.txt ---
@app.route('/robots.txt')
def robots_txt():
    """Serve robots.txt to instruct crawlers not to index the site."""
    return send_file(os.path.join(app.static_folder, 'robots.txt'), mimetype='text/plain')

# --- Service worker -----------------------------------------------------------
@app.route('/sw.js')
def service_worker():
    """Serve static/sw.js from the root, so that its scope is the whole app.

    A worker's widest scope is the directory of its URL: served from
    /static/sw.js it controlled no page. No Service-Worker-Allowed header is
    needed for a file at the root. The browser's update check must see a new
    release at once, so the file is revalidated on every request.
    """
    response = send_file(os.path.join(app.static_folder, 'sw.js'), mimetype='text/javascript', max_age=0)
    response.headers['Cache-Control'] = 'no-cache'
    return response


def main(debug=False):
    """The development server, for python src/app.py (see the top of this file).

    Production runs gunicorn with src.app:app.
    """
    app.run(host='0.0.0.0', port=8899, debug=debug)

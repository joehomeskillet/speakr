"""Read-only KIS handoff. Only an opaque handle enters the browser session."""

import hashlib
import logging
import re
from secrets import token_urlsafe
from time import time
from urllib.parse import unquote_plus

from flask import Blueprint, after_this_request, current_app, make_response, redirect, request, session, url_for
from flask_login import current_user
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.database import db
from src.services import audit

from .context_grants import CASE_REFERENCE
from .kis import MockKisAdapter, _profile

SESSION_KEY = 'clinic_case_view'
PENDING_COOKIE = 'clinic_case_pending'
VIEW_HEADER = 'X-Clinic-Case-View'
_TTL = 15 * 60
_SIGNATURE_AGE = 60
_SENSITIVE_QUERY = re.compile(r'(?:^|[?&\s])(?:fall_id|case_signature)(?=[=&\s]|$)')


class ClinicCaseSession(db.Model):
    __tablename__ = 'clinic_case_session'

    handle_digest = db.Column(db.String(64), primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=True)
    case_ref = db.Column(db.String(12), nullable=True)
    signature_digest = db.Column(db.String(64), unique=True, nullable=True)
    expires_at = db.Column(db.BigInteger, nullable=False, index=True)


def create_blueprint(case_view) -> Blueprint:
    blueprint = Blueprint('clinic_case', __name__)
    blueprint.add_url_rule('/fall', 'view', case_view, defaults={'case_view': True})
    return blueprint


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _staging_mock():
    if _profile() == 'staging':
        from .routes_output import configured_adapter

        adapter = configured_adapter(current_app)
        if isinstance(adapter, MockKisAdapter) and adapter.enabled:
            return adapter
    return None


def _staging_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='speakr-mock-kis-case-view-v1')


def _verified_case(values, signatures):
    if len(values) != 1 or not CASE_REFERENCE.fullmatch(values[0]) or not _staging_mock():
        return None, None
    case_ref = values[0]
    if not signatures:
        # MockKIS's synthetic launcher also accepts the staging QA URL directly.
        return case_ref, None
    if len(signatures) != 1 or len(signatures[0]) > 512:
        return None, None
    try:
        claims = _staging_signer().loads(signatures[0], max_age=_SIGNATURE_AGE)
    except BadData:
        return None, None
    if not (isinstance(claims, list) and len(claims) == 2 and claims[0] == case_ref
            and isinstance(claims[1], str) and 16 <= len(claims[1]) <= 64):
        return None, None
    return case_ref, _digest(claims[1])


def is_handoff_request():
    return (request.endpoint == 'recordings.index' and not request.view_args
            and request.method in ('GET', 'HEAD') and 'fall_id' in request.args)


def private_response(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def capture():
    if not is_handoff_request():
        return None
    if request.method == 'HEAD':
        return private_response(redirect(url_for('clinic_case.view'), 303))

    actor = current_user.id if (current_user.is_authenticated and current_user.is_active
                                and session.get('_user_id') == current_user.get_id()) else None
    case_ref, signature = _verified_case(request.args.getlist('fall_id'),
                                          request.args.getlist('case_signature'))
    now, handle = int(time()), token_urlsafe(32)
    pending = ClinicCaseSession(handle_digest=_digest(handle), actor_id=actor,
                                expires_at=now + _TTL, case_ref=case_ref, signature_digest=signature)
    try:
        db.session.execute(delete(ClinicCaseSession).where(ClinicCaseSession.expires_at <= now))
        if signature:
            try:
                with db.session.begin_nested():
                    db.session.add(pending)
                    db.session.flush()
            except IntegrityError:
                if not db.session.query(ClinicCaseSession).filter_by(signature_digest=signature).first():
                    raise
                pending.case_ref = pending.signature_digest = None
        db.session.add(pending)
        event = audit.record_in_tx('clinic.kis.case_handoff', force=True,
                                   actor_kind='session' if actor is not None else 'anonymous',
                                   actor_user_id=actor, outcome='success' if pending.case_ref else 'denied')
        if event is None:
            db.session.rollback()
            return private_response(make_response('', 503))
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error('KIS case handoff transaction failed (%s)', type(error).__name__)
        return private_response(make_response('', 503))

    target = url_for('clinic_case.view')
    if actor is not None:
        session[SESSION_KEY] = handle
        response = redirect(target, 303)
        response.delete_cookie(PENDING_COOKIE, path=target)
    else:
        response = redirect(url_for('auth.login', next=target), 303)
        response.set_cookie(PENDING_COOKIE, handle, path=target, max_age=_TTL, httponly=True,
                            samesite='Lax', secure=request.is_secure or current_app.config['SESSION_COOKIE_SECURE'])
    return private_response(response)


def view_marker():
    """Claim an anonymous handoff once, then bind it to this browser login."""
    pending = request.cookies.get(PENDING_COOKIE)
    if pending and len(pending) == 43 and session.get('_user_id') == current_user.get_id():
        claimed = db.session.execute(update(ClinicCaseSession).where(
            ClinicCaseSession.handle_digest == _digest(pending), ClinicCaseSession.actor_id.is_(None),
            ClinicCaseSession.expires_at > int(time()),
        ).values(actor_id=current_user.id))
        db.session.commit()
        if claimed.rowcount == 1:
            session[SESSION_KEY] = pending

        @after_this_request
        def clear_pending(response):
            response.delete_cookie(PENDING_COOKIE, path=url_for('clinic_case.view'))
            return response

    handle = session.get(SESSION_KEY)
    return _digest(handle) if handle else 'empty'


def case_reference(marker):
    """A stale tab or missing context yields the same empty result as an unknown case."""
    handle = session.get(SESSION_KEY)
    if not handle or _digest(handle) != marker:
        return ''
    row = db.session.get(ClinicCaseSession, marker)
    if row is None or row.actor_id != current_user.id or row.expires_at <= int(time()):
        return ''
    return row.case_ref or ''


class CaseAccessLogFilter(logging.Filter):
    def filter(self, record):
        if _SENSITIVE_QUERY.search(unquote_plus(record.getMessage())):
            record.msg, record.args = 'KIS handoff request (query redacted)', ()
        return True

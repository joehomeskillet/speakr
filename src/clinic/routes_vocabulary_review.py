"""Expose pending and session vocabulary review (PAR-VOC-004, PAR-VOC-005).

Provides Parity command adapters for 'review_resource_change' and Blueprint
routes for vocabulary review queue inspection and undo.
"""

from __future__ import annotations

import uuid
from hashlib import sha256
from typing import Any

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy.sql.elements import ColumnElement
from wtforms.validators import ValidationError

from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch, _problem
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember, ClinicVisibilityGrant
from src.clinic.routes_vocabulary import ApiError, canonical_selection
from src.clinic import vocabulary
from src.database import db
from src.models import User

PERMISSION_REVIEW = "clinic.vocabulary.review"

vocabulary_review_bp = Blueprint("clinic_vocabulary_review", __name__)


def get_review_item_revision(proposal_id: str, session: Any = None) -> int | None:
    """Return positive integer revision for an existing review item, or None."""
    s = session if session is not None else db.session
    item = s.get(vocabulary.ClinicVocabularyReviewItem, proposal_id)
    if item is None:
        return None
    decisions_count = (
        s.query(vocabulary.ClinicVocabularyReviewDecision)
        .filter_by(review_item_id=proposal_id)
        .count()
    )
    return decisions_count + 1


def can_review_pool(actor_id: int, pool_id: str, session: Any = None) -> bool:
    """True if actor has reviewer or management authority over the pool."""
    s = session if session is not None else db.session
    uid = int(actor_id)
    user = s.get(User, uid)
    if not user:
        return False
    account = s.get(ClinicAccountPolicy, uid)
    if account is not None and account.status != "active":
        return False
    if bool(getattr(user, "is_admin", False)):
        return True
    pool = s.get(ClinicAuthorPool, pool_id)
    if pool is not None and pool.created_by == uid:
        return True
    grant = (
        s.query(ClinicVisibilityGrant)
        .filter(
            ClinicVisibilityGrant.user_id == uid,
            ClinicVisibilityGrant.effect == "allow",
            ClinicVisibilityGrant.scope.in_(("pool", "all")),
            (ClinicVisibilityGrant.pool_id == pool_id) | (ClinicVisibilityGrant.pool_id.is_(None)),
        )
        .first()
    )
    if grant is not None:
        if not grant.actions or any(a in grant.actions for a in ("release", "edit")):
            return True
    return False


def can_review_user(actor_id: int, target_user_id: int, session: Any = None) -> bool:
    """True if actor can review user-scoped items for target_user_id."""
    if int(actor_id) == int(target_user_id):
        return True
    s = session if session is not None else db.session
    uid = int(actor_id)
    user = s.get(User, uid)
    if not user:
        return False
    account = s.get(ClinicAccountPolicy, uid)
    if account is not None and account.status != "active":
        return False
    return bool(getattr(user, "is_admin", False))


def _pool_visible() -> ColumnElement[bool]:
    item = vocabulary.ClinicVocabularyReviewItem
    decision = vocabulary.ClinicVocabularyReviewDecision
    # A personal acceptance stays private when undone and the proposal reopens.
    accepted_personally = db.session.query(decision.id).filter(
        decision.review_item_id == item.id, decision.action == vocabulary.ACTION_ACCEPT_USER,
    ).exists()
    published = (item.status == vocabulary.REVIEW_STATUS_ACCEPTED_POOL) & (item.pool_id == item.target_pool_id)
    submitted = (
        (item.origin != vocabulary.ORIGIN_SESSION)
        & item.status.notin_((vocabulary.REVIEW_STATUS_ACCEPTED_USER, vocabulary.REVIEW_STATUS_ACCEPTED_POOL))
        & ~accepted_personally
    )
    return published | submitted


def _can_review_item(actor_id: int, item: vocabulary.ClinicVocabularyReviewItem, *, undo: bool = False) -> bool:
    account = db.session.get(ClinicAccountPolicy, actor_id)
    if db.session.get(User, actor_id) is None or (account is not None and account.status != "active"):
        return False
    if actor_id == item.user_id:
        return True
    # Keep existing site-admin command access for non-session proposals, but
    # do not let pool review permissions undo a now-private acceptance.
    if (
        item.origin != vocabulary.ORIGIN_SESSION
        and (not undo or not item.pool_id)
        and can_review_user(actor_id, item.user_id)
    ):
        return True
    return bool(
        item.pool_id and can_review_pool(actor_id, item.pool_id)
        and db.session.query(vocabulary.ClinicVocabularyReviewItem.id)
        .filter_by(id=item.id).filter(_pool_visible()).first()
    )


def _accept_pool_target(actor_id: int, item: vocabulary.ClinicVocabularyReviewItem) -> ClinicAuthorPool | None:
    if item.pool_id and can_review_pool(actor_id, item.pool_id) and _can_review_item(actor_id, item):
        return db.session.get(ClinicAuthorPool, item.pool_id)
    return None


def authorize_review_resource_change(actor_id: int, permission: str, payload: dict[str, Any]) -> str | None:
    """Validate review command actor permissions, target and transition."""
    if permission != PERMISSION_REVIEW:
        return None
    proposal_id = payload.get("proposal_id")
    decision = payload.get("decision")
    if not proposal_id or decision not in ("user", "pool", "reject", "defer"):
        return None

    s = db.session
    # Serialize decisions before the parity dispatcher checks the receipt and revision.
    item = s.get(vocabulary.ClinicVocabularyReviewItem, proposal_id, populate_existing=True, with_for_update=True)
    if item is None:
        return None

    uid = int(actor_id)
    if decision == "pool":
        return item.id if _accept_pool_target(uid, item) is not None else None
    return item.id if _can_review_item(uid, item) else None


def execute_review_resource_change(actor_id: int, target: str, envelope: dict[str, Any]) -> dict[str, Any]:
    """Execute review transition and record append-only decision."""
    proposal_id = envelope["payload"]["proposal_id"]
    decision = envelope["payload"]["decision"]

    try:
        if decision == "user":
            vocabulary.accept_to_user(proposal_id, actor_id=actor_id)
        elif decision == "pool":
            vocabulary.accept_to_pool(proposal_id, actor_id=actor_id)
        elif decision == "reject":
            vocabulary.reject_item(proposal_id, actor_id=actor_id, reason="review_rejected")
        elif decision == "defer":
            vocabulary.defer_item(proposal_id, actor_id=actor_id, notes="review_deferred")
        else:
            raise CommandProblem("INVALID_PAYLOAD")
    except vocabulary.VocabularyError:
        raise CommandProblem("POLICY_DENIED")

    new_rev = get_review_item_revision(proposal_id)
    if new_rev is None:
        raise RuntimeError("Review item missing after execution")

    return {
        "request_id": envelope["request_id"],
        "command_id": str(uuid.uuid4()),
        "status": "committed",
        "revision": new_rev,
    }


def authorize_propose_vocabulary_change(actor_id: int, permission: str, payload: dict[str, Any]) -> str | None:
    """Allow personal proposals and proposals for a current author-pool member."""
    if permission != "clinic.vocabulary.propose":
        return None
    s = db.session
    uid = int(actor_id)
    if s.get(User, uid) is None:
        return None
    account = s.get(ClinicAccountPolicy, uid)
    if account is not None and account.status != "active":
        return None
    scope, scope_id = payload["scope_type"], payload["scope_id"]
    if scope == "user":
        return f"user:{uid}" if scope_id == str(uid) else None
    if scope == "author_pool" and s.get(ClinicAuthorPool, scope_id) is not None:
        member = s.query(ClinicAuthorPoolMember).filter_by(pool_id=scope_id, user_id=uid).first()
        if member is not None:
            return f"author_pool:{scope_id}"
    return None


def execute_propose_vocabulary_change(actor_id: int, target: str, envelope: dict[str, Any]) -> dict[str, Any]:
    """Create a pending review item; only a later review changes vocabulary."""
    payload = envelope["payload"]
    operation = vocabulary.REVIEW_OP_DELETE if payload["operation"] == "negative" else vocabulary.REVIEW_OP_ADD
    try:
        item = vocabulary.submit_review_item(
            user_id=actor_id, written_form=canonical_selection(payload["written"]),
            spoken_form=canonical_selection(payload["spoken"]) if payload.get("spoken") else None,
            operation=operation, origin=vocabulary.ORIGIN_MANUAL,
            pool_id=payload["scope_id"] if payload["scope_type"] == "author_pool" else None,
        )
    except (ApiError, vocabulary.VocabularyError):
        raise CommandProblem("INVALID_PAYLOAD")
    return {
        "request_id": envelope["request_id"],
        "command_id": str(uuid.uuid4()),
        "artifact_id": item.id,
        "status": "committed",
        "revision": 1,
    }


def register_parity_review_handlers(dispatch: ParityDispatch) -> None:
    """Register parity command adapter on ParityDispatch."""
    dispatch.register(
        "review_resource_change",
        authorize=authorize_review_resource_change,
        revision=get_review_item_revision,
        execute=execute_review_resource_change,
    )
    dispatch.register(
        "propose_vocabulary_change",
        authorize=authorize_propose_vocabulary_change,
        revision=lambda _target: None,
        execute=execute_propose_vocabulary_change,
    )


def undo_review_decision(actor_id: int, proposal_id: str, expected_revision: int | None = None) -> dict[str, Any]:
    """Undo review decision with revision checking and policy guard."""
    s = db.session
    item = s.get(vocabulary.ClinicVocabularyReviewItem, proposal_id, populate_existing=True, with_for_update=True)
    if item is None or not _can_review_item(int(actor_id), item, undo=True):
        raise CommandProblem("POLICY_DENIED")

    current_rev = get_review_item_revision(proposal_id, session=s)
    if expected_revision is not None and current_rev != expected_revision:
        raise CommandProblem("REVISION_CONFLICT")

    try:
        undone = vocabulary.undo_decision(proposal_id, actor_id=actor_id, session=s)
        s.commit()
    except vocabulary.VocabularyError:
        s.rollback()
        raise CommandProblem("POLICY_DENIED")

    new_rev = get_review_item_revision(proposal_id, session=s)
    return {"id": undone.id, "status": undone.status, "revision": new_rev}


@vocabulary_review_bp.get("/resources/vocabulary/reviews")
@vocabulary_review_bp.get("/api/clinic/v1/resources/vocabulary/reviews")
def list_resource_vocabulary_reviews():
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active):
        return _problem("UNAUTHORIZED")

    if request.args.get("catalog") == "author_pools":
        pools = db.session.query(ClinicAuthorPool.id, ClinicAuthorPool.name).order_by(ClinicAuthorPool.name)
        return jsonify(author_pools=[
            {"id": pool.id, "name": pool.name}
            for pool in pools if can_review_pool(current_user.id, pool.id)
        ])

    scope_type = request.args.get("scope_type")
    scope_id = request.args.get("scope_id")
    status = request.args.get("status", vocabulary.REVIEW_STATUS_PENDING)
    statuses = vocabulary.OPEN_REVIEW_STATUSES if status == "OPEN" else tuple(
        part.strip() for part in status.split(",")
    )

    if scope_type not in ("user", "author_pool") or not scope_id:
        return _problem("INVALID_PAYLOAD")
    if not statuses or any(value not in vocabulary.REVIEW_STATUSES for value in statuses):
        return _problem("INVALID_PAYLOAD")
    actor_id = current_user.id
    model = vocabulary.ClinicVocabularyReviewItem
    query = db.session.query(model).filter(model.status.in_(statuses))
    if scope_type == "user":
        try:
            target_user_id = int(scope_id)
        except ValueError:
            return _problem("INVALID_PAYLOAD")
        if not can_review_user(actor_id, target_user_id):
            return _problem("POLICY_DENIED")
        query = query.filter_by(user_id=target_user_id)
        if target_user_id != actor_id:
            query = query.filter(
                model.origin != vocabulary.ORIGIN_SESSION, model.status != vocabulary.REVIEW_STATUS_ACCEPTED_USER,
            )
    else:
        pool_id = scope_id
        user = db.session.get(User, actor_id)
        is_admin = bool(user and getattr(user, "is_admin", False))
        is_member = (
            db.session.query(ClinicAuthorPoolMember)
            .filter_by(pool_id=pool_id, user_id=actor_id)
            .first()
            is not None
        )
        if not (is_admin or is_member or can_review_pool(actor_id, pool_id)):
            return _problem("POLICY_DENIED")
        query = query.filter_by(pool_id=pool_id).filter(_pool_visible())

    rows = query.order_by(model.created_at.desc(), model.id).all()

    try:
        limit = min(100, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50

    cursor = request.args.get("cursor")
    start = 0
    if cursor:
        try:
            start = int(cursor)
        except ValueError:
            return _problem("INVALID_PAYLOAD")
    if start < 0 or (start > 0 and start >= len(rows)):
        return _problem("REVISION_CONFLICT")

    page_rows = rows[start : start + limit]
    next_cursor = str(start + limit) if (start + limit) < len(rows) else None

    items = []
    for r in page_rows:
        pool = _accept_pool_target(actor_id, r) if r.status in vocabulary.OPEN_REVIEW_STATUSES else None
        items.append({
            "id": r.id,
            "written_form": r.written_form,
            "spoken_form": r.spoken_form,
            "operation": r.operation,
            "status": r.status,
            "revision": get_review_item_revision(r.id) or 1,
            "resource_type": vocabulary._session_information_data(r).get(
                vocabulary._SESSION_RESOURCE, "vocabulary",
            ),
            "can_accept_pool": pool is not None,
            "target_pool_name": pool.name if pool is not None else None,
        })

    rev = max((item["revision"] for item in items), default=0)
    resp = jsonify({"revision": rev, "next_cursor": next_cursor, "items": items})
    etag = sha256(f"{actor_id}:{request.full_path}:".encode() + resp.get_data()).hexdigest()
    if request.if_none_match.contains_weak(etag):
        resp.status_code = 304
        resp.set_data(b"")
    resp.set_etag(etag)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.vary.add("Cookie")
    return resp


@vocabulary_review_bp.post("/resources/vocabulary/reviews/<proposal_id>/undo")
@vocabulary_review_bp.post("/api/clinic/v1/resources/vocabulary/reviews/<proposal_id>/undo")
def undo_resource_vocabulary_review(proposal_id: str):
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active):
        return _problem("UNAUTHORIZED")
    try:
        validate_csrf(request.headers.get("X-CSRF-Token"))
    except ValidationError:
        return _problem("FORBIDDEN")

    expected_rev = None
    if request.is_json and request.json:
        expected_rev = request.json.get("expected_revision")

    try:
        result = undo_review_decision(current_user.id, proposal_id, expected_revision=expected_rev)
        return jsonify(result), 200
    except CommandProblem as cp:
        return _problem(str(cp))

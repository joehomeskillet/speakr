"""Tests for vocabulary review API slice (w102-vocabulary-review-api).

Covers PAR-VOC-004, PAR-VOC-005, R-031, BT-VOC-004, BT-VOC-005.
Tests parity commands endpoint, review queue read, concurrency, unauthorized
pool promotion prevention, idempotency, and undo transitions.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clinic.identity import ClinicAccountPolicy, create_tables as create_identity_tables
from src.clinic.parity_dispatch import ParityDispatch
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember, create_tables as create_pool_tables
from src.clinic import routes_vocabulary_review as rvr
from src.clinic import vocabulary
from src.database import db
from src.models import User

PATH_PARITY = "/api/clinic/v1/parity/commands"
PATH_REVIEWS = "/api/clinic/v1/resources/vocabulary/reviews"


class MockUser(UserMixin):
    def __init__(self, user_id: int, is_admin: bool = False, is_active: bool = True):
        self.id = user_id
        self._is_admin = is_admin
        self._is_active = is_active

    @property
    def is_admin(self) -> bool:
        return self._is_admin

    @property
    def is_active(self) -> bool:
        return self._is_active

    def get_id(self) -> str:
        return str(self.id)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Setup test Flask app with real SQLite DB, CSRF, and ParityDispatch."""
    monkeypatch.setenv("CLINIC_MODE", "1")
    db_file = tmp_path / "vocab_review_api.sqlite"
    engine = create_engine(f"sqlite:///{db_file}")

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-vocab-review-secret-key",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        WTF_CSRF_ENABLED=True,
    )
    db.init_app(app)

    with app.app_context():
        # Create all needed tables
        User.__table__.create(bind=engine, checkfirst=True)
        create_identity_tables(bind=engine)
        create_pool_tables(bind=engine)
        vocabulary.create_tables(bind=engine)

    login_manager = LoginManager(app)
    CSRFProtect(app)

    # In-memory idempotency receipts for test run_once
    receipts: dict[tuple, tuple[str, dict]] = {}

    def run_once(scope, digest, operation):
        if scope in receipts:
            saved_digest, result = receipts[scope]
            if saved_digest != digest:
                from src.clinic.parity_dispatch import CommandProblem
                raise CommandProblem("IDEMPOTENCY_CONFLICT")
            return result
        result = operation()
        db.session.commit()
        receipts[scope] = (digest, result)
        return result

    dispatch = ParityDispatch(run_once)
    rvr.register_parity_review_handlers(dispatch)
    app.register_blueprint(dispatch.blueprint)
    app.register_blueprint(rvr.vocabulary_review_bp)

    current_actor: list[MockUser] = [MockUser(101)]

    @login_manager.user_loader
    def load_user(uid: str):
        if current_actor[0] and str(current_actor[0].id) == uid:
            return current_actor[0]
        return None

    @app.get("/test-csrf")
    def test_csrf():
        return jsonify(csrf=generate_csrf())

    @app.get("/test-login/<int:uid>")
    def test_login(uid: int):
        is_admin = uid == 999
        u = MockUser(uid, is_admin=is_admin)
        current_actor[0] = u
        login_user(u)
        return jsonify(csrf=generate_csrf(), user_id=uid)

    # Populate synthetic users and pool
    with app.app_context():
        user_alpha = User(id=101, username="alpha", email="alpha@test.ch", password="x", is_admin=False)
        user_beta = User(id=102, username="beta", email="beta@test.ch", password="x", is_admin=False)
        user_admin = User(id=999, username="pruefer", email="pruefer@test.ch", password="x", is_admin=True)
        db.session.add_all([user_alpha, user_beta, user_admin])
        db.session.commit()

        pool_cardio = ClinicAuthorPool(id="pool-cardio", name="Kardiologie", created_by=999)
        db.session.add(pool_cardio)
        db.session.commit()

        # Alpha is member of pool-cardio
        member_alpha = ClinicAuthorPoolMember(pool_id="pool-cardio", user_id=101, created_by=999)
        db.session.add(member_alpha)
        db.session.commit()

    return app, current_actor


def _login(client, uid: int) -> dict[str, str]:
    resp = client.get(f"/test-login/{uid}")
    token = resp.json["csrf"]
    return {
        "X-CSRF-Token": token,
        "Idempotency-Key": f"key-{uuid.uuid4()}",
    }


# ============================================================================
# 1. Parity Dispatch Registration
# ============================================================================


def test_parity_registration(app_env):
    app, _ = app_env
    # Handler for review_resource_change is registered on clinic_parity
    rules = [rule.rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith("clinic_parity.")]
    assert PATH_PARITY in rules

    # Duplicate registration raises ValueError
    dispatch = ParityDispatch(lambda scope, digest, op: op())
    rvr.register_parity_review_handlers(dispatch)
    with pytest.raises(ValueError, match="duplicate"):
        rvr.register_parity_review_handlers(dispatch)


# ============================================================================
# 2. Positive Review Transitions (User, Pool, Reject, Defer)
# ============================================================================


def test_positive_accept_to_user(app_env):
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(
            user_id=101,
            written_form="Hypertonie",
            spoken_form="hoher Blutdruck",
            pool_id="pool-cardio",
            operation=vocabulary.REVIEW_OP_ADD,
        )
        db.session.commit()
        item_id = item.id

    headers = _login(client, 101)
    body = {
        "command": "review_resource_change",
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "payload": {
            "proposal_id": item_id,
            "decision": "user",
        },
    }

    res = client.post(PATH_PARITY, headers=headers, json=body)
    assert res.status_code == 200, res.data
    data = res.json
    assert data["status"] == "committed"
    assert data["revision"] == 2
    assert data["request_id"] == body["request_id"]
    assert "command_id" in data

    with app.app_context():
        it = db.session.get(vocabulary.ClinicVocabularyReviewItem, item_id)
        assert it.status == vocabulary.REVIEW_STATUS_ACCEPTED_USER
        assert it.target_entry_id is not None

        # Check vocabulary entry
        entry = db.session.get(vocabulary.ClinicVocabularyEntry, it.target_entry_id)
        assert entry is not None
        assert entry.written_form == "Hypertonie"
        assert entry.scope == "user"
        assert entry.user_id == 101

        # Check append-only decision record
        history = vocabulary.get_decision_history(item_id)
        assert len(history) == 1
        assert history[0].action == vocabulary.ACTION_ACCEPT_USER
        assert history[0].actor_id == 101


def test_positive_accept_to_pool_by_admin(app_env):
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(
            user_id=101,
            written_form="Vorhofflimmern",
            pool_id="pool-cardio",
            operation=vocabulary.REVIEW_OP_ADD,
        )
        db.session.commit()
        item_id = item.id

    # Admin 999 approves to pool
    headers = _login(client, 999)
    body = {
        "command": "review_resource_change",
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "payload": {
            "proposal_id": item_id,
            "decision": "pool",
        },
    }

    res = client.post(PATH_PARITY, headers=headers, json=body)
    assert res.status_code == 200, res.data
    assert res.json["status"] == "committed"
    assert res.json["revision"] == 2

    with app.app_context():
        it = db.session.get(vocabulary.ClinicVocabularyReviewItem, item_id)
        assert it.status == vocabulary.REVIEW_STATUS_ACCEPTED_POOL
        entry = db.session.get(vocabulary.ClinicVocabularyEntry, it.target_entry_id)
        assert entry.scope == "pool"
        assert entry.pool_id == "pool-cardio"
        assert entry.written_form == "Vorhofflimmern"


def test_positive_reject_and_defer(app_env):
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item1 = vocabulary.submit_review_item(user_id=101, written_form="Tippfehler")
        item2 = vocabulary.submit_review_item(user_id=101, written_form="UnklarePhrase")
        db.session.commit()
        id1, id2 = item1.id, item2.id

    headers = _login(client, 101)

    # Reject item1
    res1 = client.post(
        PATH_PARITY,
        headers=headers,
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "payload": {"proposal_id": id1, "decision": "reject"},
        },
    )
    assert res1.status_code == 200
    assert res1.json["revision"] == 2

    # Defer item2
    res2 = client.post(
        PATH_PARITY,
        headers=_login(client, 101),
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "payload": {"proposal_id": id2, "decision": "defer"},
        },
    )
    assert res2.status_code == 200
    assert res2.json["revision"] == 2

    with app.app_context():
        it1 = db.session.get(vocabulary.ClinicVocabularyReviewItem, id1)
        it2 = db.session.get(vocabulary.ClinicVocabularyReviewItem, id2)
        assert it1.status == vocabulary.REVIEW_STATUS_REJECTED
        assert it2.status == vocabulary.REVIEW_STATUS_DEFERRED

    # Deferred item can be accepted later at revision 2
    res3 = client.post(
        PATH_PARITY,
        headers=_login(client, 101),
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 2,
            "payload": {"proposal_id": id2, "decision": "user"},
        },
    )
    assert res3.status_code == 200
    assert res3.json["revision"] == 3


# ============================================================================
# 3. Concurrency, Revisions & Unauthorized Pool Promotion (Criterion 2)
# ============================================================================


def test_unauthorized_pool_promotion_fails_without_modifying_entry(app_env):
    """Ordinary author Alpha cannot promote to pool vocabulary without grant."""
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(
            user_id=101,
            written_form="EigenmächtigesWort",
            pool_id="pool-cardio",
        )
        db.session.commit()
        item_id = item.id

    headers = _login(client, 101)  # Alpha is ordinary author, not reviewer
    body = {
        "command": "review_resource_change",
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "payload": {
            "proposal_id": item_id,
            "decision": "pool",  # Unauthorized pool promotion
        },
    }

    res = client.post(PATH_PARITY, headers=headers, json=body)
    assert res.status_code == 403
    assert res.json["code"] == "POLICY_DENIED"

    # Verify database entry is completely untouched
    with app.app_context():
        it = db.session.get(vocabulary.ClinicVocabularyReviewItem, item_id)
        assert it.status == vocabulary.REVIEW_STATUS_PENDING
        assert it.target_entry_id is None
        assert it.target_pool_id is None
        # No decisions logged
        history = vocabulary.get_decision_history(item_id)
        assert len(history) == 0


def test_stale_revision_conflict(app_env):
    """Review with mismatched expected_revision raises 409 REVISION_CONFLICT."""
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(user_id=101, written_form="Kollision")
        db.session.commit()
        item_id = item.id

    headers = _login(client, 101)

    # Pass stale revision 0 when item is at revision 1
    res = client.post(
        PATH_PARITY,
        headers=headers,
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 0,
            "payload": {"proposal_id": item_id, "decision": "user"},
        },
    )
    assert res.status_code == 409
    assert res.json["code"] == "REVISION_CONFLICT"

    # Pass future revision 5
    res2 = client.post(
        PATH_PARITY,
        headers=_login(client, 101),
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 5,
            "payload": {"proposal_id": item_id, "decision": "user"},
        },
    )
    assert res2.status_code == 409
    assert res2.json["code"] == "REVISION_CONFLICT"


def test_concurrent_review_second_fails(app_env):
    """Two concurrent reviewers send expected_revision=1; first succeeds, second gets 409."""
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(user_id=101, written_form="Rennen", pool_id="pool-cardio")
        db.session.commit()
        item_id = item.id

    h1 = _login(client, 101)
    req1 = {
        "command": "review_resource_change",
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "payload": {"proposal_id": item_id, "decision": "user"},
    }
    r1 = client.post(PATH_PARITY, headers=h1, json=req1)
    assert r1.status_code == 200

    # Second reviewer tries expected_revision 1
    h2 = _login(client, 999)
    req2 = {
        "command": "review_resource_change",
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "payload": {"proposal_id": item_id, "decision": "pool"},
    }
    r2 = client.post(PATH_PARITY, headers=h2, json=req2)
    assert r2.status_code == 409
    assert r2.json["code"] == "REVISION_CONFLICT"


# ============================================================================
# 4. Security, CSRF, Idempotency & Untrusted Actor (Criterion 3)
# ============================================================================


def test_idempotent_replay_and_conflict(app_env):
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(user_id=101, written_form="Einmalig")
        db.session.commit()
        item_id = item.id

    headers = _login(client, 101)
    body = {
        "command": "review_resource_change",
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "payload": {"proposal_id": item_id, "decision": "user"},
    }

    # First attempt
    res1 = client.post(PATH_PARITY, headers=headers, json=body)
    assert res1.status_code == 200

    # Exact replay with same Idempotency-Key
    res2 = client.post(PATH_PARITY, headers=headers, json=body)
    assert res2.status_code == 200
    assert res2.json == res1.json

    # Conflicting reuse of same Idempotency-Key
    conflict_body = dict(body, request_id=str(uuid.uuid4()))
    res3 = client.post(PATH_PARITY, headers=headers, json=conflict_body)
    assert res3.status_code == 409
    assert res3.json["code"] == "IDEMPOTENCY_CONFLICT"


def test_security_guards_unauthenticated_and_csrf(app_env, monkeypatch):
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(user_id=101, written_form="Sicherheit")
        db.session.commit()
        item_id = item.id

    body = {
        "command": "review_resource_change",
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "payload": {"proposal_id": item_id, "decision": "user"},
    }

    # 1. Unauthenticated with valid CSRF token -> 401 UNAUTHORIZED
    anon_csrf = client.get("/test-csrf").json["csrf"]
    unauth = client.post(
        PATH_PARITY,
        headers={"X-CSRF-Token": anon_csrf, "Idempotency-Key": "key-12345678"},
        json=body,
    )
    assert unauth.status_code == 401
    assert unauth.json["code"] == "UNAUTHORIZED"

    # 2. Authenticated but missing CSRF Token -> 403 FORBIDDEN
    _login(client, 101)
    no_csrf = client.post(PATH_PARITY, headers={"Idempotency-Key": "key-12345678"}, json=body)
    assert no_csrf.status_code == 403

    # 3. Foreign author trying to review Alpha's personal word
    foreign_headers = _login(client, 102)  # Beta
    foreign = client.post(PATH_PARITY, headers=foreign_headers, json=body)
    assert foreign.status_code == 403
    assert foreign.json["code"] == "POLICY_DENIED"

    # 4. Clinic mode disabled -> 404
    monkeypatch.delenv("CLINIC_MODE")
    off = client.post(PATH_PARITY, headers=_login(client, 101), json=body)
    assert off.status_code == 404


# ============================================================================
# 5. Undo Transition & Decision History (Criterion 1)
# ============================================================================


def test_undo_transition_and_history(app_env):
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(user_id=101, written_form="Widerruf")
        db.session.commit()
        item_id = item.id

    headers = _login(client, 101)

    # 1. Accept to user
    res1 = client.post(
        PATH_PARITY,
        headers=headers,
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "payload": {"proposal_id": item_id, "decision": "user"},
        },
    )
    assert res1.status_code == 200
    assert res1.json["revision"] == 2

    # 2. Undo decision via Blueprint endpoint
    undo_headers = _login(client, 101)
    res_undo = client.post(
        f"{PATH_REVIEWS}/{item_id}/undo",
        headers=undo_headers,
        json={"expected_revision": 2},
    )
    assert res_undo.status_code == 200
    assert res_undo.json["status"] == vocabulary.REVIEW_STATUS_PENDING
    assert res_undo.json["revision"] == 3

    # 3. Verify history and vocabulary entry deletion
    with app.app_context():
        it = db.session.get(vocabulary.ClinicVocabularyReviewItem, item_id)
        assert it.status == vocabulary.REVIEW_STATUS_PENDING
        assert it.target_entry_id is None

        history = vocabulary.get_decision_history(item_id)
        assert len(history) == 2
        assert history[0].action == vocabulary.ACTION_ACCEPT_USER
        assert history[1].action == vocabulary.ACTION_UNDO


# ============================================================================
# 6. Review Queue Read (GET /resources/vocabulary/reviews)
# ============================================================================


def test_list_review_queue_and_etag(app_env):
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        vocabulary.submit_review_item(user_id=101, written_form="Alpha1")
        vocabulary.submit_review_item(user_id=101, written_form="Alpha2")
        vocabulary.submit_review_item(user_id=102, written_form="Beta1")
        db.session.commit()

    headers = _login(client, 101)

    # Read user review queue
    res = client.get(f"{PATH_REVIEWS}?scope_type=user&scope_id=101", headers=headers)
    assert res.status_code == 200
    data = res.json
    assert "revision" in data
    assert "items" in data
    assert len(data["items"]) == 2
    assert {i["written_form"] for i in data["items"]} == {"Alpha1", "Alpha2"}

    # ETag / If-None-Match test
    etag = res.headers.get("ETag")
    assert etag is not None
    etag_headers = dict(headers, **{"If-None-Match": etag})
    res_not_modified = client.get(f"{PATH_REVIEWS}?scope_type=user&scope_id=101", headers=etag_headers)
    assert res_not_modified.status_code == 304

    # Unauthorized read of Beta's queue
    res_foreign = client.get(f"{PATH_REVIEWS}?scope_type=user&scope_id=102", headers=headers)
    assert res_foreign.status_code == 403


# ============================================================================
# 7. BT-VOC-004 & BT-VOC-005 Behavioral Verification
# ============================================================================


def test_bt_voc_004_oracle_behavior(app_env):
    """BT-VOC-004: Alpha submits, unapproved has no effect on pool; Prüfer approves."""
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        # Alpha submits a new word
        item = vocabulary.submit_review_item(
            user_id=101,
            written_form="Spezialbegriff",
            pool_id="pool-cardio",
            operation=vocabulary.REVIEW_OP_ADD,
        )
        db.session.commit()
        item_id = item.id

        # Before review: word MUST NOT be in effective vocabulary
        eff = vocabulary.get_effective_vocabulary(user_id=101, pool_ids=["pool-cardio"])
        assert "Spezialbegriff" not in eff

    # Alpha cannot approve to pool
    h_alpha = _login(client, 101)
    res_deny = client.post(
        PATH_PARITY,
        headers=h_alpha,
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "payload": {"proposal_id": item_id, "decision": "pool"},
        },
    )
    assert res_deny.status_code == 403

    # Still not in effective vocabulary
    with app.app_context():
        eff = vocabulary.get_effective_vocabulary(user_id=101, pool_ids=["pool-cardio"])
        assert "Spezialbegriff" not in eff

    # Prüfer (Admin 999) approves to pool
    h_admin = _login(client, 999)
    res_ok = client.post(
        PATH_PARITY,
        headers=h_admin,
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "payload": {"proposal_id": item_id, "decision": "pool"},
        },
    )
    assert res_ok.status_code == 200

    # Now in effective vocabulary for all pool members
    with app.app_context():
        eff_alpha = vocabulary.get_effective_vocabulary(user_id=101, pool_ids=["pool-cardio"])
        assert "Spezialbegriff" in eff_alpha
        eff_beta = vocabulary.get_effective_vocabulary(user_id=102, pool_ids=["pool-cardio"])
        assert "Spezialbegriff" in eff_beta


def test_bt_voc_005_session_review_and_defer(app_env):
    """BT-VOC-005: Session review deferred remains pending; user-approved stays private."""
    app, _ = app_env
    client = app.test_client()

    with app.app_context():
        item = vocabulary.submit_review_item(
            user_id=101,
            written_form="Privatwort",
            origin=vocabulary.ORIGIN_SESSION,
        )
        db.session.commit()
        item_id = item.id

    # 1. Defer at end of session
    h_alpha = _login(client, 101)
    r_def = client.post(
        PATH_PARITY,
        headers=h_alpha,
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "payload": {"proposal_id": item_id, "decision": "defer"},
        },
    )
    assert r_def.status_code == 200

    # 2. Offered again in pending/deferred list
    r_list = client.get(f"{PATH_REVIEWS}?scope_type=user&scope_id=101&status=DEFERRED", headers=h_alpha)
    assert r_list.status_code == 200
    assert any(i["id"] == item_id for i in r_list.json["items"])

    # 3. Alpha accepts to user scope
    r_accept = client.post(
        PATH_PARITY,
        headers=_login(client, 101),
        json={
            "command": "review_resource_change",
            "request_id": str(uuid.uuid4()),
            "expected_revision": 2,
            "payload": {"proposal_id": item_id, "decision": "user"},
        },
    )
    assert r_accept.status_code == 200

    # 4. Visible only to Alpha, never to Beta
    with app.app_context():
        eff_alpha = vocabulary.get_effective_vocabulary(user_id=101, pool_ids=["pool-cardio"])
        assert "Privatwort" in eff_alpha
        eff_beta = vocabulary.get_effective_vocabulary(user_id=102, pool_ids=["pool-cardio"])
        assert "Privatwort" not in eff_beta

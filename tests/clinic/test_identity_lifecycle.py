"""Local identity lifecycle (docs/10_Local_Entra.md sections 6 and 7).

Backs T-075, T-076, T-077 (service level), T-078, T-079 and the service part
of T-073 and T-074. Acceptance entries stay not_run: no route, no parallel
process and no PostgreSQL run is covered here.
"""

import hashlib
import importlib.util
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import pytest
from sqlalchemy import text

from src.app import app, db
from src.clinic import identity
from src.models import User

GOOD = "correct horse battery staple"
OTHER = "another long passphrase 42"
T0 = datetime(2026, 11, 17, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def good_hash():
    return identity.hash_password(GOOD)


@pytest.fixture(autouse=True)
def ctx(clinic_isolation):
    with app.app_context():
        identity.create_tables()
        # The original recovery contract names issuer 1. Only this module needs it.
        # clinic_isolation snapshots existing accounts before this fixture runs.
        if db.engine.dialect.name == "postgresql" and db.session.get(User, 1) is None:
            # Reserve the first sequence value on a fresh database without rewinding it.
            db.session.execute(text("""SELECT nextval(pg_get_serial_sequence('"user"', 'id'))"""))
            db.session.add(User(id=1, username="clinic_issuer", email="clinic_issuer@local.test",
                                password="x", is_admin=True))
            db.session.commit()
        db.session.rollback()
        for model in (identity.ClinicLocalRecovery, identity.ClinicBootstrapMarker,
                      identity.ClinicAccountPolicy):
            db.session.query(model).delete()
        db.session.commit()
        yield
        db.session.rollback()


def _suffix():
    return uuid.uuid4().hex[:10]


def _user(status="active", login_policy="local_only", password=None, admin=False):
    suffix = _suffix()
    user = User(username="id" + suffix, email=suffix + "@example.invalid", password=password)
    db.session.add(user)
    db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(
        user_id=user.id, status=status, login_policy=login_policy, is_identity_admin=admin))
    db.session.commit()
    return user


def _policy(user):
    db.session.expire_all()
    return db.session.get(identity.ClinicAccountPolicy, user.id)


def _recovery_rows(user):
    return identity.ClinicLocalRecovery.query.filter_by(user_id=user.id).all()


# --- password policy (T-079) ---

@pytest.mark.parametrize("password,code", [
    ("a" * 14, "too_short"),
    ("ä" * 14, "too_short"),
    ("a" * 129, "too_long"),
    ("PasswordPassword", "blocklisted"),
    ("pass word pass word", "blocklisted"),
    ("１２３４５６７８９０１２３４５", "blocklisted"),  # full-width digits, NFKC match
    (None, "invalid_type"),
])
def test_policy_rejects(password, code):
    with pytest.raises(identity.PasswordPolicyError) as raised:
        identity.validate_password(password)
    assert raised.value.code == code


@pytest.mark.parametrize("password", [
    "a1" * 7 + "b",                  # exactly 15
    "x" * 128,                       # exactly 128
    "ä" * 15,
    "  " + "ab" * 6 + "  ",          # 16 with edge spaces: no trim
    "Müller-Straße 12, Zürich 😀!",  # unicode, spaces
    "ab " * 42 + "ab",               # 128 with spaces
])
def test_policy_accepts(password):
    identity.validate_password(password)


def test_site_blocklist_file(tmp_path, monkeypatch):
    path = tmp_path / "blocklist.txt"
    path.write_text("# comment\nMy Clinic Secret 123\n", encoding="utf-8")
    monkeypatch.setenv(identity.BLOCKLIST_FILE_ENV, str(path))
    with pytest.raises(identity.PasswordPolicyError):
        identity.validate_password("my clinic secret 123")
    identity.validate_password("my clinic secret 124")


# --- hashing and legacy bcrypt (T-078) ---

def test_hash_format_and_no_truncation(good_hash):
    assert good_hash.startswith("scrypt:131072:8:1$")
    assert len(good_hash) > 60
    long_pw = "ü" * 127 + "a"
    stored = identity.hash_password(long_pw)
    assert identity.verify_password(stored, long_pw) == (True, False)
    assert identity.verify_password(stored, "ü" * 127 + "b") == (False, False)
    assert identity.verify_password(stored, "ü" * 127) == (False, False)


def test_other_scrypt_parameters_verify_and_rehash():
    from werkzeug.security import generate_password_hash
    weak = generate_password_hash(GOOD, method="scrypt:16384:8:1")
    assert identity.verify_password(weak, GOOD) == (True, True)


def test_bcrypt_legacy_verifies_and_upgrades_on_login():
    legacy = bcrypt.hashpw(GOOD.encode(), bcrypt.gensalt(rounds=4)).decode()
    assert len(legacy) == 60
    user = _user(password=legacy)
    assert identity.verify_password(legacy, GOOD) == (True, True)
    assert identity.verify_password(legacy, OTHER) == (False, False)

    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(user.username, OTHER)
    db.session.expire_all()
    assert user.password == legacy  # a failed login does not rehash

    result = identity.authenticate_local(user.username, GOOD)
    db.session.expire_all()
    assert result.user_id == user.id
    assert user.password.startswith("scrypt:131072:8:1$")
    assert identity.authenticate_local(user.username, GOOD).user_id == user.id


@pytest.mark.parametrize("stored", [
    "plaintext-password-value", "$1$abcdefgh$abcdefghijklmnopqrstuv", "pbkdf2:sha256:1$a$b",
    "$2b$12$short", "x" * 200,
])
def test_unknown_hash_formats_are_refused(stored):
    assert identity.verify_password(stored, GOOD) == (False, False)
    user = _user(password=stored)
    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(user.username, GOOD)


def test_hash_slots_give_backpressure(monkeypatch):
    monkeypatch.setattr(identity, "HASH_WAIT_SECONDS", 0.05)
    for _ in range(identity.MAX_CONCURRENT_HASHES):
        assert identity._hash_slots.acquire(timeout=1)
    try:
        with pytest.raises(identity.HashBusy):
            identity.hash_password(GOOD)
    finally:
        for _ in range(identity.MAX_CONCURRENT_HASHES):
            identity._hash_slots.release()


# --- authentication gates (T-009, T-077) ---

@pytest.mark.parametrize("status,login_policy", [
    ("disabled", "local_only"),
    ("pending_activation", "local_only"),
    ("active", "entra_only"),
    ("active", "device_only"),
    ("disabled", "local_and_entra"),
])
def test_ineligible_accounts_cannot_authenticate(good_hash, status, login_policy):
    user = _user(status=status, login_policy=login_policy, password=good_hash)
    with pytest.raises(identity.AuthenticationFailed) as raised:
        identity.authenticate_local(user.username, GOOD)
    unknown = pytest.raises(identity.AuthenticationFailed)
    with unknown as unknown_raised:
        identity.authenticate_local("no-such-" + _suffix(), GOOD)
    assert str(raised.value) == str(unknown_raised.value)  # same message everywhere


def test_user_without_policy_row_has_no_local_login(good_hash):
    suffix = _suffix()
    user = User(username="np" + suffix, email=suffix + "@example.invalid", password=good_hash)
    db.session.add(user)
    db.session.commit()
    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(user.username, GOOD)


def test_active_local_user_logs_in_by_username_or_email(good_hash):
    user = _user(password=good_hash)
    assert identity.authenticate_local(user.username, GOOD).user_id == user.id
    assert identity.authenticate_local(user.email.upper(), GOOD).user_id == user.id
    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(user.username, GOOD + " ")  # no trimming
    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(user.username, "x" * 500)


def test_local_and_entra_policy_still_allows_local_login(good_hash):
    user = _user(login_policy="local_and_entra", password=good_hash)
    assert identity.authenticate_local(user.username, GOOD).user_id == user.id


# --- create, activate (T-075) ---

def test_create_local_user_is_pending_without_password():
    suffix = _suffix()
    user = identity.create_local_user("cu" + suffix, " CU" + suffix + "@Example.Invalid ", actor_id=1)
    policy = _policy(user)
    assert (policy.status, policy.login_policy, user.password) == ("pending_activation", "local_only", None)
    assert user.email == "cu" + suffix + "@example.invalid"
    assert not policy.is_identity_admin
    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(user.username, GOOD)


def test_create_local_user_rejects_duplicates_and_bad_input():
    suffix = _suffix()
    identity.create_local_user("dp" + suffix, suffix + "@example.invalid")
    with pytest.raises(identity.IdentityConflict):
        identity.create_local_user("dp" + suffix, "other" + suffix + "@example.invalid")
    with pytest.raises(identity.IdentityConflict):
        identity.create_local_user("dq" + suffix, suffix.upper() + "@example.invalid")
    with pytest.raises(identity.IdentityConflict):
        identity.create_local_user("x", "x@example.invalid")
    with pytest.raises(identity.IdentityConflict):
        identity.create_local_user("valid" + suffix, "not-an-address")


def test_activation_flow_single_use_and_no_auto_login():
    user = identity.create_local_user("ac" + _suffix(), _suffix() + "@example.invalid")
    issued = identity.issue_recovery(user.id, issued_by=1, now=T0)
    assert issued.purpose == "activate_local"
    assert issued.expires_at == T0 + timedelta(minutes=30)
    assert len(issued.code) >= 43  # 256 bit, urlsafe
    assert issued.code not in repr(issued)

    identity.complete_recovery(issued.code, GOOD, now=T0 + timedelta(minutes=1))
    assert _policy(user).status == "active"
    assert identity.authenticate_local(user.username, GOOD).user_id == user.id
    with pytest.raises(identity.InvalidRecoveryCode):
        identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(minutes=2))
    assert identity.authenticate_local(user.username, GOOD).user_id == user.id


# --- reset (T-076, T-077) ---

def _reset(good_hash, **kwargs):
    user = _user(password=good_hash, **kwargs)
    return user, identity.issue_recovery(user.id, issued_by=1, now=T0)


def test_reset_window_is_thirty_minutes(good_hash):
    user, issued = _reset(good_hash)
    with pytest.raises(identity.InvalidRecoveryCode):
        identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(minutes=30))
    with pytest.raises(identity.InvalidRecoveryCode):
        identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(hours=2))
    identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(minutes=29, seconds=59))
    assert identity.authenticate_local(user.username, OTHER).user_id == user.id


def test_reset_is_single_use(good_hash):
    user, issued = _reset(good_hash)
    identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(minutes=1))
    with pytest.raises(identity.InvalidRecoveryCode):
        identity.complete_recovery(issued.code, "a third long passphrase 7", now=T0 + timedelta(minutes=2))
    assert identity.authenticate_local(user.username, OTHER).user_id == user.id


def test_newer_code_supersedes_older(good_hash):
    user, first = _reset(good_hash)
    second = identity.issue_recovery(user.id, issued_by=1, now=T0 + timedelta(minutes=1))
    with pytest.raises(identity.InvalidRecoveryCode):
        identity.complete_recovery(first.code, OTHER, now=T0 + timedelta(minutes=2))
    identity.complete_recovery(second.code, OTHER, now=T0 + timedelta(minutes=2))


def test_password_change_voids_open_code(good_hash):
    user, issued = _reset(good_hash)
    identity.change_password(user.id, GOOD, OTHER, now=T0 + timedelta(minutes=1))
    with pytest.raises(identity.InvalidRecoveryCode):
        identity.complete_recovery(issued.code, "a third long passphrase 7", now=T0 + timedelta(minutes=2))
    assert identity.authenticate_local(user.username, OTHER).user_id == user.id
    with pytest.raises(identity.AuthenticationFailed):
        identity.change_password(user.id, "wrong current password!", OTHER)


def test_reset_ends_sessions_and_forces_change(good_hash):
    user = _user(password=good_hash)
    before = _policy(user).security_version
    session_before = user.session_version or 0
    issued = identity.issue_recovery(user.id, issued_by=1, now=T0)
    policy = _policy(user)
    assert policy.security_version == before + 1
    assert (user.session_version or 0) == session_before + 1
    assert policy.must_change_password is True
    assert identity.authenticate_local(user.username, GOOD).must_change_password is True

    identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(minutes=1))
    policy = _policy(user)
    assert policy.security_version == before + 2
    assert policy.must_change_password is False
    assert policy.password_changed_at is not None
    assert identity.authenticate_local(user.username, OTHER).must_change_password is False
    current = identity.security_version_of(user.id)
    assert current == before + 2
    assert identity.revoke_sessions(user.id) == current + 1


def test_rejected_password_leaves_code_usable(good_hash):
    user, issued = _reset(good_hash)
    with pytest.raises(identity.PasswordPolicyError):
        identity.complete_recovery(issued.code, "short", now=T0 + timedelta(minutes=1))
    with pytest.raises(identity.PasswordPolicyError):
        identity.complete_recovery(issued.code, "PasswordPassword", now=T0 + timedelta(minutes=1))
    identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(minutes=2))


@pytest.mark.parametrize("status,login_policy", [
    ("disabled", "local_only"),
    ("active", "entra_only"),
    ("active", "device_only"),
])
def test_recovery_is_refused_without_policy_change(good_hash, status, login_policy):
    user = _user(status=status, login_policy=login_policy, password=good_hash)
    with pytest.raises(identity.IdentityDenied):
        identity.issue_recovery(user.id, issued_by=1, now=T0)
    policy = _policy(user)
    assert (policy.status, policy.login_policy) == (status, login_policy)
    assert _recovery_rows(user) == []


def test_codes_do_not_survive_disable_or_policy_change(good_hash):
    user, issued = _reset(good_hash)
    identity.set_status(user.id, "disabled", actor_id=1, now=T0)
    with pytest.raises(identity.InvalidRecoveryCode):
        identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(minutes=1))
    assert _policy(user).status == "disabled"

    user2, issued2 = _reset(good_hash)
    policy = _policy(user2)
    policy.login_policy = "entra_only"  # changed behind the code's back
    db.session.commit()
    with pytest.raises(identity.InvalidRecoveryCode):
        identity.complete_recovery(issued2.code, OTHER, now=T0 + timedelta(minutes=1))
    db.session.expire_all()
    assert db.session.get(User, user2.id).password == good_hash


def test_issue_checks_expected_version(good_hash):
    user = _user(password=good_hash)
    with pytest.raises(identity.VersionConflict):
        identity.issue_recovery(user.id, issued_by=1, expected_version=99, now=T0)
    identity.issue_recovery(user.id, issued_by=1, expected_version=_policy(user).version, now=T0)


def test_code_is_stored_only_as_hash(good_hash):
    user, issued = _reset(good_hash)
    (row,) = _recovery_rows(user)
    assert row.token_hash == hashlib.sha256(issued.code.encode()).hexdigest()
    assert issued.code not in row.token_hash
    dump = db.session.execute(text("SELECT * FROM clinic_local_recovery")).fetchall()
    assert all(issued.code not in str(value) for record in dump for value in record)
    assert row.purpose == "reset_local" and row.issued_by == 1 and row.consumed_at is None


# --- disable, reactivate, last administrator (T-074) ---

def test_disable_blocks_login_and_reactivate_restores(good_hash):
    user = _user(password=good_hash)
    before = _policy(user).security_version
    identity.set_status(user.id, "disabled", actor_id=1)
    assert _policy(user).security_version == before + 1
    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(user.username, GOOD)
    identity.set_status(user.id, "active", actor_id=1)
    assert identity.authenticate_local(user.username, GOOD).user_id == user.id
    with pytest.raises(identity.IdentityDenied):
        identity.set_status(user.id, "active")
    assert db.session.get(User, user.id) is not None  # nothing is ever deleted


def test_reactivating_a_never_activated_user_returns_to_pending():
    user = identity.create_local_user("ra" + _suffix(), _suffix() + "@example.invalid")
    identity.set_status(user.id, "disabled")
    identity.set_status(user.id, "active")
    assert _policy(user).status == "pending_activation"


def test_last_local_identity_admin_is_protected(good_hash):
    first = _user(password=good_hash, admin=True)
    with pytest.raises(identity.LastAdminProtected):
        identity.set_status(first.id, "disabled", actor_id=first.id)
    assert _policy(first).status == "active"
    second = _user(password=good_hash, admin=True)
    identity.set_status(first.id, "disabled", actor_id=second.id)
    with pytest.raises(identity.LastAdminProtected):
        identity.set_status(second.id, "disabled", actor_id=second.id)


# --- bootstrap (T-073 service level) ---

def test_bootstrap_is_idempotent_and_never_stores_plain_password():
    suffix = _suffix()
    username, email = "bs" + suffix, suffix + "@example.invalid"
    users_before = User.query.count()
    assert identity.bootstrap_first_admin(username, email, GOOD, name="Operator") is True
    admin = User.query.filter_by(username=username).one()
    policy = _policy(admin)
    assert (policy.status, policy.login_policy, policy.is_identity_admin) == ("active", "local_only", True)
    assert admin.is_admin is True
    assert admin.password.startswith("scrypt:131072:8:1$") and GOOD not in admin.password
    assert identity.authenticate_local(username, GOOD).user_id == admin.id

    snapshot = (admin.password, policy.version, User.query.count())
    assert identity.bootstrap_first_admin("bt" + suffix, "b" + email, OTHER) is False
    db.session.expire_all()
    assert (admin.password, _policy(admin).version, User.query.count()) == snapshot
    assert User.query.count() == users_before + 1


def test_bootstrap_rejects_weak_password_and_changes_nothing():
    count = User.query.count()
    with pytest.raises(identity.PasswordPolicyError):
        identity.bootstrap_first_admin("bw" + _suffix(), _suffix() + "@example.invalid", "short")
    assert User.query.count() == count
    assert identity.ClinicBootstrapMarker.query.count() == 0


def test_bootstrap_marker_blocks_a_second_row():
    admin = identity.bootstrap_first_admin("bm" + _suffix(), _suffix() + "@example.invalid", GOOD)
    assert admin is True
    db.session.add(identity.ClinicBootstrapMarker(id=1, user_id=User.query.first().id))
    with pytest.raises(Exception) as raised:
        db.session.commit()
    assert "IntegrityError" in type(raised.value).__name__
    db.session.rollback()


def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "clinic_bootstrap_admin.py"
    spec = importlib.util.spec_from_file_location("clinic_bootstrap_admin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Stdin(io.StringIO):
    def isatty(self):
        return False


def test_script_reads_secret_from_env_or_stdin_and_never_prints_it(capsys):
    script = _load_script()
    suffix = _suffix()
    argv = ["--username", "sc" + suffix, "--email", suffix + "@example.invalid"]
    assert GOOD not in " ".join(argv)

    assert script.run(argv, env={}, stdin=_Stdin("")) == 2  # no secret supplied
    assert script.run(argv, env={script.DEFAULT_PASSWORD_ENV: "short"}, stdin=_Stdin("")) == 2

    assert script.run(argv, env={script.DEFAULT_PASSWORD_ENV: GOOD}, stdin=_Stdin("")) == 0
    first = capsys.readouterr()
    assert User.query.filter_by(username="sc" + suffix).one().password.startswith("scrypt:")

    assert script.run(argv + ["--password-stdin"], env={}, stdin=_Stdin(OTHER + "\n")) == 0
    second = capsys.readouterr()
    assert "nothing changed" in second.out
    for output in (first, second):
        assert GOOD not in output.out + output.err and OTHER not in output.out + output.err
    assert identity.authenticate_local("sc" + suffix, GOOD).user_id


# --- logs carry no secrets ---

def test_no_secret_in_logs_or_messages(caplog, good_hash):
    caplog.set_level(logging.DEBUG)
    suffix = _suffix()
    identity.bootstrap_first_admin("lg" + suffix, suffix + "@example.invalid", GOOD)
    user = identity.create_local_user("lh" + suffix, "h" + suffix + "@example.invalid", actor_id=1)
    issued = identity.issue_recovery(user.id, issued_by=1, now=T0)
    messages = []
    for call in (
        lambda: identity.complete_recovery(issued.code, "short", now=T0),
        lambda: identity.complete_recovery(issued.code + "x", OTHER, now=T0),
        lambda: identity.authenticate_local("lg" + suffix, OTHER),
    ):
        with pytest.raises(identity.IdentityError) as raised:
            call()
        messages.append(str(raised.value) + repr(raised.value))
    identity.complete_recovery(issued.code, OTHER, now=T0 + timedelta(minutes=1))
    identity.authenticate_local("lg" + suffix, GOOD)

    stored_hash = User.query.filter_by(username="lg" + suffix).one().password
    haystack = caplog.text + " ".join(messages) + repr(issued)
    for secret in (GOOD, OTHER, issued.code, hashlib.sha256(issued.code.encode()).hexdigest(),
                   stored_hash, good_hash):
        assert secret not in haystack
    assert "clinic identity" in caplog.text  # the events themselves are logged

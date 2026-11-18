"""Synthetic Flask operator-console tests using the real identity service."""

import hashlib
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.app import app, db
from src.clinic import identity, identity_cli
from src.models import User

PASSWORD = "synthetic operator phrase 73"
BOOTSTRAP = ["bootstrap", "--username", "test_operator", "--email", "operator@example.invalid"]


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setattr(app.cli, "commands", app.cli.commands.copy())
    identity_cli.register_identity_commands(app)

    def invoke(*args):
        return app.test_cli_runner().invoke(args=["clinic-identity", *args])

    return invoke


@pytest.fixture
def terminal(monkeypatch):
    tty = SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr(identity_cli, "sys", SimpleNamespace(stdin=tty, stdout=tty))
    monkeypatch.setattr(identity_cli.getpass, "getpass", lambda _: PASSWORD)


def test_disabled_and_redirected_commands_never_prompt(cli, monkeypatch):
    monkeypatch.setattr(identity_cli.getpass, "getpass", lambda _: pytest.fail("unexpected prompt"))
    assert "interaktives Terminal" in cli(*BOOTSTRAP).stderr
    monkeypatch.delenv("CLINIC_MODE")
    assert "Klinikmodus ist deaktiviert" in cli(*BOOTSTRAP).stderr
    app.cli.commands.pop("clinic-identity")
    identity_cli.register_identity_commands(app)
    assert "clinic-identity" not in app.cli.commands


def test_bootstrap_fails_closed_without_hidden_input_and_can_retry(cli, terminal, monkeypatch):
    def visible_fallback(_):
        warnings.warn("no echo control", identity_cli.getpass.GetPassWarning, stacklevel=2)
        pytest.fail("password would have been visible")

    with monkeypatch.context() as patch:
        patch.setattr(identity_cli.getpass, "getpass", visible_fallback)
        failed = cli(*BOOTSTRAP)
        assert failed.exit_code == 1 and "Verdeckte Passworteingabe" in failed.stderr
    with app.app_context():
        assert identity.ClinicBootstrapMarker.query.count() == 0
    created = cli(*BOOTSTRAP)
    assert created.exit_code == 0 and PASSWORD not in created.output


def test_concurrent_bootstrap_creates_one_admin(cli, terminal, monkeypatch, capsys):
    gate = Barrier(2, timeout=15)
    hash_password = identity.hash_password

    def synchronize(password):
        gate.wait()
        return hash_password(password)

    def bootstrap(suffix):
        with app.app_context():
            app.cli.main(
                ["clinic-identity", "bootstrap", "--username", f"operator_{suffix}",
                 "--email", f"operator_{suffix}@example.invalid"],
                standalone_mode=False,
            )

    with monkeypatch.context() as patch:
        patch.setattr(identity, "hash_password", synchronize)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(bootstrap, ("a", "b")))
    assert sorted(capsys.readouterr().out.splitlines()) == [
        "Identitätsadministration bereits eingerichtet.", "Identitätsadministration eingerichtet.",
    ]
    assert cli(*BOOTSTRAP).exit_code == 0
    with app.app_context():
        marker = identity.ClinicBootstrapMarker.query.one()
        assert identity.ClinicAccountPolicy.query.filter_by(is_identity_admin=True).one().user_id == marker.user_id
        assert User.query.filter(User.username.in_(["operator_a", "operator_b", "test_operator"])).count() == 1


def test_recovery_requires_admin_and_current_version_and_issues_only_one_usable_code(
    cli, terminal, monkeypatch, caplog,
):
    caplog.set_level(logging.INFO, logger=identity.__name__)
    assert cli(*BOOTSTRAP).exit_code == 0
    with app.app_context():
        operator = User.query.filter_by(username="test_operator").one()
        operator_id = operator.id
        other = User(username="test_staff", email="staff@example.invalid", password=operator.password, is_admin=True)
        db.session.add(other)
        db.session.flush()
        db.session.add(identity.ClinicAccountPolicy(user_id=other.id, status="active"))
        db.session.commit()
        target_id = identity.create_local_user("test_target", "target@example.invalid").id

    def recover(version, operator="test_operator"):
        return cli("recovery", "--operator", operator, "--user-id", str(target_id),
                   "--expected-version", str(version))

    with monkeypatch.context() as patch:
        patch.setattr(identity_cli.sys.stdout, "isatty", lambda: False)
        redirected = recover(1)
    assert redirected.exit_code == 1 and not redirected.stdout

    denied = recover(1, "test_staff")
    with monkeypatch.context() as patch:
        patch.setattr(identity_cli.getpass, "getpass", lambda _: "wrong synthetic password")
        bad_password = recover(1)
    assert denied.exit_code == bad_password.exit_code == 1
    assert not denied.stdout and not bad_password.stdout
    assert denied.stderr == bad_password.stderr

    def fail_commit():
        raise SQLAlchemyError("synthetic database detail")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", fail_commit)
        failure = recover(1)
    assert failure.exit_code == 1 and not failure.stdout
    assert "Die Ausgabe ist fehlgeschlagen" in failure.stderr
    assert "synthetic database detail" not in failure.output
    with app.app_context():
        assert identity.ClinicLocalRecovery.query.count() == 0

    first = recover(1)
    stale = recover(1)
    second = recover(2)
    assert first.exit_code == second.exit_code == 0
    assert stale.exit_code == 1 and not stale.stdout and "Kontoversion ist veraltet" in stale.stderr
    first_code, second_code = first.stdout.strip(), second.stdout.strip()
    assert not first.stderr and not second.stderr and first_code != second_code
    assert all(secret not in caplog.text for secret in (PASSWORD, first_code, second_code))

    with app.app_context():
        rows = identity.ClinicLocalRecovery.query.filter_by(issued_by=operator_id, user_id=target_id).all()
        assert {row.token_hash for row in rows} == {
            hashlib.sha256(code.encode()).hexdigest() for code in (first_code, second_code)
        }
        with pytest.raises(identity.InvalidRecoveryCode):
            identity.complete_recovery(first_code, PASSWORD)
        identity.complete_recovery(second_code, PASSWORD)
        with pytest.raises(identity.InvalidRecoveryCode):
            identity.complete_recovery(second_code, PASSWORD)

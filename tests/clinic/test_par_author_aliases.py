"""Synthetic database round trips for authorized author/device alias storage."""

import uuid

import pytest

from src.app import app, db
from src.clinic import author_aliases as aliases
from src.clinic.agent_channel import AgentChannelError, aware
from src.clinic.author_resolution import AuthorResolutionError
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.registry import MODEL_MODULES
from src.models import User


@pytest.fixture(autouse=True)
def alias_environment(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    with app.app_context():
        yield


def _user(admin=False):
    key = uuid.uuid4().hex
    row = User(username=key[:20], email=f"{key}@example.invalid", password="x", is_admin=admin)
    db.session.add(row)
    db.session.commit()
    return row.id


def _add(actor, author, *, kind="author", code="AUTH99", **kwargs):
    return aliases.add_author_alias(
        actor_user_id=actor, author_user_id=author, kind=kind, code=code,
        provenance="synthetic approval P-011", **kwargs,
    )


@pytest.mark.parametrize("kind,code,lookup", [
    ("author", "  A\u0308da   Test  ", {"embedded_author": "ÄDA Test"}),
    ("device", " usb-1 ", {"device_id": "USB-1"}),
])
def test_authorized_round_trip_retains_provenance(kind, code, lookup):
    assert "author_aliases" in MODEL_MODULES
    actor, author = _user(True), _user()
    row = _add(actor, author, kind=kind, code=code, may_assign_author=lambda a, u: (a, u) == (actor, author))
    row_id = row.id
    db.session.remove()
    saved = db.session.get(aliases.ClinicAuthorAlias, row_id)
    assert saved.created_by_user_id == actor
    assert saved.author_user_id == author
    assert saved.provenance == "synthetic approval P-011"
    assert aware(saved.created_at).utcoffset().total_seconds() == 0
    result = aliases.resolve_stored_author(**lookup)
    assert (result.status, result.author_user_id) == ("resolved", author)


@pytest.mark.parametrize("kind", ["author", "device"])
def test_conflicting_code_remains_unresolved_after_round_trip(kind):
    first, second = _user(True), _user(True)
    first_row = _add(first, first, kind=kind)
    first_id = first_row.id
    _add(second, second, kind=kind, code=" auth99 ")
    db.session.remove()
    lookup = {"embedded_author" if kind == "author" else "device_id": "AUTH99"}
    result = aliases.resolve_stored_author(**lookup)
    assert (result.status, result.author_user_id) == ("conflict", None)
    assert aliases.ClinicAuthorAlias.query.count() == 2
    assert db.session.get(aliases.ClinicAuthorAlias, first_id).author_user_id == first


def test_device_cannot_override_conflicting_author_alias():
    first, second = _user(True), _user(True)
    _add(first, first, kind="device", code="USB-1")
    _add(first, first)
    _add(second, second)
    result = aliases.resolve_stored_author(device_id="USB-1", embedded_author="AUTH99")
    assert (result.status, result.author_user_id) == ("conflict", None)


def test_repeated_evidence_for_same_author_is_not_a_conflict():
    actor = _user(True)
    _add(actor, actor)
    _add(actor, actor)
    _add(actor, actor, kind="device", code="USB-1")
    assert aliases.ClinicAuthorAlias.query.count() == 3
    assert aliases.resolve_stored_author(device_id="USB-1", embedded_author="AUTH99").author_user_id == actor


@pytest.mark.parametrize("case", ["ordinary", "disabled", "foreign", "denied", "unknown"])
def test_unauthorized_mapping_writes_nothing(case):
    actor = _user(case != "ordinary")
    author = _user() if case in ("foreign", "denied") else actor
    if case == "disabled":
        db.session.add(ClinicAccountPolicy(user_id=actor, status="disabled"))
        db.session.commit()
    if case == "unknown":
        actor = -1
    with pytest.raises(AgentChannelError) as exc:
        _add(actor, author, may_assign_author=(lambda *_: False) if case == "denied" else None)
    assert exc.value.code == "forbidden"
    assert aliases.ClinicAuthorAlias.query.count() == 0


@pytest.mark.parametrize("changes", [
    {"kind": "unknown"}, {"code": " "}, {"code": "x" * 256}, {"code": "a\x00b"},
    {"code": None}, {"author_user_id": True}, {"author_user_id": -1},
    {"provenance": " "}, {"provenance": "x" * 1025}, {"provenance": "a\x00b"},
])
def test_invalid_mapping_writes_nothing(changes):
    actor = _user(True)
    arguments = dict(actor_user_id=actor, author_user_id=actor, kind="author", code="AUTH99", provenance="approval")
    arguments.update(changes)
    with pytest.raises((AgentChannelError, AuthorResolutionError)):
        aliases.add_author_alias(**arguments)
    assert aliases.ClinicAuthorAlias.query.count() == 0


def test_missing_unknown_and_inactive_authors_require_clarification():
    actor = _user(True)
    _add(actor, actor)
    for lookup in ({}, {"device_id": "unknown"}, {"embedded_author": "unknown"}):
        result = aliases.resolve_stored_author(**lookup)
        assert (result.status, result.author_user_id) == ("unresolved", None)
    db.session.add(ClinicAccountPolicy(user_id=actor, status="disabled"))
    db.session.commit()
    result = aliases.resolve_stored_author(embedded_author="AUTH99")
    assert (result.status, result.author_user_id, result.reason) == ("unresolved", None, "inactive_author")

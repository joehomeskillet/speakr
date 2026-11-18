"""Approved destination aliases and collision policy.

BT-EXP-006 and WB-014 stay not_run. This file does not compare a reference
workstation and does not drive a browser picker. It checks the server-side
slice: alias rights, contained names, visible collisions and write failures.
A receipt never claims the file was stored on the client.
"""

import errno
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.clinic.output import OutputNameContext, expand_filename
from src.clinic.output_destinations import (
    POLICY_FAIL,
    POLICY_UNIQUE,
    POLICY_VERSION,
    OUTCOME_COLLISION,
    OUTCOME_CREATED,
    OUTCOME_REJECTED,
    OUTCOME_WRITE_ERROR,
    ApprovedAlias,
    DestinationError,
    PlaceRequest,
    place_export,
    resolve_destination,
)

PLAIN = OutputNameContext(author="Meier", title="Austritt")
HOSTILE = OutputNameContext(author="Müller:Arzt", title="Ärzt/Bericht*")
WHEN = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def _alias(root, **kwargs):
    fields = dict(
        name="workflow",
        root=str(root),
        actions=frozenset({"export"}),
        profiles=frozenset({"managed_web_online"}),
        version=2,
        kind="workflow",
        label="Workflow-Ablage",
    )
    fields.update(kwargs)
    return ApprovedAlias(**fields)


def _request(alias, **kwargs):
    alias_name = kwargs.pop("alias_name", alias.name)
    fields = dict(
        alias=alias_name,
        alias_version=alias.version,
        action="export",
        profile="managed_web_online",
        context=PLAIN,
        policy=POLICY_FAIL,
        content=b"dokument",
        actor_user_id=7,
        pattern="%A - %T",
        extension="rtf",
    )
    fields.update(kwargs)
    return PlaceRequest(**fields)


def _catalog(alias):
    return {alias.name: alias}


def test_resolve_uses_positional_placeholders_and_does_not_write(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    resolved = resolve_destination(_catalog(alias), _request(alias, pattern="%T - %A"))
    assert resolved.filename == expand_filename("%T - %A", PLAIN, extension="rtf")
    assert resolved.filename == "Austritt - Meier.rtf"
    assert resolved.relative_path == resolved.filename
    assert resolved.label == "Workflow-Ablage"
    assert resolved.local_save_proven is False
    assert list(root.iterdir()) == []


def test_place_hostile_name_stays_inside_alias(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root, kind="named", label="Befundablage", name="befund")
    request = _request(alias, context=HOSTILE, content=b"inhalt")
    receipt = place_export(_catalog(alias), request, now=WHEN)
    expected = expand_filename("%A - %T", HOSTILE, extension="rtf")
    assert receipt.outcome == OUTCOME_CREATED
    assert receipt.reason == "created"
    assert receipt.filename == expected
    assert receipt.relative_path == expected
    assert "/" not in expected and "\\" not in expected
    assert "Müller" in expected and "Ärzt" in expected
    assert (root / expected).read_bytes() == b"inhalt"
    assert receipt.sha256 is not None
    assert receipt.size == len(b"inhalt")
    assert receipt.local_save_proven is False
    assert receipt.label == "Befundablage"
    assert receipt.at == WHEN
    assert not (tmp_path / "Bericht").exists()


def test_manual_name_is_literal_and_sanitised(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    receipt = place_export(
        _catalog(alias),
        _request(alias, pattern=None, manual_name="Ärzt:in * %A", extension="txt"),
        now=WHEN,
    )
    expected = expand_filename("Ärzt:in * %%A", PLAIN, extension="txt")
    assert receipt.outcome == OUTCOME_CREATED
    assert receipt.filename == expected
    assert "Meier" not in receipt.filename
    assert (root / expected).is_file()


@pytest.mark.parametrize(
    "pattern",
    ["../%T", "%A/../../etc", "/var/ablage/%T", "C:\\secret\\%A", ".."],
)
def test_traversal_in_the_name_is_rejected(tmp_path, pattern):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    audit = []
    receipt = place_export(_catalog(alias), _request(alias, pattern=pattern), audit=audit, now=WHEN)
    assert receipt.outcome == OUTCOME_REJECTED
    assert receipt.reason == "traversal_rejected"
    assert receipt.filename is None
    assert audit == [receipt]
    assert list(root.iterdir()) == []


def test_client_path_is_rejected_even_when_it_looks_local(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    for client_path in ("/tmp/evil.rtf", "notes.rtf", ""):
        receipt = place_export(_catalog(alias), _request(alias, client_path=client_path), now=WHEN)
        assert receipt.outcome == OUTCOME_REJECTED
        assert receipt.reason == "client_path_rejected"
    assert list(root.iterdir()) == []


def test_placeholder_values_cannot_escape_the_alias(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    context = OutputNameContext(author="../../etc", title="passwd")
    receipt = place_export(
        _catalog(alias),
        _request(alias, context=context, pattern="%A_%T", extension="txt", content=b"ok"),
        now=WHEN,
    )
    expected = expand_filename("%A_%T", context, extension="txt")
    assert receipt.outcome == OUTCOME_CREATED
    assert receipt.filename == expected
    assert (root / expected).read_bytes() == b"ok"
    assert not (tmp_path / "etc").exists()


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"profile": "byod_internal"}, "profile_denied"),
        ({"profile": "import_agent"}, "profile_denied"),
        ({"action": "delete"}, "action_denied"),
        ({"alias_version": 1}, "stale_version"),
        ({"alias_version": 9}, "stale_version"),
        ({"alias_name": "missing"}, "unknown_alias"),
        ({"policy": "overwrite"}, "unknown_policy"),
        ({"pattern": "%Q"}, "bad_filename"),
        ({"pattern": None, "manual_name": None}, "name_required"),
        ({"pattern": "%A", "manual_name": "auch"}, "name_required"),
        ({"actor_user_id": True}, "bad_actor"),
        ({"actor_user_id": 0}, "bad_actor"),
        ({"content": "text"}, "bad_content"),
    ],
)
def test_unauthorized_stale_and_bad_names_write_nothing(tmp_path, kwargs, reason):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    receipt = place_export(_catalog(alias), _request(alias, **kwargs), now=WHEN)
    assert receipt.outcome == OUTCOME_REJECTED
    assert receipt.reason == reason
    assert receipt.local_save_proven is False
    assert list(root.iterdir()) == []


def test_stale_version_then_current_version_retries(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root, version=3)
    audit = []
    catalog = _catalog(alias)
    first = place_export(catalog, _request(alias, alias_version=2), audit=audit, now=WHEN)
    second = place_export(catalog, _request(alias, alias_version=3, content=b"neu"), audit=audit, now=WHEN)
    assert first.reason == "stale_version"
    assert second.outcome == OUTCOME_CREATED
    assert (root / second.filename).read_bytes() == b"neu"
    assert [item.outcome for item in audit] == [OUTCOME_REJECTED, OUTCOME_CREATED]


def test_fail_policy_keeps_the_first_bytes(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    catalog = _catalog(alias)
    first = place_export(catalog, _request(alias, content=b"eins"), now=WHEN)
    second = place_export(catalog, _request(alias, content=b"zwei"), now=WHEN)
    assert first.outcome == OUTCOME_CREATED
    assert second.outcome == OUTCOME_COLLISION
    assert second.reason == "name_taken"
    assert second.filename is None
    assert (root / first.filename).read_bytes() == b"eins"
    assert list(root.iterdir()) == [root / first.filename]


def test_version_policy_adds_the_next_free_name(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    catalog = _catalog(alias)
    first = place_export(catalog, _request(alias, policy=POLICY_VERSION, content=b"eins"), now=WHEN)
    second = place_export(catalog, _request(alias, policy=POLICY_VERSION, content=b"zwei"), now=WHEN)
    assert first.filename == "Meier - Austritt.rtf"
    assert second.outcome == OUTCOME_CREATED
    assert second.reason == POLICY_VERSION
    assert second.filename == "Meier - Austritt_v2.rtf"
    assert (root / first.filename).read_bytes() == b"eins"
    assert (root / second.filename).read_bytes() == b"zwei"


def test_unique_name_policy_suffixes_only_the_collision(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    catalog = _catalog(alias)
    place_export(catalog, _request(alias, content=b"eins"), now=WHEN)
    receipt = place_export(
        catalog,
        _request(alias, policy=POLICY_UNIQUE, content=b"zwei"),
        now=WHEN,
        token_factory=lambda: "abcd1234",
    )
    assert receipt.outcome == OUTCOME_CREATED
    assert receipt.reason == POLICY_UNIQUE
    assert receipt.filename == "Meier - Austritt_abcd1234.rtf"
    assert (root / "Meier - Austritt.rtf").read_bytes() == b"eins"
    assert (root / receipt.filename).read_bytes() == b"zwei"


def test_missing_directory_is_visible_and_retry_creates(tmp_path):
    root = tmp_path / "out"
    alias = _alias(root)
    catalog = _catalog(alias)
    blocked = place_export(catalog, _request(alias, content=b"spaeter"), now=WHEN)
    assert blocked.outcome == OUTCOME_WRITE_ERROR
    assert blocked.reason == "not_a_directory"
    assert not root.exists()
    root.mkdir()
    stored = place_export(catalog, _request(alias, content=b"spaeter"), now=WHEN)
    assert stored.outcome == OUTCOME_CREATED
    assert (root / stored.filename).read_bytes() == b"spaeter"


def test_short_write_removes_the_partial_file(tmp_path, monkeypatch):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    real_write = os.write
    calls = {"n": 0}

    def flaky(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EIO, "disk")
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", flaky)
    failed = place_export(_catalog(alias), _request(alias, content=b"kaputt"), now=WHEN)
    assert failed.outcome == OUTCOME_WRITE_ERROR
    assert failed.reason == "write_failed"
    assert list(root.iterdir()) == []
    stored = place_export(_catalog(alias), _request(alias, content=b"kaputt"), now=WHEN)
    assert stored.outcome == OUTCOME_CREATED
    assert (root / stored.filename).read_bytes() == b"kaputt"


def test_existing_symlink_is_not_followed(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    link = root / "Meier - Austritt.rtf"
    link.symlink_to(outside)
    alias = _alias(root)
    receipt = place_export(_catalog(alias), _request(alias, content=b"neu"), now=WHEN)
    assert receipt.outcome in {OUTCOME_COLLISION, OUTCOME_WRITE_ERROR}
    assert receipt.filename is None
    assert link.is_symlink()
    assert outside.read_bytes() == b"secret"


def test_file_as_root_is_not_written(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_bytes(b"stay")
    alias = _alias(root)
    receipt = place_export(_catalog(alias), _request(alias), now=WHEN)
    assert receipt.outcome == OUTCOME_WRITE_ERROR
    assert receipt.reason == "not_a_directory"
    assert root.read_bytes() == b"stay"


def test_concurrent_fail_policy_keeps_one_complete_body(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    catalog = _catalog(alias)
    barrier = threading.Barrier(2)
    results = []
    errors = []
    lock = threading.Lock()

    def run(payload):
        try:
            barrier.wait(5)
            receipt = place_export(catalog, _request(alias, content=payload), now=WHEN)
            with lock:
                results.append(receipt)
        except Exception as exc:  # surface the thread failure in the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(payload,)) for payload in (b"one", b"two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert errors == []
    created = [item for item in results if item.outcome == OUTCOME_CREATED]
    collided = [item for item in results if item.outcome == OUTCOME_COLLISION]
    assert len(created) == 1 and len(collided) == 1
    assert collided[0].reason == "name_taken"
    assert (root / created[0].filename).read_bytes() in {b"one", b"two"}
    assert list(root.iterdir()) == [root / created[0].filename]


def test_concurrent_version_policy_keeps_both_bodies(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    catalog = _catalog(alias)
    barrier = threading.Barrier(2)
    results = []
    errors = []
    lock = threading.Lock()

    def run(payload):
        try:
            barrier.wait(5)
            receipt = place_export(
                catalog, _request(alias, policy=POLICY_VERSION, content=payload), now=WHEN
            )
            with lock:
                results.append(receipt)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(payload,)) for payload in (b"one", b"two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert errors == []
    assert sorted(item.outcome for item in results) == [OUTCOME_CREATED, OUTCOME_CREATED]
    names = {item.filename for item in results}
    assert len(names) == 2
    bodies = {(root / name).read_bytes() for name in names}
    assert bodies == {b"one", b"two"}


def test_resolve_refusal_does_not_create_a_file(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    with pytest.raises(DestinationError) as info:
        resolve_destination(_catalog(alias), _request(alias, client_path="/tmp/x.rtf"))
    assert info.value.code == "client_path_rejected"
    assert list(root.iterdir()) == []


def test_relative_alias_root_is_rejected():
    alias = ApprovedAlias(
        name="workflow",
        root="relative/out",
        actions=frozenset({"export"}),
        profiles=frozenset({"managed_web_online"}),
        version=2,
        kind="workflow",
        label="Workflow-Ablage",
    )
    receipt = place_export(_catalog(alias), _request(alias), now=WHEN)
    assert receipt.outcome == OUTCOME_REJECTED
    assert receipt.reason == "alias_root_invalid"
    assert not Path("relative/out").exists()


def test_unsafe_unique_token_adds_no_file(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    catalog = _catalog(alias)
    place_export(catalog, _request(alias, content=b"eins"), now=WHEN)
    receipt = place_export(
        catalog,
        _request(alias, policy=POLICY_UNIQUE, content=b"zwei"),
        now=WHEN,
        token_factory=lambda: "../nope",
    )
    assert receipt.outcome == OUTCOME_REJECTED
    assert receipt.reason == "bad_token"
    assert (root / "Meier - Austritt.rtf").read_bytes() == b"eins"
    assert list(root.iterdir()) == [root / "Meier - Austritt.rtf"]


def test_version_cap_is_a_visible_collision(tmp_path, monkeypatch):
    monkeypatch.setattr("src.clinic.output_destinations._MAX_VERSION", 1)
    root = tmp_path / "out"
    root.mkdir()
    alias = _alias(root)
    catalog = _catalog(alias)
    first = place_export(catalog, _request(alias, policy=POLICY_VERSION, content=b"eins"), now=WHEN)
    second = place_export(catalog, _request(alias, policy=POLICY_VERSION, content=b"zwei"), now=WHEN)
    assert first.outcome == OUTCOME_CREATED
    assert second.outcome == OUTCOME_COLLISION
    assert second.reason == "names_exhausted"
    assert (root / first.filename).read_bytes() == b"eins"
    assert list(root.iterdir()) == [root / first.filename]


def test_same_filename_in_two_aliases_does_not_collide(tmp_path):
    workflow = _alias(tmp_path / "workflow")
    named = _alias(tmp_path / "named", name="named", kind="named", label="Named")
    (tmp_path / "workflow").mkdir()
    (tmp_path / "named").mkdir()
    catalog = {workflow.name: workflow, named.name: named}
    first = place_export(catalog, _request(workflow, content=b"w"), now=WHEN)
    second = place_export(catalog, _request(named, content=b"n"), now=WHEN)
    assert first.outcome == OUTCOME_CREATED
    assert second.outcome == OUTCOME_CREATED
    assert (tmp_path / "workflow" / first.filename).read_bytes() == b"w"
    assert (tmp_path / "named" / second.filename).read_bytes() == b"n"

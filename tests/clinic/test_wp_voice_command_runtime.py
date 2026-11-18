"""Synthetic PAR-VOC-008 web command runtime contribution.

The unchanged BT-VOC-008 still requires separate reference/browser evidence.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clinic import vocabulary
from src.clinic.policy import Deny, Grant, RecordingFacts, Subject
from src.clinic.voice_command_runtime import (
    Confirmation, VoiceAction, VoiceCommandError, catalog_version,
    evaluate_voice_command, execute_voice_command,
)

NOW = datetime(2026, 11, 18, 13, 0, tzinfo=timezone.utc)
RECORDING = RecordingFacts(recording_id=5, owner_id=11)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'voice_runtime.db'}")
    vocabulary.create_tables(bind=engine)
    with sessionmaker(bind=engine)() as db_session:
        yield db_session
    engine.dispose()


def command(session, phrase, kind="insert_text", payload="Befund", **options):
    return vocabulary.add_voice_command(
        "user", phrase, action_type=kind, action_payload=payload,
        user_id=11, session=session, **options,
    )


def context(session, phrase, **overrides):
    values = dict(
        user_id=11, session=session, subject=Subject(11), recording=RECORDING,
        now=NOW, request_id="event-1", expected_version=catalog_version(11, session=session),
        lease_check=lambda: True,
    )
    values.update(overrides)
    return phrase, values


def error_code(call, expected):
    with pytest.raises(VoiceCommandError) as raised:
        call()
    assert raised.value.code == expected


def test_hooks_macro_order_scope_resolution_and_idempotent_plan(session):
    vocabulary.add_voice_command(
        "pool", "Teil", action_payload="Pool", pool_id="pool-1", session=session,
    )
    command(session, "Teil", payload="Persönlich")
    command(session, "Markieren", "app_command", "insert_marker")
    root = command(
        session, "Bericht", "macro", json.dumps(["Teil", "Markieren"]),
        before_hook="focus_editor", after_hook="next_marker",
    )
    session.commit()
    phrase, args = context(session, "bericht", pool_ids=["pool-1"])
    args["expected_version"] = catalog_version(11, ["pool-1"], session=session)
    batches = []

    def apply(plan):
        batches.append(plan)
        return plan.actions

    actions = execute_voice_command(phrase, apply=apply, **args)
    assert actions == (
        VoiceAction("app_command", "focus_editor"),
        VoiceAction("insert_text", "Persönlich"),
        VoiceAction("app_command", "insert_marker"),
        VoiceAction("app_command", "next_marker"),
    )
    assert len(batches) == 1
    assert batches[0].command_id == root.id
    assert (batches[0].actor_id, batches[0].recording_id) == (11, 5)
    assert evaluate_voice_command(phrase, **args) == batches[0]


def test_disabled_and_other_user_commands_are_unavailable(session):
    vocabulary.add_voice_command(
        "pool", "Deaktiviert", action_payload="Nicht ausführen",
        pool_id="pool-1", session=session,
    )
    command(session, "Deaktiviert", status="disabled")
    vocabulary.add_voice_command("user", "Fremd", user_id=99, session=session)
    session.commit()
    for phrase in ("Deaktiviert", "Fremd", "Nicht vorhanden"):
        name, args = context(session, phrase, pool_ids=["pool-1"])
        args["expected_version"] = catalog_version(11, ["pool-1"], session=session)
        error_code(lambda: evaluate_voice_command(name, **args), "unknown_command")


@pytest.mark.parametrize("kind,payload", [
    ("eval", "1+1"), ("shell", "whoami"), ("os_hotkey", "Alt+F4"),
    ("window_automation", "close"), ("python", "print('x')"),
    ("app_command", "Ctrl+Alt+Delete"),
    ("app_command", "os.execute"), ("app_command", "window.close"),
])
def test_executable_and_unlisted_actions_rejected_before_dispatch(session, kind, payload):
    command(session, "Unsicher", kind, payload)
    session.commit()
    phrase, args = context(session, "Unsicher")
    batches = []
    error_code(
        lambda: execute_voice_command(phrase, apply=batches.append, **args),
        "forbidden_action",
    )
    assert batches == []


def test_inserted_text_is_always_literal(session):
    literal = "__import__('os').system('echo nope')"
    command(session, "Zitat", payload=literal)
    session.commit()
    phrase, args = context(session, "Zitat")
    assert evaluate_voice_command(phrase, **args).actions == (
        VoiceAction("insert_text", literal),
    )


@pytest.mark.parametrize("payload,code", [
    ('["Kreis"]', "macro_cycle"),
    ('["Nicht vorhanden"]', "unknown_reference"),
    ('["focus_editor", "Alt+F4"]', "unknown_reference"),
    ('{"invalid": true}', "invalid_macro"),
    ('[23]', "invalid_reference"),
    ('not json', "invalid_macro"),
])
def test_bad_expansion_never_dispatches_even_after_valid_prefix(session, payload, code):
    command(session, "Kreis", "macro", payload, before_hook="focus_editor")
    session.commit()
    phrase, args = context(session, "Kreis")
    batches = []
    error_code(lambda: execute_voice_command(phrase, apply=batches.append, **args), code)
    assert batches == []


def test_mutual_cycle_depth_and_step_limits(session):
    command(session, "A", "macro", '["B"]')
    command(session, "B", "macro", '["A"]')
    session.commit()
    phrase, args = context(session, "A")
    error_code(lambda: evaluate_voice_command(phrase, **args), "macro_cycle")

    for index in range(10):
        command(session, f"D{index}", "macro", json.dumps([f"D{index + 1}"]))
    command(session, "D10")
    session.commit()
    phrase, args = context(session, "D0")
    error_code(lambda: evaluate_voice_command(phrase, **args), "macro_too_deep")

    command(session, "Kurz", payload="X")
    command(session, "Zu viele", "macro", json.dumps(["Kurz"] * 65))
    session.commit()
    phrase, args = context(session, "Zu viele")
    error_code(lambda: evaluate_voice_command(phrase, **args), "invalid_macro")

    command(session, "Zwei Schritte", payload="X", before_hook="focus_editor")
    command(session, "Schrittgrenze", "macro", json.dumps(["Zwei Schritte"] * 33))
    session.commit()
    phrase, args = context(session, "Schrittgrenze")
    error_code(lambda: evaluate_voice_command(phrase, **args), "too_many_steps")


def test_length_limit_counts_entire_expansion(session):
    command(session, "Block", payload="X" * 5000)
    command(session, "Doppelt", "macro", '["Block", "Block"]')
    session.commit()
    phrase, args = context(session, "Doppelt")
    error_code(lambda: evaluate_voice_command(phrase, **args), "text_too_long")


def test_current_permissions_deny_and_lease_are_required(session):
    command(session, "Text")
    session.commit()
    phrase, args = context(session, "Text")
    batches = []

    for changes, code in (
        ({"recording": RecordingFacts(5, owner_id=22)}, "access_denied"),
        ({"subject": Subject(11, denies=(Deny(recording_ids=frozenset({5})),))}, "access_denied"),
        ({"subject": Subject(99)}, "invalid_actor"),
        ({"lease_check": None}, "lease_required"),
        ({"lease_check": lambda: False}, "lease_required"),
    ):
        error_code(
            lambda: execute_voice_command(phrase, apply=batches.append, **{**args, **changes}),
            code,
        )
    assert batches == []


def test_stale_command_version_and_retry_after_failure(session):
    command(session, "Satz", payload="Version 1")
    session.commit()
    phrase, args = context(session, "Satz")
    command(session, "Satz", payload="Version 2")
    session.commit()
    batches = []
    error_code(
        lambda: execute_voice_command(phrase, apply=batches.append, **args),
        "stale_catalog",
    )
    assert batches == []
    args["expected_version"] = catalog_version(11, session=session)
    attempts = []
    committed = {}

    def atomic_apply(plan):
        attempts.append(plan.request_id)
        if len(attempts) == 1:
            raise RuntimeError("retryable transaction failure")
        return committed.setdefault(plan.request_id, plan.actions)

    with pytest.raises(RuntimeError, match="retryable"):
        execute_voice_command(phrase, apply=atomic_apply, **args)
    first = execute_voice_command(phrase, apply=atomic_apply, **args)
    assert execute_voice_command(phrase, apply=atomic_apply, **args) == first
    assert first == (VoiceAction("insert_text", "Version 2"),)
    assert attempts == ["event-1"] * 3
    assert len(committed) == 1


def test_sensitive_actions_require_current_rights_and_server_confirmation(session):
    command(session, "Text", payload="Vorher")
    command(session, "Abgeben", "app_command", "release_dictation")
    root = command(session, "Fertig", "macro", '["Text", "Abgeben"]')
    session.commit()
    phrase, args = context(session, "Fertig")
    batches = []
    error_code(
        lambda: execute_voice_command(phrase, apply=batches.append, **args),
        "access_denied",
    )
    assert batches == []

    args["subject"] = Subject(11, grants=(Grant("own", frozenset({"release"})),))
    error_code(
        lambda: execute_voice_command(phrase, apply=batches.append, **args),
        "confirmation_required",
    )
    confirmed = Confirmation(
        "event-1", root.id, args["expected_version"], 11, 5,
        "release_dictation", NOW - timedelta(seconds=30),
    )
    args["confirmations"] = {"release_dictation": confirmed}
    error_code(
        lambda: execute_voice_command(phrase, apply=batches.append, **args),
        "confirmation_required",
    )  # An unverified client-supplied timestamp is insufficient.
    args["verify_confirmation"] = lambda item: item == confirmed
    plan = execute_voice_command(phrase, apply=lambda item: item, **args)
    assert plan.actions == (
        VoiceAction("insert_text", "Vorher"),
        VoiceAction("app_command", "release_dictation"),
    )
    assert batches == []

    for invalid in (
        None,
        Confirmation("different", root.id, args["expected_version"], 11, 5, "release_dictation", NOW),
        Confirmation("event-1", root.id, args["expected_version"], 22, 5, "release_dictation", NOW),
        Confirmation("event-1", root.id, args["expected_version"], 11, 5, "release_dictation", NOW - timedelta(minutes=2)),
        Confirmation("event-1", root.id, args["expected_version"], 11, 5, "release_dictation", NOW + timedelta(seconds=1)),
    ):
        error_code(
            lambda: execute_voice_command(
                phrase, apply=batches.append, **{**args, "confirmations": {"release_dictation": invalid}},
            ),
            "confirmation_required",
        )
    assert batches == []


def test_send_action_needs_independent_export_grant_and_confirmation(session):
    root = command(session, "Senden", "app_command", "send_dictation")
    session.commit()
    phrase, args = context(session, "Senden")
    conf = Confirmation("event-1", root.id, args["expected_version"], 11, 5, "send_dictation", NOW)
    args["confirmations"] = {"send_dictation": conf}
    args["verify_confirmation"] = lambda value: value == conf
    error_code(lambda: evaluate_voice_command(phrase, **args), "access_denied")
    args["subject"] = Subject(11, grants=(Grant("own", frozenset({"export"})),))
    assert evaluate_voice_command(phrase, **args).actions == (
        VoiceAction("app_command", "send_dictation"),
    )


def test_release_cannot_be_expanded_twice_with_one_confirmation(session):
    command(session, "Auslösen", "app_command", "release_dictation")
    command(session, "Doppelte Freigabe", "macro", '["Auslösen", "Auslösen"]')
    session.commit()
    phrase, args = context(session, "Doppelte Freigabe")
    error_code(
        lambda: evaluate_voice_command(phrase, **args),
        "repeated_sensitive_action",
    )


def test_late_macro_error_cannot_partially_release_or_send(session):
    command(session, "Freigeben", "app_command", "release_dictation")
    command(session, "Senden", "app_command", "send_dictation")
    command(session, "Fehlerhafte Folge", "macro", '["Freigeben", "Senden", "Fehlt"]')
    session.commit()
    phrase, args = context(session, "Fehlerhafte Folge")
    batches = []
    error_code(
        lambda: execute_voice_command(phrase, apply=batches.append, **args),
        "unknown_reference",
    )
    assert batches == []

"""Synthetic service evidence for BT-WF-014 (workflow notification slice)."""

import pytest
from flask import Flask

from src.clinic import workflow
from src.clinic.models import ClinicDictation
from src.clinic.workflow_notifications import KIND, emit, event_key
from src.database import db
from src.models import Notification, Recording, User
from src.services import notifications


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    with app.app_context():
        db.create_all()
        users = {}
        for name in ("author", "typist", "acceptor", "other"):
            users[name] = User(username=name, email=f"{name}@example.invalid", password="x")
            db.session.add(users[name])
        db.session.flush()
        ids = {name: user.id for name, user in users.items()}
        recording = Recording(user_id=ids["author"], title="Synthetic")
        db.session.add(recording)
        db.session.flush()
        item = workflow.start(db.session(), "create", author_user_id=ids["author"],
                              pool_id="pool", assignee_user_id=ids["typist"],
                              acceptance_required=True)
        db.session.add(ClinicDictation(id=item.id, recording_id=recording.id,
                                       author_user_id=ids["author"]))
        for trigger in ("send_offline", "skip_recognition", "complete"):
            workflow.transition(db.session(), item.id, trigger)
        db.session.commit()
        state = {"visible": set(ids.values())}

        def send(event, actor="other", version=0, **kw):
            args = dict(actor_user_id=ids[actor], expected_version=version,
                        acceptor_ids=[ids["acceptor"]],
                        can_see=lambda uid, _row: uid in state["visible"])
            return emit(event, item.id, **(args | kw))

        yield item, ids, state, send


def _rows():
    return db.session.query(Notification).order_by(Notification.user_id).all()


def test_acceptance_request_notifies_acceptor_once_with_stable_key(case):
    item, ids, _, send = case
    first = send("acceptance_requested")
    assert first.event_key == event_key("acceptance_requested", item.id, 0)
    assert (first.created, first.existing) == ((ids["acceptor"],), ())
    again = send("acceptance_requested")
    assert (again.created, again.existing) == ((), (ids["acceptor"],))
    (row,) = _rows()
    assert (row.kind, row.dedupe_key, row.link) == (KIND, first.event_key, f"/clinic/dictations/{item.id}")
    assert row.params == {"title": "Abnahme angefordert", "body": "Das Diktat wartet auf Ihre Abnahme."}


def test_rejection_goes_to_current_assignee_only_and_text_is_free_of_details(case):
    item, ids, _, send = case
    workflow.transition(db.session(), item.id, "reject")
    db.session.commit()
    result = send("acceptance_rejected", actor="acceptor")
    assert result.created == (ids["typist"],)
    # Reassigned after the transition: the new assignee is routed, the old one is not.
    row = db.session.get(workflow.ClinicWorkflowDictation, item.id)
    row.assignee_user_id = ids["other"]
    db.session.commit()
    later = send("acceptance_rejected", actor="acceptor", version=0)
    assert later.created == (ids["other"],)
    with pytest.raises(workflow.WorkflowError, match="forbidden_context"):
        send("acceptance_rejected", actor="acceptor", context={"patient_name": "Muster"})


def test_recipient_without_current_visibility_is_dropped(case):
    _, ids, state, send = case
    state["visible"].discard(ids["acceptor"])
    result = send("acceptance_requested")
    assert (result.created, result.dropped) == ((), (ids["acceptor"],))
    assert _rows() == []


def test_rolled_back_transition_gives_no_false_completion(case):
    item, _, _, send = case
    with pytest.raises(workflow.WorkflowError, match="transition_not_durable"):
        send("completed")
    workflow.transition(db.session(), item.id, "accept")
    db.session.rollback()
    with pytest.raises(workflow.WorkflowError, match="transition_not_durable"):
        send("accepted")
    assert _rows() == []


def test_completion_notifies_author_and_stale_version_is_refused(case):
    item, ids, _, send = case
    for trigger in ("accept", "complete"):
        workflow.transition(db.session(), item.id, trigger)
    db.session.commit()
    result = send("completed", actor="typist")
    assert result.created == (ids["author"],)
    with pytest.raises(workflow.WorkflowError, match="stale_version"):
        send("completed", actor="typist", version=5)
    with pytest.raises(workflow.WorkflowError, match="not_found"):
        emit("completed", "missing", actor_user_id=ids["typist"], expected_version=0,
             acceptor_ids=[], can_see=lambda *_: True)


def test_retry_after_failed_store_delivers_only_missing_and_keeps_dismissal(case, monkeypatch):
    item, ids, _, send = case
    for trigger in ("accept",):
        workflow.transition(db.session(), item.id, trigger)
    db.session.commit()
    real = notifications.notify

    def flaky(kind, key, *, user_ids, **kw):
        if user_ids == [ids["typist"]]:
            return 0
        return real(kind, key, user_ids=user_ids, **kw)

    monkeypatch.setattr(notifications, "notify", flaky)
    first = send("accepted", actor="acceptor")
    assert (first.created, first.failed) == ((ids["author"],), (ids["typist"],))
    monkeypatch.setattr(notifications, "notify", real)
    notifications.dismiss(db.session.get(User, ids["author"]), _rows()[0].id)
    retry = send("accepted", actor="acceptor")
    assert (retry.created, retry.existing, retry.failed) == (
        (ids["typist"],), (ids["author"],), ())
    assert len(_rows()) == 2
    assert _rows()[0].dismissed_at is not None


def test_disabled_clinic_mode_and_unknown_event(case, monkeypatch):
    _, _, _, send = case
    with pytest.raises(workflow.WorkflowError, match="unknown_event"):
        send("nope")
    monkeypatch.delenv("CLINIC_MODE")
    with pytest.raises(workflow.WorkflowError, match="clinic_disabled"):
        send("acceptance_requested")

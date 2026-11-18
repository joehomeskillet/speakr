"""Server-side live drafts: several drafts, one recording holder, explicit discard.

PAR-LIVE-004 / BT-LIVE-004. The original manual oracle is not executed here.
These tests cover this slice: stable dictation ids, the one-open-session
rule, a pause on switch, phrase binding, confirmed archive, and the
unauthorized, stale-version and retry cases.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import app, db
from src.clinic import live_session as live
from src.clinic.draft_registry import DraftConflict, DraftError
from src.clinic.live_drafts import ARCHIVED, IDLE, LiveDraftError, LiveDrafts
from src.clinic.live_session import ClinicLiveSession
from src.clinic.models import ClinicDictation
from src.models import Recording, User

T0 = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture
def db_ctx():
    with app.app_context():
        ClinicLiveSession.__table__.create(db.engine, checkfirst=True)
        yield
        db.session.rollback()


def _user() -> User:
    suffix = uuid.uuid4().hex[:10]
    user = User(username="ld" + suffix, email=suffix + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    return user


def _dictation(author: User) -> ClinicDictation:
    recording = Recording(user_id=author.id, title="live-draft", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
    db.session.add(row)
    db.session.flush()
    return row


def _recording(owner_id: int) -> list[ClinicLiveSession]:
    return (
        db.session.query(ClinicLiveSession)
        .filter(
            ClinicLiveSession.author_user_id == owner_id,
            ClinicLiveSession.state == live.RECORDING,
        )
        .all()
    )


def test_bt_live_004_texts_and_audio_stay_on_their_draft(db_ctx):
    author = _user()
    book = LiveDrafts()
    first = _dictation(author)
    second = _dictation(author)
    draft_a = book.create(author.id, "Entwurf A", T0, first.id, "Text A")
    draft_b = book.create(author.id, "Entwurf B", at(1), second.id, "Text B")
    book.commit_audio(author.id, draft_a.id, "audio-a", at(1))
    book.commit_audio(author.id, draft_b.id, "audio-b", at(1))

    assert {item.id for item in book.list(author.id)} == {first.id, second.id}
    resumed_a = book.resume(author.id, draft_a.id, "tab", at(2))
    resumed_b = book.resume(author.id, draft_b.id, "tab", at(3))
    assert resumed_a.id == first.id and resumed_b.id == second.id
    assert resumed_b.text == "Text B"
    assert [item.state for item in book.list(author.id) if item.id == first.id] == [live.PAUSED]
    assert len(_recording(author.id)) == 1
    assert _recording(author.id)[0].dictation_id == second.id

    # A paused session still occupies the dictation: a second open row is refused.
    with pytest.raises(live.LiveSessionError) as info:
        live.start(first.id, author.id, at(4))
    assert info.value.reason == live.ACTIVE_SESSION

    phrase_b = book.append_phrase(
        author.id, draft_b.id, " Zusatz B", resumed_b.version, resumed_b.holder_token, "tab", at(5)
    )
    book.switch(author.id, draft_a.id, "tab", at(6))
    resumed_a = book.resume(author.id, draft_a.id, "tab", at(7))
    phrase_a = book.append_phrase(
        author.id, draft_a.id, " Zusatz A", resumed_a.version, resumed_a.holder_token, "tab", at(8)
    )
    assert phrase_a.text == "Text A Zusatz A"
    assert phrase_a.audio_ref == "audio-a"
    by_id = {item.id: item for item in book.list(author.id)}
    assert by_id[second.id].text == phrase_b.text == "Text B Zusatz B"
    assert by_id[second.id].audio_ref == "audio-b"

    cancelled = book.cancel(author.id, draft_a.id, at(9))
    assert cancelled.id == first.id
    assert cancelled.text == "Text A Zusatz A"
    assert cancelled.discarded_at is None
    assert any(item.id == first.id for item in book.list(author.id))
    saved = book.save(author.id, draft_a.id, "Text A gespeichert", cancelled.version, "tab", at(10))
    assert saved.text == "Text A gespeichert"
    assert saved.id == first.id

    with pytest.raises(DraftError) as refused:
        book.discard(author.id, draft_b.id, confirm=False, now=at(11))
    assert refused.value.code == "confirmation_required"
    assert any(item.id == second.id for item in book.list(author.id))

    archived = book.discard(author.id, draft_b.id, confirm=True, now=at(12))
    assert archived.state == ARCHIVED
    assert archived.audio_ref == "audio-b"
    assert book.original_audio(author.id, second.id) == "audio-b"
    assert {item.id for item in book.list(author.id)} == {first.id}
    assert book.list_archived(author.id)[0].text == "Text B Zusatz B"
    actions = [entry.action for entry in book.audit(author.id, second.id)]
    assert "audio_committed" in actions and "archived" in actions and "discard_refused" in actions
    ended = (
        db.session.query(ClinicLiveSession)
        .filter_by(dictation_id=second.id)
        .one()
    )
    assert ended.state == live.ENDED

    fresh = _dictation(author)
    restarted = book.create(author.id, "Entwurf C", at(13), fresh.id, "Text C")
    assert restarted.id == fresh.id
    assert restarted.id not in (first.id, second.id)
    assert "Text A" not in restarted.text and "Text B" not in restarted.text
    assert book.original_audio(author.id, fresh.id) is None


def test_switch_cannot_append_the_old_phrase_to_the_new_draft(db_ctx):
    author = _user()
    book = LiveDrafts()
    first = _dictation(author)
    second = _dictation(author)
    draft_a = book.create(author.id, "Entwurf A", T0, first.id, "Text A")
    draft_b = book.create(author.id, "Entwurf B", T0, second.id, "Text B")
    live_a = book.resume(author.id, draft_a.id, "tab", at(1))
    book.switch(author.id, draft_b.id, "tab", at(2))

    with pytest.raises(LiveDraftError) as old_target:
        book.append_phrase(
            author.id, draft_a.id, " Satz", live_a.version, live_a.holder_token, "tab", at(3)
        )
    assert old_target.value.code == "wrong_draft"
    with pytest.raises(LiveDraftError) as new_target:
        book.append_phrase(
            author.id, draft_b.id, " Satz", 1, live_a.holder_token, "tab", at(4)
        )
    assert new_target.value.code == "wrong_draft"
    by_id = {item.id: item for item in book.list(author.id)}
    assert by_id[first.id].text == "Text A"
    assert by_id[second.id].text == "Text B"
    assert by_id[second.id].state == IDLE

    live_b = book.resume(author.id, draft_b.id, "tab", at(5))
    with pytest.raises(LiveDraftError) as stale:
        book.append_phrase(
            author.id, draft_b.id, " Satz", live_b.version, live_a.holder_token, "tab", at(6)
        )
    assert stale.value.code == "stale_holder"
    assert {item.id: item.text for item in book.list(author.id)} == {
        first.id: "Text A",
        second.id: "Text B",
    }

    written = book.append_phrase(
        author.id, draft_b.id, " nur B", live_b.version, live_b.holder_token, "tab", at(7)
    )
    assert written.text == "Text B nur B"
    assert {item.text for item in book.list(author.id) if item.id == first.id} == {"Text A"}


def test_unauthorized_caller_leaves_the_draft_unchanged(db_ctx):
    author = _user()
    other = _user()
    book = LiveDrafts()
    row = _dictation(author)
    draft = book.create(author.id, "Entwurf", T0, row.id, "Geheim")
    book.commit_audio(author.id, draft.id, "audio-geheim", at(1))
    for call in (
        lambda: book.resume(other.id, draft.id, "tab", at(2)),
        lambda: book.append_phrase(other.id, draft.id, " x", 1, 1, "tab", at(2)),
        lambda: book.discard(other.id, draft.id, confirm=True, now=at(2)),
        lambda: book.original_audio(other.id, draft.id),
        lambda: book.audit(other.id, draft.id),
        lambda: book.switch(other.id, draft.id, "tab", at(2)),
    ):
        with pytest.raises(LiveDraftError) as info:
            call()
        assert info.value.code == "unauthorized"
    kept = book.list(author.id)[0]
    assert kept.text == "Geheim" and kept.state == IDLE and kept.audio_ref == "audio-geheim"
    assert book.list(other.id) == []


def test_stale_version_and_retries(db_ctx):
    author = _user()
    book = LiveDrafts()
    row = _dictation(author)
    draft = book.create(author.id, "Entwurf", T0, row.id, "Start")
    live_draft = book.resume(author.id, draft.id, "tab", at(1))
    again = book.resume(author.id, draft.id, "tab", at(2))
    assert again.holder_token == live_draft.holder_token
    assert len(_recording(author.id)) == 1
    sessions = db.session.query(ClinicLiveSession).filter_by(dictation_id=row.id).all()
    assert len(sessions) == 1

    with pytest.raises(DraftConflict) as stale:
        book.append_phrase(
            author.id, draft.id, " neu", 99, live_draft.holder_token, "tab", at(3)
        )
    assert stale.value.current_text == "Start"
    assert book.list(author.id)[0].text == "Start"
    written = book.append_phrase(
        author.id, draft.id, " neu", live_draft.version, live_draft.holder_token, "tab", at(4)
    )
    assert written.text == "Start neu"

    with pytest.raises(LiveDraftError) as bad_audio:
        book.commit_audio(author.id, draft.id, "  ", at(5))
    assert bad_audio.value.code == "invalid_audio"
    assert book.original_audio(author.id, draft.id) is None
    assert book.commit_audio(author.id, draft.id, "audio-1", at(6)) == "audio-1"
    assert book.commit_audio(author.id, draft.id, "audio-1", at(7)) == "audio-1"
    with pytest.raises(LiveDraftError) as replaced:
        book.commit_audio(author.id, draft.id, "audio-2", at(8))
    assert replaced.value.code == "audio_committed"
    assert book.original_audio(author.id, draft.id) == "audio-1"

    missing = book.create(author.id, "Ohne Zeile", at(9), "missing-dictation", "bleibt")
    with pytest.raises(live.LiveSessionError) as absent:
        book.resume(author.id, missing.id, "tab", at(10))
    assert absent.value.reason == live.NOT_FOUND
    kept = {item.id: item for item in book.list(author.id)}
    assert kept[missing.id].text == "bleibt" and kept[missing.id].state == IDLE
    assert kept[row.id].text == "Start neu" and kept[row.id].state == live.RECORDING
    retried = book.append_phrase(
        author.id, draft.id, " bleibt", written.version, live_draft.holder_token, "tab", at(11)
    )
    assert retried.text == "Start neu bleibt"

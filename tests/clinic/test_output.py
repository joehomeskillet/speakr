"""Output service: file name placeholders and append-only output receipts.

Acceptance catalog entries stay not_run. The receipt tests use the real
throwaway database of the test app, no database mocks.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.app import app, db
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.output import (
    CHANNEL_CLIPBOARD,
    CHANNEL_TEXT,
    FORMAT_FORMATTED,
    ClinicOutputReceipt,
    MissingPlaceholderValue,
    OutputNameContext,
    OutputNameError,
    OutputReceiptError,
    OutputReceiptImmutable,
    UnknownPlaceholder,
    expand_filename,
    is_current_output,
    list_outputs,
    record_output,
    sanitize_stem,
)
from src.clinic.revisions import add_asr_revision, add_human_revision
from src.models import Recording, User

CTX = OutputNameContext(
    author="Meier",
    title="Austritt",
    created_on=date(2026, 11, 18),
    service_staff="Keller",
    document_creator="Huber",
)


# --- file name patterns ----------------------------------------------------

def test_a_and_t_follow_pattern_position():
    assert expand_filename("%A - %T", CTX) == "Meier - Austritt"
    assert expand_filename("%T - %A", CTX) == "Austritt - Meier"
    assert expand_filename("%T%A%T", CTX) == "AustrittMeierAustritt"


def test_all_placeholders_and_literal_percent():
    name = expand_filename("%A_%T_%D_%S_%C_100%%", CTX, extension="rtf")
    assert name == "Meier_Austritt_2026-11-18_Keller_Huber_100%.rtf"


def test_date_from_aware_datetime_uses_zurich_day():
    late_utc = datetime(2026, 11, 17, 23, 30, tzinfo=timezone.utc)  # 00:30 next day in Zurich
    ctx = OutputNameContext(created_on=late_utc)
    assert expand_filename("%D", ctx) == "2026-11-18"


def test_naive_datetime_is_rejected():
    ctx = OutputNameContext(created_on=datetime(2026, 11, 18, 8, 0))
    with pytest.raises(OutputNameError):
        expand_filename("%D", ctx)


@pytest.mark.parametrize("pattern,token", [("%X", "%X"), ("%a", "%a"), ("%T-%Z", "%Z"), ("abc%", "%")])
def test_unknown_placeholder_is_a_typed_error(pattern, token):
    with pytest.raises(UnknownPlaceholder) as info:
        expand_filename(pattern, CTX)
    assert info.value.token == token
    assert isinstance(info.value, OutputNameError)


@pytest.mark.parametrize("value", [None, ""])
def test_known_placeholder_without_value_is_an_error(value):
    ctx = OutputNameContext(author=value, title="T")
    with pytest.raises(MissingPlaceholderValue) as info:
        expand_filename("%A - %T", ctx)
    assert info.value.token == "%A"


def test_non_str_value_is_rejected():
    with pytest.raises(OutputNameError):
        expand_filename("%A", OutputNameContext(author=42))


def test_values_cannot_add_path_segments_or_forbidden_characters():
    ctx = OutputNameContext(author="../../etc", title='a<b>:"c"|d?*')
    name = expand_filename("%A - %T", ctx)
    assert "/" not in name and "\\" not in name
    assert not set('<>:"|?*') & set(name)
    # leading dots of "../../etc" are stripped, separators become "_"
    assert name == "_.._etc - a_b___c__d__"


def test_control_and_format_characters_are_replaced():
    ctx = OutputNameContext(title="a\x00b\nc‮d\ud800e")
    assert expand_filename("%T", ctx) == "a_b c_d_e"


def test_whitespace_collapses_and_edges_are_trimmed():
    assert sanitize_stem("  a   b\t\tc . ") == "a b c"


def test_leading_and_trailing_dots_and_spaces_are_removed():
    assert sanitize_stem("...name. . ") == "name"


@pytest.mark.parametrize("bad", ["", "   ", "...", " . "])
def test_empty_result_is_an_error(bad):
    with pytest.raises(OutputNameError):
        sanitize_stem(bad)


@pytest.mark.parametrize("stem", ["con", "NUL", "Com1", "lpt9", "aux.txt", "CON .x"])
def test_windows_device_names_are_prefixed(stem):
    assert sanitize_stem(stem).startswith("_")


def test_similar_names_are_not_prefixed():
    assert sanitize_stem("console") == "console"
    assert sanitize_stem("COM10") == "COM10"


def test_stem_is_cut_to_max_length_and_extension_kept():
    ctx = OutputNameContext(title="x" * 500)
    name = expand_filename("%T", ctx, extension="docx", max_stem_length=40)
    assert name == "x" * 40 + ".docx"


def test_cut_does_not_leave_a_trailing_dot_or_space():
    assert sanitize_stem("abc. def", max_length=4) == "abc"


def test_unicode_is_normalised_to_nfc():
    ctx = OutputNameContext(author="Müller")
    assert expand_filename("%A", ctx) == "Müller"


@pytest.mark.parametrize("ext", ["", ".rtf", "r/f", "toolongext1", "r f"])
def test_bad_extension_is_rejected(ext):
    with pytest.raises(OutputNameError):
        expand_filename("%T", CTX, extension=ext)


def test_literal_text_around_placeholders_is_kept():
    assert expand_filename("Brief %A (%D)", CTX) == "Brief Meier (2026-11-18)"


# --- output receipts -------------------------------------------------------

@pytest.fixture
def clinic_db():
    with app.app_context():
        db.create_all()  # new receipt table, checkfirst
        hook = None
        if db.engine.dialect.name == "sqlite":
            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()
        db.session.rollback()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _user():
    suffix = uuid.uuid4().hex[:10]
    user = User(username="op" + suffix, email=suffix + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    return user


def _scene():
    author = _user()
    recording = Recording(user_id=author.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(
        dictation_id=dictation.id,
        content_hash="o" * 64,
        size_bytes=1,
        storage_key="originals/" + uuid.uuid4().hex,
        receipt="stored",
        origin_ref="device:test",
    )
    db.session.add(original)
    db.session.flush()
    return author, dictation, original


T0 = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)


def test_receipt_binds_user_target_time_and_exact_revision(clinic_db):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "roher Text", "model:x")
    receipt = record_output(
        dictation, asr, author.id, CHANNEL_CLIPBOARD, "clipboard", output_at=T0
    )
    assert receipt.transcript_revision_id == asr.id
    assert receipt.text_hash == asr.normalised_text_hash
    assert receipt.actor_user_id == author.id
    assert receipt.channel == CHANNEL_CLIPBOARD
    assert receipt.target == "clipboard"
    assert receipt.text_format == "PLAIN"
    assert receipt.output_at == T0


def test_receipt_keeps_old_revision_after_a_correction(clinic_db):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "roher Text", "model:x")
    first = record_output(dictation, asr, author.id, CHANNEL_CLIPBOARD, "clipboard")
    human = add_human_revision(dictation, asr, "korrigierter Text", author.id)
    second = record_output(
        dictation, human, author.id, CHANNEL_TEXT, "dokument",
        text_format=FORMAT_FORMATTED,
    )
    assert first.transcript_revision_id == asr.id
    assert second.transcript_revision_id == human.id
    assert second.text_hash != first.text_hash
    assert is_current_output(first) is False
    assert is_current_output(second) is True
    db.session.refresh(first)
    assert first.transcript_revision_id == asr.id


def test_repeated_output_is_a_new_row_in_time_order(clinic_db):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "text", "model:x")
    late = record_output(dictation, asr, author.id, CHANNEL_TEXT, "b", output_at=T0 + timedelta(hours=1))
    early = record_output(dictation, asr, author.id, CHANNEL_TEXT, "a", output_at=T0)
    assert [row.id for row in list_outputs(dictation)] == [early.id, late.id]


def test_receipt_is_append_only_in_the_orm(clinic_db):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "text", "model:x")
    receipt = record_output(dictation, asr, author.id, CHANNEL_CLIPBOARD, "clipboard")
    receipt.target = "changed"
    with pytest.raises(OutputReceiptImmutable):
        db.session.flush()
    db.session.rollback()


def test_receipt_cannot_be_deleted_through_the_orm(clinic_db):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "text", "model:x")
    receipt = record_output(dictation, asr, author.id, CHANNEL_CLIPBOARD, "clipboard")
    db.session.commit()
    db.session.delete(receipt)
    with pytest.raises(OutputReceiptImmutable):
        db.session.flush()
    db.session.rollback()
    assert db.session.query(ClinicOutputReceipt).count() == 1


def test_revision_of_another_dictation_is_rejected(clinic_db):
    author, dictation, original = _scene()
    _author2, other, other_original = _scene()
    foreign = add_asr_revision(other, other_original, "fremd", "model:x")
    with pytest.raises(OutputReceiptError):
        record_output(dictation, foreign, author.id, CHANNEL_CLIPBOARD, "clipboard")


def test_database_rejects_a_mismatching_hash(clinic_db):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "text", "model:x")
    db.session.add(ClinicOutputReceipt(
        dictation_id=dictation.id,
        transcript_revision_id=asr.id,
        text_hash="f" * 64,
        actor_user_id=author.id,
        channel=CHANNEL_TEXT,
        text_format="PLAIN",
        target="x",
        output_at=T0,
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_database_rejects_unknown_channel(clinic_db):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "text", "model:x")
    db.session.add(ClinicOutputReceipt(
        dictation_id=dictation.id,
        transcript_revision_id=asr.id,
        text_hash=asr.normalised_text_hash,
        actor_user_id=author.id,
        channel="FAX",
        text_format="PLAIN",
        target="x",
        output_at=T0,
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


@pytest.mark.parametrize("kwargs", [
    {"channel": "FAX"},
    {"target": ""},
    {"target": "   "},
    {"target": "x" * 256},
    {"text_format": "HTML"},
    {"output_at": datetime(2026, 11, 18, 8, 0)},
])
def test_service_rejects_bad_input(clinic_db, kwargs):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "text", "model:x")
    args = {"channel": CHANNEL_TEXT, "target": "x"}
    args.update(kwargs)
    with pytest.raises(OutputReceiptError):
        record_output(dictation, asr, author.id, args.pop("channel"), args.pop("target"), **args)


def test_unknown_actor_is_rejected(clinic_db):
    _author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "text", "model:x")
    with pytest.raises(OutputReceiptError):
        record_output(dictation, asr, 10**9, CHANNEL_TEXT, "x")

"""PAR_VOC_002: import and export vocabulary as a UTF-8 text list.

One entry per line, ``written_form`` or ``written_form<TAB>spoken_form``.
Uses a real SQLite database in tmp_path (no database mocks).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clinic import vocabulary


@pytest.fixture
def session(tmp_path):
    db_file = tmp_path / "vocab_io.db"
    engine = create_engine(f"sqlite:///{db_file}")
    vocabulary.create_tables(bind=engine)
    sess = sessionmaker(bind=engine)()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


def _entry(session, written_form):
    return session.query(vocabulary.ClinicVocabularyEntry).filter_by(written_form=written_form).one()


def test_PAR_VOC_002_roundtrip_raw(session):
    vocabulary.add_vocabulary_entry(scope="user", written_form="Anamnese", user_id=1, session=session)
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Größe", spoken_form="groesse", user_id=1, session=session,
    )
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="beta", spoken_form="b", user_id=1, session=session,
    )
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Weg", user_id=1, status=vocabulary.STATUS_SUPPRESSED, session=session,
    )
    session.commit()

    exported = vocabulary.export_vocabulary_text(scope="user", user_id=1, session=session)
    assert exported.splitlines() == ["Anamnese", "Größe\tgroesse", "beta\tb"]
    assert vocabulary.export_vocabulary_text(scope="user", user_id=99, session=session) == ""

    result = vocabulary.import_vocabulary_text(exported, scope="pool", pool_id="p1", session=session)
    session.commit()
    assert result == {"added": 3, "updated": 0, "skipped": 0, "errors": []}
    assert vocabulary.export_vocabulary_text(scope="pool", pool_id="p1", session=session) == exported
    stored = session.query(vocabulary.ClinicVocabularyEntry).filter_by(pool_id="p1").all()
    assert {(row.written_form, row.spoken_form) for row in stored} == {
        ("Anamnese", None), ("Größe", "groesse"), ("beta", "b"),
    }
    assert {row.origin for row in stored} == {vocabulary.ORIGIN_IMPORT}


def test_PAR_VOC_002_roundtrip_effective(session):
    vocabulary.add_vocabulary_entry(
        scope="pool", written_form="Aspirin", spoken_form="pool", pool_id="cardio", session=session,
    )
    vocabulary.add_vocabulary_entry(
        scope="pool", written_form="EKG", spoken_form="pool-ekg", pool_id="cardio", session=session,
    )
    vocabulary.add_vocabulary_entry(
        scope="pool", written_form="Stent", spoken_form="pool-stent", pool_id="cardio", session=session,
    )
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="EKG", spoken_form="user-ekg", user_id=7, session=session,
    )
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Aspirin", user_id=7,
        status=vocabulary.STATUS_SUPPRESSED, session=session,
    )
    vocabulary.add_vocabulary_entry(scope="user", written_form="Anamnese", user_id=7, session=session)
    session.commit()

    exported = vocabulary.export_effective_vocabulary_text(7, ["cardio"], session=session)
    assert exported.splitlines() == ["Anamnese", "EKG\tuser-ekg", "Stent\tpool-stent"]
    result = vocabulary.import_vocabulary_text(exported, scope="user", user_id=8, session=session)
    session.commit()
    assert result["added"] == 3 and result["errors"] == []
    assert vocabulary.export_vocabulary_text(scope="user", user_id=8, session=session) == exported


def test_PAR_VOC_002_duplicate_rules(session):
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Anamnese", spoken_form="alt", user_id=1,
        origin=vocabulary.ORIGIN_MANUAL, notes="behalten", session=session,
    )
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Weg", spoken_form="weg", user_id=1,
        origin=vocabulary.ORIGIN_MANUAL, status=vocabulary.STATUS_SUPPRESSED, session=session,
    )
    session.commit()

    skipped = vocabulary.import_vocabulary_text(
        "Anamnese\tneu\nFrisch", scope="user", user_id=1, duplicate_rule="skip", session=session,
    )
    session.commit()
    assert skipped["added"] == 1 and skipped["skipped"] == 1
    kept = _entry(session, "Anamnese")
    assert kept.spoken_form == "alt" and kept.origin == vocabulary.ORIGIN_MANUAL

    replaced = vocabulary.import_vocabulary_text(
        "Anamnese\tneu\nAnamnese\tnoch\nWeg\nOhneGesprochen",
        scope="user", user_id=1, session=session,
    )
    session.commit()
    assert replaced["updated"] == 3 and replaced["added"] == 1 and replaced["errors"] == []
    kept = _entry(session, "Anamnese")
    assert kept.spoken_form == "noch" and kept.origin == vocabulary.ORIGIN_IMPORT and kept.notes == "behalten"
    weg = _entry(session, "Weg")
    assert weg.spoken_form == "weg" and weg.status == vocabulary.STATUS_ACTIVE
    assert weg.origin == vocabulary.ORIGIN_IMPORT

    with pytest.raises(vocabulary.VocabularyError) as exc:
        vocabulary.import_vocabulary_text(
            "Anamnese\tanders", scope="user", user_id=1, duplicate_rule="error", session=session,
        )
    assert exc.value.code == "duplicate_entry"
    assert _entry(session, "Anamnese").spoken_form == "noch"

    with pytest.raises(vocabulary.VocabularyError) as origin_exc:
        vocabulary.import_vocabulary_text(
            "Neu", scope="user", user_id=1, origin=vocabulary.ORIGIN_MANUAL, session=session,
        )
    assert origin_exc.value.code == "invalid_origin"
    assert session.query(vocabulary.ClinicVocabularyEntry).filter_by(written_form="Neu").count() == 0


def test_PAR_VOC_002_bad_lines_do_not_abort(session):
    content = (
        "\n# kommentar\n\tnur gesprochen\n" + ("W" * 256) + "\nGut\tok\nWort\t" + ("s" * 256) + "\nAuch\n"
    )
    result = vocabulary.import_vocabulary_text(content, scope="pool", pool_id="p", session=session)
    session.commit()
    assert result["added"] == 2
    assert result["errors"] == [
        {"line": 3, "reason": "empty_written_form"},
        {"line": 4, "reason": "form_too_long"},
        {"line": 6, "reason": "form_too_long"},
    ]
    exported = vocabulary.export_vocabulary_text(scope="pool", pool_id="p", session=session)
    assert exported.splitlines() == ["Auch", "Gut\tok"]


def test_PAR_VOC_002_scope_separation(session):
    vocabulary.import_vocabulary_text("Alpha\tA\nBeta", scope="user", user_id=1, session=session)
    vocabulary.import_vocabulary_text("Gamma", scope="user", user_id=2, session=session)
    vocabulary.import_vocabulary_text("Alpha\tpool", scope="pool", pool_id="p", session=session)
    session.commit()
    assert vocabulary.export_vocabulary_text(scope="user", user_id=1, session=session) == "Alpha\tA\nBeta"
    assert vocabulary.export_vocabulary_text(scope="user", user_id=2, session=session) == "Gamma"
    assert vocabulary.export_vocabulary_text(scope="pool", pool_id="p", session=session) == "Alpha\tpool"
    again = vocabulary.import_vocabulary_text(
        "Alpha\tanders", scope="user", user_id=2, duplicate_rule="error", session=session,
    )
    session.commit()
    assert again["added"] == 1 and again["errors"] == []


def test_PAR_VOC_002_case_sensitive_written_form(session):
    result = vocabulary.import_vocabulary_text("EKG\tgro\nekG\tklein", scope="user", user_id=1, session=session)
    session.commit()
    assert result["added"] == 2 and result["updated"] == 0
    lines = vocabulary.export_vocabulary_text(scope="user", user_id=1, session=session).splitlines()
    assert lines == ["EKG\tgro", "ekG\tklein"]

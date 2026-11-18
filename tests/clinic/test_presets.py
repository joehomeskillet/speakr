"""Tests for central presets and dictation properties (P04 / PAR-CFG-001..005).

Verifies:
- Default hierarchy: System (Systempflicht) -> Pool -> User -> Local.
- Central lock enforcement (Systempflicht cannot be overridden locally).
- Dictation properties form model & validation with German/English aliases.
- Exact priority values (0, 1, 128) and rejection of invalid values.
- Strict independence of speech recognition and approval (Owner decision 2026-11-18).
- Pool sentinel '<Automatisch>'.
- Regex extraction rules, preview parity, length limit and identity target protection.
- Central document templates with server copy.
- Shipping categories with mandatory selection guards.
- Metadata fields and safe XML extraction.
- Real SQLite database in tmp directory (no DB mocks).
"""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clinic import presets
from src.clinic.presets import (
    AUTO_POOL_SENTINEL,
    DEFAULT_TYPIST_SENTINEL,
    PRIORITY_IDLE,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
    ClinicCategorySet,
    ClinicMetadataField,
    ClinicPreset,
    ClinicRegexRule,
    ClinicTemplate,
    DictationProperties,
    ForbiddenTargetError,
    InvalidPriorityError,
    InvalidSecurityError,
    LockedFieldError,
    MissingFieldError,
    RegexValidationError,
    ValidationError,
    apply_regex_rules,
    create_tables,
    extract_xml_metadata,
    is_auto_pool,
    list_templates,
    preview_regex_rules,
    resolve_presets,
    save_category_set,
    save_regex_rule,
    save_template,
    set_preset,
    validate_category_requirement,
    validate_dictation_properties,
)


@pytest.fixture
def real_sqlite_session(tmp_path):
    """Real SQLite database file in a temporary directory (no mocks)."""
    db_file = tmp_path / "presets_test.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", echo=False)
    create_tables(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. Default-Hierarchie (System -> Pool -> User -> Local) & Systempflicht
# ---------------------------------------------------------------------------


def test_default_hierarchy_resolution(real_sqlite_session):
    s = real_sqlite_session

    # 1. Base system defaults
    res = resolve_presets(session=s)
    assert res.values["language"] == "de-DE"
    assert res.values["priority"] == PRIORITY_NORMAL
    assert res.origins["language"] == "system"

    # 2. System preset override
    set_preset("language", "de-AT", scope="system", session=s)
    res = resolve_presets(session=s)
    assert res.values["language"] == "de-AT"
    assert res.origins["language"] == "system"

    # 3. Pool preset override
    set_preset("language", "de-CH", scope="pool", scope_id="pool-1", session=s)
    res = resolve_presets(pool_id="pool-1", session=s)
    assert res.values["language"] == "de-CH"
    assert res.origins["language"] == "pool"

    # 4. User preset override
    set_preset("language", "fr-CH", scope="user", scope_id="user-42", session=s)
    res = resolve_presets(user_id="user-42", pool_id="pool-1", session=s)
    assert res.values["language"] == "fr-CH"
    assert res.origins["language"] == "user"

    # 5. Local override (client draft)
    res = resolve_presets(user_id="user-42", pool_id="pool-1", local_overrides={"language": "it-CH"}, session=s)
    assert res.values["language"] == "it-CH"
    assert res.origins["language"] == "local"

    # 6. Cancellation / Discard resets to server hierarchy
    res_cancel = resolve_presets(user_id="user-42", pool_id="pool-1", local_overrides=None, session=s)
    assert res_cancel.values["language"] == "fr-CH"
    assert res_cancel.origins["language"] == "user"


def test_system_lock_enforcement_systempflicht(real_sqlite_session):
    s = real_sqlite_session

    # System locks use_speech_recognition to True
    set_preset("use_speech_recognition", True, scope="system", is_locked=True, session=s)

    # Pool tries to override -> ignored because system is locked
    set_preset("use_speech_recognition", False, scope="pool", scope_id="pool-cardio", session=s)
    res = resolve_presets(pool_id="pool-cardio", session=s)
    assert res.values["use_speech_recognition"] is True
    assert res.origins["use_speech_recognition"] == "system"
    assert "use_speech_recognition" in res.locked

    # User tries to override -> ignored
    set_preset("use_speech_recognition", False, scope="user", scope_id="user-doc1", session=s)
    res = resolve_presets(user_id="user-doc1", pool_id="pool-cardio", session=s)
    assert res.values["use_speech_recognition"] is True
    assert res.origins["use_speech_recognition"] == "system"

    # Local attempt to mutate locked field raises LockedFieldError
    with pytest.raises(LockedFieldError) as exc_info:
        resolve_presets(
            user_id="user-doc1", pool_id="pool-cardio",
            local_overrides={"use_speech_recognition": False}, session=s,
        )
    assert exc_info.value.field == "use_speech_recognition"
    assert exc_info.value.locked_by == "system"


def test_pool_lock_enforcement(real_sqlite_session):
    s = real_sqlite_session

    # Pool locks priority to 128 (urgent)
    set_preset("priority", PRIORITY_URGENT, scope="pool", scope_id="pool-er", is_locked=True, session=s)

    # User in pool cannot override
    set_preset("priority", PRIORITY_IDLE, scope="user", scope_id="user-doc2", session=s)
    res = resolve_presets(user_id="user-doc2", pool_id="pool-er", session=s)
    assert res.values["priority"] == PRIORITY_URGENT
    assert res.origins["priority"] == "pool"
    assert "priority" in res.locked

    # Local attempt to change priority raises LockedFieldError (locked by pool)
    with pytest.raises(LockedFieldError) as exc_info:
        resolve_presets(
            user_id="user-doc2", pool_id="pool-er",
            local_overrides={"priority": PRIORITY_NORMAL}, session=s,
        )
    assert exc_info.value.field == "priority"
    assert exc_info.value.locked_by == "pool"


# ---------------------------------------------------------------------------
# 2. Formularmodell & Validierung (DictationProperties)
# ---------------------------------------------------------------------------


def test_dictation_properties_valid_canonical():
    props = validate_dictation_properties({
        "author": "Dr. Meier",
        "language": "de-CH",
        "dictation_name": "Befundbericht Kardiologie",
        "notes": "Eilt sehr",
        "priority": 128,
        "security": "confidential",
        "use_speech_recognition": True,
        "pool": "<Automatisch>",
        "typist": "Sek1",
        "with_approval": True,
    })
    assert props.author == "Dr. Meier"
    assert props.language == "de-CH"
    assert props.dictation_name == "Befundbericht Kardiologie"
    assert props.notes == "Eilt sehr"
    assert props.priority == 128
    assert props.security == "confidential"
    assert props.use_speech_recognition is True
    assert props.pool == "<Automatisch>"
    assert props.typist == "Sek1"
    assert props.with_approval is True

    # Check dict access and conversions
    assert props["author"] == "Dr. Meier"
    assert props["Diktatname"] == "Befundbericht Kardiologie"
    assert props.to_dict()["priority"] == 128
    assert props.to_german_dict()["Priorität"] == 128


def test_dictation_properties_valid_german_aliases():
    props = validate_dictation_properties({
        "Autor": 101,
        "Sprache": "de-DE",
        "Diktatname": "Konsilbericht",
        "Anmerkungen": "Station 3",
        "Priorität": "dringend",
        "Sicherheit": "vertraulich",
        "Spracherkennung benutzen": "ja",
        "Pool": "<Automatisch>",
        "Schreibkraft": "Voreingestellte Schreibkräfte",
        "Mit Abnahme": "nein",
    })
    assert props.author == 101
    assert props.language == "de-DE"
    assert props.dictation_name == "Konsilbericht"
    assert props.notes == "Station 3"
    assert props.priority == 128
    assert props.security == "confidential"
    assert props.use_speech_recognition is True
    assert props.with_approval is False


def test_dictation_properties_missing_required_fields():
    base = {
        "author": "user1",
        "language": "de-DE",
        "dictation_name": "Test",
        "priority": 1,
        "security": "normal",
        "use_speech_recognition": True,
        "pool": "<Automatisch>",
        "with_approval": False,
    }

    for required_key in base:
        incomplete = dict(base)
        del incomplete[required_key]
        with pytest.raises(MissingFieldError) as exc:
            validate_dictation_properties(incomplete)
        assert exc.value.field == required_key

    # Empty string should also count as missing
    with pytest.raises(MissingFieldError):
        validate_dictation_properties({**base, "dictation_name": "   "})


# ---------------------------------------------------------------------------
# 3. Prioritätswerte (0, 1, 128)
# ---------------------------------------------------------------------------


def test_priority_exact_values():
    base = {
        "author": "u1", "language": "de-DE", "dictation_name": "Test",
        "security": "normal", "use_speech_recognition": True,
        "pool": "<Automatisch>", "with_approval": False,
    }

    # 0 (idle), 1 (normal), 128 (urgent)
    p0 = validate_dictation_properties({**base, "priority": 0})
    assert p0.priority == 0

    p1 = validate_dictation_properties({**base, "priority": 1})
    assert p1.priority == 1

    p128 = validate_dictation_properties({**base, "priority": 128})
    assert p128.priority == 128

    # Named string mappings
    assert validate_dictation_properties({**base, "priority": "idle"}).priority == 0
    assert validate_dictation_properties({**base, "priority": "normal"}).priority == 1
    assert validate_dictation_properties({**base, "priority": "urgent"}).priority == 128
    assert validate_dictation_properties({**base, "priority": "sofort"}).priority == 128
    assert validate_dictation_properties({**base, "priority": "frist"}).priority == 128

    # Invalid priorities must be rejected with InvalidPriorityError
    for invalid in (2, 3, -1, 50, 99, 129, 200, "high", "low", "invalid", None):
        with pytest.raises(ValidationError):
            validate_dictation_properties({**base, "priority": invalid})


# ---------------------------------------------------------------------------
# 4. Strict Independence of Approval & Speech Recognition (Owner Decision)
# ---------------------------------------------------------------------------


def test_strict_independence_approval_and_speech_recognition():
    """Owner decision 2026-11-18:

    'Mit Abnahme' and 'Spracherkennung benutzen' are strictly independent settings.
    All 4 combinations must be completely valid without coupling.
    """
    base = {
        "author": "doc1", "language": "de-DE", "dictation_name": "Test",
        "priority": 1, "security": "normal", "pool": "<Automatisch>",
    }

    # 1. No ASR, No Approval
    c1 = validate_dictation_properties({**base, "use_speech_recognition": False, "with_approval": False})
    assert c1.use_speech_recognition is False
    assert c1.with_approval is False

    # 2. ASR, No Approval
    c2 = validate_dictation_properties({**base, "use_speech_recognition": True, "with_approval": False})
    assert c2.use_speech_recognition is True
    assert c2.with_approval is False

    # 3. No ASR, With Approval
    c3 = validate_dictation_properties({**base, "use_speech_recognition": False, "with_approval": True})
    assert c3.use_speech_recognition is False
    assert c3.with_approval is True

    # 4. ASR, With Approval
    c4 = validate_dictation_properties({**base, "use_speech_recognition": True, "with_approval": True})
    assert c4.use_speech_recognition is True
    assert c4.with_approval is True


# ---------------------------------------------------------------------------
# 5. Security Classification Validation
# ---------------------------------------------------------------------------


def test_security_validation():
    base = {
        "author": "u1", "language": "de-DE", "dictation_name": "Test",
        "priority": 1, "use_speech_recognition": True,
        "pool": "<Automatisch>", "with_approval": False,
    }

    for valid in ("normal", "confidential", "restricted", "vertraulich", "streng_vertraulich"):
        p = validate_dictation_properties({**base, "security": valid})
        assert p.security in ("normal", "confidential", "restricted")

    with pytest.raises(InvalidSecurityError):
        validate_dictation_properties({**base, "security": "top-secret-super-custom"})


# ---------------------------------------------------------------------------
# 6. Pool Sentinel (<Automatisch>)
# ---------------------------------------------------------------------------


def test_pool_sentinel():
    assert is_auto_pool("<Automatisch>") is True
    assert is_auto_pool("AUTO") is True
    assert is_auto_pool("<auto>") is True
    assert is_auto_pool("pool-uuid-1234") is False

    base = {
        "author": "u1", "language": "de-DE", "dictation_name": "Test",
        "priority": 1, "security": "normal", "use_speech_recognition": True, "with_approval": False,
    }
    # Sentinel pool
    p_auto = validate_dictation_properties({**base, "pool": "<Automatisch>"})
    assert p_auto.pool == AUTO_POOL_SENTINEL

    # Specific pool
    p_spec = validate_dictation_properties({**base, "pool": "pool-cardiology-east"})
    assert p_spec.pool == "pool-cardiology-east"


# ---------------------------------------------------------------------------
# 7. RegEx Rules (PAR-CFG-004)
# ---------------------------------------------------------------------------


def test_regex_rules_apply_and_preview_parity():
    rules = [
        ClinicRegexRule(
            name="Doktor I - Dringend",
            pattern=r"(sofort\s+schreiben|heute\s+fertigstellen|dringend)",
            target_field="priority",
            replacement="128",
            priority_order=10,
        ),
        ClinicRegexRule(
            name="Befundtitel",
            pattern=r"Befund:\s*([^.\n\r]+(?:\.|$))",
            target_field="title",
            priority_order=20,
        ),
        ClinicRegexRule(
            name="Anmerkungsnotiz",
            pattern=r"Hinweis:\s*([^.\n\r]+(?:\.|$))",
            target_field="notes",
            priority_order=30,
        ),
    ]

    sample = "Patient Meier. Befund: Herzinsuffizienz Grad II. Bitte sofort schreiben! Hinweis: Vorbefund anfordern."

    # Both apply and preview must produce exact same field extraction
    applied = apply_regex_rules(rules, sample)
    assert applied["priority"] == 128
    assert applied["title"] == "Herzinsuffizienz Grad II."
    assert applied["notes"] == "Vorbefund anfordern."

    preview = preview_regex_rules(rules, sample)
    assert preview["resulting_fields"] == applied
    assert len(preview["matches"]) == 3
    assert preview["truncated"] is False


def test_regex_rules_forbidden_identity_targets():
    """PAR-CFG-004: Identity targets (user/patient/case) are strictly forbidden."""
    for forbidden in ("user_id", "author_id", "patient_id", "case_id", "integration_id"):
        with pytest.raises(ForbiddenTargetError):
            save_regex_rule(
                name="Forbidden Rule",
                pattern=r"ID:\s*(\d+)",
                target_field=forbidden,
            )

        with pytest.raises(ForbiddenTargetError):
            apply_regex_rules(
                [{"name": "Bad", "pattern": r"(\d+)", "target_field": forbidden}],
                "text 123",
            )


def test_regex_rules_max_input_length_truncation():
    rules = [
        ClinicRegexRule(
            name="Tail Check",
            pattern=r"GEHEIMES_WORT",
            target_field="notes",
            priority_order=1,
            max_input_length=50,
        ),
    ]
    # Word appears after char 50
    long_text = ("A" * 50) + " GEHEIMES_WORT"
    res = preview_regex_rules(rules, long_text, max_input_length=50)
    assert res["truncated"] is True
    assert "notes" not in res["resulting_fields"]

    # Word appears within char 50
    short_text = "GEHEIMES_WORT " + ("A" * 30)
    res_found = preview_regex_rules(rules, short_text, max_input_length=50)
    assert res_found["resulting_fields"]["notes"] == "GEHEIMES_WORT"


# ---------------------------------------------------------------------------
# 8. Vorlagen (Templates, PAR-CFG-002)
# ---------------------------------------------------------------------------


def test_templates_server_copy_and_hierarchy(real_sqlite_session):
    s = real_sqlite_session
    docx_bytes = b"PK\x03\x04FAKE_WORD_DOCX_PAYLOAD"

    # Save system template
    t_sys = save_template(
        name="Standardbericht",
        content_bytes=docx_bytes,
        scope="system",
        notes="Allgemeine Vorlage",
        insertion_mark=":Tdns.comfort",
        session=s,
    )
    assert t_sys.version == 1
    import hashlib
    assert t_sys.content_hash == hashlib.sha256(docx_bytes).hexdigest()
    assert t_sys.content_bytes == docx_bytes

    # Save pool template
    save_template(
        name="Kardiobericht",
        content_bytes=b"POOL_DOCX",
        scope="pool",
        scope_id="pool-cardio",
        session=s,
    )

    # Save user template
    save_template(
        name="Privatnotiz",
        content_bytes=b"USER_DOCX",
        scope="user",
        scope_id="user-123",
        session=s,
    )

    # List templates for user-123 in pool-cardio
    tmpls = list_templates(user_id="user-123", pool_ids=["pool-cardio"], session=s)
    names = {t.name for t in tmpls}
    assert "Standardbericht" in names
    assert "Kardiobericht" in names
    assert "Privatnotiz" in names


# ---------------------------------------------------------------------------
# 9. Versandkategorien (PAR-CFG-003)
# ---------------------------------------------------------------------------


def test_shipping_categories_requirement(real_sqlite_session):
    s = real_sqlite_session
    cset = save_category_set(
        name="Klinik-Standardkategorien",
        categories=["Ambulant", "Stationär", "Konsil", "Notfall"],
        is_required=True,
        session=s,
    )

    # Note with valid category passes
    assert validate_category_requirement(cset, "Dies ist ein Stationär-Befund.") is True

    # Note missing category raises ValidationError
    with pytest.raises(ValidationError):
        validate_category_requirement(cset, "Befund ohne passende Kategorie.")

    with pytest.raises(ValidationError):
        validate_category_requirement(cset, "")


# ---------------------------------------------------------------------------
# 10. Konfigurierbare Metadatenspalten & Safe XML (PAR-CFG-005)
# ---------------------------------------------------------------------------


def test_xml_metadata_safe_extraction():
    fields = [
        ClinicMetadataField(name="station", label="Station", xml_path="Station"),
        ClinicMetadataField(name="department", label="Abteilung", xml_path="Abteilung", default_value="Allgemein"),
        ClinicMetadataField(name="room", label="Zimmer", xml_path="Zimmer", default_value="Unbekannt"),
    ]

    xml_valid = """
    <DictationMeta>
        <Station>Kardiologie West</Station>
        <Abteilung>Innere Medizin</Abteilung>
    </DictationMeta>
    """
    extracted = extract_xml_metadata(xml_valid, fields)
    assert extracted["station"] == "Kardiologie West"
    assert extracted["department"] == "Innere Medizin"
    assert extracted["room"] == "Unbekannt"


def test_xml_metadata_security_disallows_doctype():
    fields = [ClinicMetadataField(name="station", label="Station", xml_path="Station")]
    xml_xxe = """<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><DictationMeta><Station>&xxe;</Station></DictationMeta>"""
    with pytest.raises(ValidationError) as exc:
        extract_xml_metadata(xml_xxe, fields)
    assert "security" in str(exc.value)


# ---------------------------------------------------------------------------
# 11. Database Constraints & Field Validation Edge Cases
# ---------------------------------------------------------------------------


def test_db_unique_and_check_constraints(real_sqlite_session):
    from sqlalchemy.exc import IntegrityError
    s = real_sqlite_session

    p1 = ClinicPreset(scope="system", scope_id="__system__", key="lang", value_json='"de"')
    s.add(p1)
    s.flush()

    # Duplicate (scope, scope_id, key) must fail
    p2 = ClinicPreset(scope="system", scope_id="__system__", key="lang", value_json='"en"')
    s.add(p2)
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()

    # Invalid scope must fail check constraint
    p3 = ClinicPreset(scope="invalid_scope", scope_id="123", key="test", value_json='"x"')
    s.add(p3)
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()


def test_dictation_properties_regex_validation_rule():
    base = {
        "author": "u1", "language": "de-DE", "dictation_name": "InvalidName123",
        "priority": 1, "security": "normal", "use_speech_recognition": True,
        "pool": "<Automatisch>", "with_approval": False,
    }
    # Rule requiring title to start with "Bericht:"
    rule = ClinicRegexRule(name="Title Prefix", pattern=r"^Bericht:", target_field="title")

    with pytest.raises(RegexValidationError) as exc:
        validate_dictation_properties(base, regex_rules=[rule])
    assert exc.value.field == "dictation_name"

    # Valid name matching prefix
    valid_props = validate_dictation_properties({**base, "dictation_name": "Bericht: Kardiologie"}, regex_rules=[rule])
    assert valid_props.dictation_name == "Bericht: Kardiologie"


def test_template_version_increment(real_sqlite_session):
    s = real_sqlite_session
    t1 = save_template(name="Entlassbericht", content_bytes=b"V1", session=s)
    assert t1.version == 1
    t2 = save_template(name="Entlassbericht", content_bytes=b"V2", session=s)
    assert t2.version == 2

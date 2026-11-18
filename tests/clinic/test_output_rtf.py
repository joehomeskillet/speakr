"""PAR-EXP-003: RTF output of written and formatted clinic transcripts.

Tests cover:
- RTF 1.x header and structural validation
- Umlauts and German characters (ä, ö, ü, Ä, Ö, Ü, ß, €) as \\uN escapes
- Bold and italic mapping from Markdown (**bold**, *italic*, ***both***)
- Bold and italic mapping from HTML (<b>, <strong>, <i>, <em>, <u>, <br>, <p>)
- Plain text fallback preserving text and paragraph boundaries
- Strictly balanced RTF group delimiters { and }
- Deterministic output (byte-identical across calls)
- Real database records (ClinicTranscriptRevision, ClinicRelease, Recording) without mocks
"""

import hashlib
import json
import uuid

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.clinic.output_rtf import (
    RTFDocument,
    RTFValidationError,
    get_rtf_bytes,
    rtf_escape,
    text_to_rtf,
    validate_rtf,
)
from src.models import Recording, User

# Realistic clinical discharge text with German medical terminology and umlauts
CLINICAL_DISCHARGE_TEXT = (
    "# Austrittsbericht\n"
    "**Patient:** Hans Müller, geb. 12.04.1958\n"
    "**Diagnosen:**\n"
    "- Arterielle Hypertonie, medikamentös *gut eingestellt*\n"
    "- Status nach elektiver Cholezystektomie am 14.11.2026\n"
    "\n"
    "## Befund und Verlauf\n"
    "Der postoperativ stabilisierte Patient zeigt eine **reizlose Wundverhältnisse** "
    "im Bereich der Trokar-Einstichstellen. Keine Rötung, keine Überwärmung.\n"
    "Subjektiv bestehen noch *leichte Schmerzen* bei Belastung (VAS 2/10).\n"
    "\n"
    "## Medikation bei Austritt\n"
    "1. Pantoprazol 40mg 1-0-0 (Magenschutz)\n"
    "2. Paracetamol 1g b. Bed. bis 4x/Tag (Analgesie)\n"
    "\n"
    "Größe: 178 cm, Gewicht: 82 kg. Entlassung nach Hause am 18.11.2026."
)


@pytest.fixture
def clinic_db():
    """App context with SQLite foreign keys on for real database testing."""
    with app.app_context():
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


def _create_real_dictation_fixture():
    """Create real database entities for testing without mocks."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        username="dr_" + suffix,
        email="dr_" + suffix + "@spital.example.invalid",
        password="hash_" + suffix,
    )
    db.session.add(user)
    db.session.flush()

    recording = Recording(
        user_id=user.id,
        title="Austrittsbericht Müller",
        status="COMPLETED",
        transcription=CLINICAL_DISCHARGE_TEXT,
    )
    db.session.add(recording)
    db.session.flush()

    dictation = ClinicDictation(
        recording_id=recording.id,
        author_user_id=user.id,
    )
    db.session.add(dictation)
    db.session.flush()

    audio = ClinicAudioRevision(
        dictation_id=dictation.id,
        content_hash="a" * 64,
    )
    db.session.add(audio)
    db.session.flush()

    original = ClinicOriginal(
        dictation_id=dictation.id,
        content_hash="o" * 64,
        size_bytes=256,
        storage_key="originals/" + uuid.uuid4().hex,
        receipt="stored",
        origin_ref="mic:real-device",
    )
    db.session.add(original)
    db.session.flush()

    transcript = ClinicTranscriptRevision(
        dictation_id=dictation.id,
        original_version_id=original.id,
        kind="HUMAN",
        body=CLINICAL_DISCHARGE_TEXT,
        normalised_text_hash=hashlib.sha256(CLINICAL_DISCHARGE_TEXT.encode("utf-8")).hexdigest(),
    )
    db.session.add(transcript)
    db.session.flush()

    context = ClinicContextRevision(
        dictation_id=dictation.id,
        source_system="kis",
        tenant_scope="site",
        case_ref="fall-2026-99",
        patient_ref="pat-12345",
    )
    db.session.add(context)
    db.session.flush()

    release = ClinicRelease(
        dictation_id=dictation.id,
        audio_revision_id=audio.id,
        transcript_revision_id=transcript.id,
        context_revision_id=context.id,
        text_hash=transcript.normalised_text_hash,
        actor_user_id=user.id,
        document_type="Austrittsbericht",
        release_type="clinical",
        authorization_decision='{"approved": true}',
    )
    db.session.add(release)
    db.session.flush()

    return user, recording, dictation, transcript, release


# --- PAR-EXP-003 Tests -----------------------------------------------------

def test_PAR_EXP_003_header_and_pure_ascii():
    """RTF output must start with {\\rtf1 and consist entirely of 7-bit ASCII bytes."""
    rtf_bytes = text_to_rtf("Einfacher Text.")
    assert rtf_bytes.startswith(b"{\\rtf1")
    assert rtf_bytes.endswith(b"}")

    # Must decode cleanly as pure ASCII (no unescaped UTF-8 bytes)
    ascii_str = rtf_bytes.decode("ascii")
    assert "{\\rtf1" in ascii_str
    assert "\\viewkind4\\uc1" in ascii_str


def test_PAR_EXP_003_umlauts_and_unicode_escapes():
    """German umlauts and special characters must be properly escaped using \\uN? syntax."""
    text = "Herr Dr. Müller prüft Größe, Überweisung und Entlassung für Fr. Schön. Straße & Café (€ 100)."
    rtf_bytes = text_to_rtf(text)
    ascii_str = rtf_bytes.decode("ascii")

    # Check for RTF Unicode escapes
    assert r"\u228" in ascii_str or r"\u252" in ascii_str  # ä, ü
    assert r"\u246" in ascii_str  # ö
    assert r"\u223" in ascii_str  # ß
    assert r"\u196" in ascii_str or r"\u220" in ascii_str  # Ä, Ü
    assert r"\u8364" in ascii_str  # € (Euro sign)

    # Validate structural parsing and character round-trip
    doc = validate_rtf(rtf_bytes)
    assert doc.is_valid is True
    assert "Müller" in doc.full_text
    assert "Größe" in doc.full_text
    assert "Straße" in doc.full_text
    assert "Schön" in doc.full_text
    assert "Café" in doc.full_text
    assert "€ 100" in doc.full_text


def test_PAR_EXP_003_markdown_bold_and_italic_mapping():
    """Markdown bold and italic delimiters must be correctly mapped to RTF styling."""
    md_text = (
        "Dies ist **fetter Text** und dies ist *kursiver Text*.\n"
        "Hier ist ***fett und kursiv*** kombiniert.\n"
        "Alternativ mit Unterstrichen: __fett__ und _kursiv_."
    )
    rtf_bytes = text_to_rtf(md_text)
    ascii_str = rtf_bytes.decode("ascii")

    # RTF contains bold and italic groups
    assert r"\b " in ascii_str or r"\b" in ascii_str
    assert r"\i " in ascii_str or r"\i" in ascii_str

    # Structural parser verifies styled spans
    doc = validate_rtf(rtf_bytes)
    assert doc.is_valid is True

    # Check styled spans in first paragraph
    p0_spans = doc.styled_paragraphs[0]
    bold_spans = [s for s in p0_spans if s.bold and not s.italic]
    italic_spans = [s for s in p0_spans if s.italic and not s.bold]
    assert any("fetter Text" in s.text for s in bold_spans)
    assert any("kursiver Text" in s.text for s in italic_spans)

    # Check combined bold+italic in second paragraph
    p1_spans = doc.styled_paragraphs[1]
    both_spans = [s for s in p1_spans if s.bold and s.italic]
    assert any("fett und kursiv" in s.text for s in both_spans)


def test_PAR_EXP_003_markdown_delimiters_not_overapplied():
    """Asterisks in math expressions or underscores in identifiers must not trigger italic."""
    text = "Berechnung: 2 * 3 = 6 und Variable pat_id_code bleiben unformatiert."
    rtf_bytes = text_to_rtf(text)
    doc = validate_rtf(rtf_bytes)

    # Neither italic nor bold should be active
    for p in doc.styled_paragraphs:
        for span in p:
            assert not span.bold
            assert not span.italic
    assert "2 * 3 = 6" in doc.full_text
    assert "pat_id_code" in doc.full_text


def test_PAR_EXP_003_html_formatting_mapping():
    """HTML tags for bold, italic, underline and paragraphs must map to RTF."""
    html_text = (
        "<p>Patient zeigt <b>starke Besserung</b> und <i>stabile Vitalwerte</i>.</p>"
        "<p>Zusätzlich <strong>wichtiger</strong> und <em>dringender</em> Hinweis.<br>Folgezeile.</p>"
    )
    rtf_bytes = text_to_rtf(html_text)
    doc = validate_rtf(rtf_bytes)
    assert doc.is_valid is True
    assert len(doc.paragraphs) == 2

    p0 = doc.styled_paragraphs[0]
    assert any("starke Besserung" in s.text and s.bold for s in p0)
    assert any("stabile Vitalwerte" in s.text and s.italic for s in p0)

    p1 = doc.styled_paragraphs[1]
    assert any("wichtiger" in s.text and s.bold for s in p1)
    assert any("dringender" in s.text and s.italic for s in p1)


def test_PAR_EXP_003_plain_text_fallback():
    """Plain-text transcripts without formatting must be preserved exactly without style injection."""
    plain = (
        "Klinik am Südhang\n"
        "Stationäre Aufnahme vom 10.11.2026.\n"
        "\n"
        "Patient klagt über Schmerzen im rechten Sprunggelenk nach Sturz.\n"
        "Keine bekannten Allergien."
    )
    rtf_bytes = text_to_rtf(plain)
    doc = validate_rtf(rtf_bytes)
    assert doc.is_valid is True

    # No styled spans
    for p in doc.styled_paragraphs:
        for span in p:
            assert not span.bold
            assert not span.italic
            assert not span.underline

    # Paragraph count and text preservation
    non_empty_paras = [p for p in doc.paragraphs if p.strip()]
    assert len(non_empty_paras) == 4
    assert "Klinik am Südhang" in doc.paragraphs[0]
    assert "Schmerzen im rechten Sprunggelenk" in non_empty_paras[2]
    assert "Schmerzen im rechten Sprunggelenk" in doc.paragraphs[3]


def test_PAR_EXP_003_balanced_groups():
    """Every generated RTF document must have strictly balanced braces."""
    texts = [
        "",
        "Einzeiler.",
        "Code mit geschweiften Klammern: { int x = 42; } und Pfad C:\\Temp\\Test.",
        CLINICAL_DISCHARGE_TEXT,
        "<b>HTML</b> und **Markdown** und {Klammern} gemischt.",
    ]
    for t in texts:
        rtf_bytes = text_to_rtf(t)
        doc = validate_rtf(rtf_bytes)
        assert doc.groups_balanced is True

        # Manual balance check on the raw string (ignoring \{ and \})
        raw = rtf_bytes.decode("ascii")
        depth = 0
        i = 0
        while i < len(raw):
            if raw[i] == "\\":
                i += 2  # skip escaped character
                continue
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                assert depth >= 0, f"Group closed below depth 0 at position {i}"
            i += 1
        assert depth == 0, f"Unbalanced braces, final depth: {depth}"


def test_PAR_EXP_003_structural_validator_rejects_malformed_rtf():
    """validate_rtf must catch syntax errors in malformed RTF data."""
    # Not starting with {\rtf1
    with pytest.raises(RTFValidationError, match=r"does not start with"):
        validate_rtf("Hello world")

    # Unbalanced opening brace
    with pytest.raises(RTFValidationError, match=r"Unbalanced braces"):
        validate_rtf(r"{\rtf1\ansi Text { unclosed }")

    # Unmatched closing brace
    with pytest.raises(RTFValidationError, match=r"Unmatched closing brace"):
        validate_rtf(r"{\rtf1\ansi Text }}")

    # Trailing backslash
    with pytest.raises(RTFValidationError, match=r"Trailing backslash"):
        validate_rtf(r"{\rtf1\ansi Text " + "\\")


def test_PAR_EXP_003_deterministic_output():
    """Rendering the same transcript multiple times must produce identical bytes and hashes."""
    run1 = text_to_rtf(CLINICAL_DISCHARGE_TEXT)
    run2 = text_to_rtf(CLINICAL_DISCHARGE_TEXT)
    run3 = text_to_rtf(CLINICAL_DISCHARGE_TEXT)

    assert run1 == run2
    assert run2 == run3
    hash1 = hashlib.sha256(run1).hexdigest()
    hash2 = hashlib.sha256(run2).hexdigest()
    assert hash1 == hash2


def test_PAR_EXP_003_get_rtf_bytes_from_transcript_revision(clinic_db):
    """get_rtf_bytes with a persisted ClinicTranscriptRevision produces valid RTF."""
    _user, _rec, _dict, transcript, _release = _create_real_dictation_fixture()

    rtf_bytes = get_rtf_bytes(transcript)
    assert rtf_bytes.startswith(b"{\\rtf1")

    doc = validate_rtf(rtf_bytes)
    assert doc.is_valid is True
    assert "Hans Müller" in doc.full_text
    assert "Arterielle Hypertonie" in doc.full_text


def test_PAR_EXP_003_get_rtf_bytes_from_release(clinic_db):
    """get_rtf_bytes with a persisted ClinicRelease resolves the revision and produces RTF."""
    _user, _rec, _dict, _transcript, release = _create_real_dictation_fixture()

    rtf_bytes = get_rtf_bytes(release)
    assert rtf_bytes.startswith(b"{\\rtf1")

    doc = validate_rtf(rtf_bytes)
    assert doc.is_valid is True
    assert "Hans Müller" in doc.full_text
    assert "Cholezystektomie" in doc.full_text


def test_PAR_EXP_003_get_rtf_bytes_from_recording_with_segments(clinic_db):
    """get_rtf_bytes with a Recording containing Whisper segment JSON renders segments."""
    user = User(username="dr_whisper", email="dr_whisper@spital.invalid", password="x")
    db.session.add(user)
    db.session.flush()

    segment_data = [
        {"speaker": "Arzt", "sentence": "Wie geht es Ihnen heute?", "start_time": 0.0, "end_time": 2.0},
        {"speaker": "Patient", "sentence": "Die Schmerzen haben deutlich nachgelassen.", "start_time": 2.5, "end_time": 5.0},
    ]
    recording = Recording(
        user_id=user.id,
        title="Gespräch 18.11.2026",
        status="COMPLETED",
        transcription=json.dumps(segment_data),
    )
    db.session.add(recording)
    db.session.flush()

    rtf_bytes = get_rtf_bytes(recording)
    doc = validate_rtf(rtf_bytes)
    assert doc.is_valid is True
    assert "[Arzt]: Wie geht es Ihnen heute?" in doc.full_text
    assert "[Patient]: Die Schmerzen haben deutlich nachgelassen." in doc.full_text


def test_PAR_EXP_003_markdown_headings_and_bullets():
    """Headings and bullet lists in Markdown are mapped to styled paragraphs."""
    content = (
        "# Überschrift 1\n"
        "## Überschrift 2\n"
        "- Erster Punkt\n"
        "- Zweiter Punkt mit **Fettung**\n"
    )
    rtf_bytes = text_to_rtf(content)
    ascii_str = rtf_bytes.decode("ascii")
    assert r"\bullet\tab " in ascii_str

    doc = validate_rtf(rtf_bytes)
    assert doc.is_valid is True
    assert "Überschrift 1" in doc.paragraphs[0]
    assert "Überschrift 2" in doc.paragraphs[1]


def test_PAR_EXP_003_empty_and_none_sources():
    """Empty strings and None sources produce valid empty RTF documents."""
    for empty_input in ["", None, "   "]:
        rtf_bytes = get_rtf_bytes(empty_input)
        assert rtf_bytes.startswith(b"{\\rtf1")
        doc = validate_rtf(rtf_bytes)
        assert doc.is_valid is True

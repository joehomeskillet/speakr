"""Central presets and dictation properties form model (PAR-CFG-001..005).

- Default hierarchy: System (Systempflicht) -> Pool -> User -> Local.
- Dictation properties form model with strictly independent speech recognition
  and approval (owner decision 2026-11-18).
- Central templates with durable server copy (usable without original file).
- Shipping category sets with mandatory input guards.
- RegEx metadata extraction with preview parity and identity protection.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, LargeBinary, String, Text, UniqueConstraint

from src.database import db

# Constants & Sentinels
SYSTEM_SCOPE_ID = "__system__"
AUTO_POOL_SENTINEL = "<Automatisch>"
DEFAULT_TYPIST_SENTINEL = "<Voreingestellt>"
ALL_ELIGIBLE_TYPISTS = "all_eligible"

PRIORITY_IDLE, PRIORITY_NORMAL, PRIORITY_URGENT = 0, 1, 128
ALLOWED_PRIORITIES = frozenset({PRIORITY_IDLE, PRIORITY_NORMAL, PRIORITY_URGENT})
ALLOWED_SECURITY_LEVELS = frozenset({"normal", "confidential", "restricted"})

ALLOWED_REGEX_TARGETS = frozenset({
    "title", "dictation_name", "remarks", "notes", "priority", "urgency", "deadline", "due_date"
})
FORBIDDEN_REGEX_TARGETS = frozenset({
    "user_id", "author_id", "patient_id", "case_id", "integration_id", "client_id", "account_id"
})

GERMAN_FIELD_ALIASES: dict[str, str] = {
    "Autor": "author", "Sprache": "language", "Diktatname": "dictation_name",
    "Anmerkungen": "notes", "Priorität": "priority", "Sicherheit": "security",
    "Spracherkennung benutzen": "use_speech_recognition", "Pool": "pool",
    "Schreibkraft": "typist", "Mit Abnahme": "with_approval",
}
INVERSE_GERMAN_ALIASES = {v: k for k, v in GERMAN_FIELD_ALIASES.items()}

PRIORITY_NAME_MAP: dict[Any, int] = {
    0: PRIORITY_IDLE, 1: PRIORITY_NORMAL, 128: PRIORITY_URGENT,
    "0": PRIORITY_IDLE, "1": PRIORITY_NORMAL, "128": PRIORITY_URGENT,
    "idle": PRIORITY_IDLE, "normal": PRIORITY_NORMAL, "urgent": PRIORITY_URGENT,
    "dringend": PRIORITY_URGENT, "frist": PRIORITY_URGENT, "sofort": PRIORITY_URGENT,
    "terminsache": PRIORITY_URGENT,
}
SECURITY_NAME_MAP: dict[str, str] = {
    "normal": "normal", "standard": "normal", "confidential": "confidential",
    "vertraulich": "confidential", "restricted": "restricted", "streng_vertraulich": "restricted",
}
SYSTEM_DEFAULTS: dict[str, Any] = {
    "language": "de-DE", "priority": PRIORITY_NORMAL, "security": "normal",
    "use_speech_recognition": True, "pool": AUTO_POOL_SENTINEL,
    "typist": DEFAULT_TYPIST_SENTINEL, "with_approval": False,
}

# Typed Exceptions
class PresetError(Exception):
    def __init__(self, message: str, code: str = "preset_error") -> None:
        super().__init__(message)
        self.message, self.code = message, code


class ValidationError(PresetError):
    def __init__(self, message: str, field: str | None = None, code: str = "validation_error") -> None:
        super().__init__(message, code=code)
        self.field = field


class MissingFieldError(ValidationError):
    def __init__(self, field: str) -> None:
        super().__init__(f"Required field '{field}' is missing.", field=field, code="missing_field")


class InvalidPriorityError(ValidationError):
    def __init__(self, value: Any) -> None:
        super().__init__(f"Invalid priority '{value}'. Allowed: 0, 1, 128.", field="priority", code="invalid_priority")
        self.value = value


class InvalidSecurityError(ValidationError):
    def __init__(self, value: Any) -> None:
        super().__init__(f"Invalid security '{value}'.", field="security", code="invalid_security")
        self.value = value


class LockedFieldError(ValidationError):
    def __init__(self, field: str, locked_by: str = "system") -> None:
        super().__init__(f"Field '{field}' locked by {locked_by}.", field=field, code="locked_field")
        self.locked_by = locked_by


class RegexValidationError(ValidationError):
    def __init__(self, field: str, pattern: str, value: str) -> None:
        super().__init__(f"Field '{field}' failed pattern '{pattern}'.", field=field, code="regex_validation_failed")
        self.pattern, self.value = pattern, value


class ForbiddenTargetError(PresetError):
    def __init__(self, target: str) -> None:
        super().__init__(f"Regex target '{target}' forbidden.", code="forbidden_regex_target")
        self.target = target


# Form Model: DictationProperties
@dataclass
class DictationProperties:
    """Dictation properties form model.

    Owner decision 2026-11-18:
    `with_approval` and `use_speech_recognition` are strictly independent.
    """

    author: str | int
    language: str
    dictation_name: str
    notes: str = ""
    priority: int = PRIORITY_NORMAL
    security: str = "normal"
    use_speech_recognition: bool = True
    pool: str = AUTO_POOL_SENTINEL
    typist: str | int | None = None
    with_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_german_dict(self) -> dict[str, Any]:
        return {INVERSE_GERMAN_ALIASES.get(k, k): v for k, v in asdict(self).items()}

    def __getitem__(self, item: str) -> Any:
        key = GERMAN_FIELD_ALIASES.get(item, item)
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(item)


def is_auto_pool(val: Any) -> bool:
    return str(val).strip() in (AUTO_POOL_SENTINEL, "AUTO", "<auto>")


def _parse_bool(val: Any, field_name: str) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("true", "1", "yes", "ja", "on"):
            return True
        if low in ("false", "0", "no", "nein", "off"):
            return False
    raise ValidationError(f"Invalid boolean for '{field_name}'.", field=field_name)


def validate_dictation_properties(
    data: Mapping[str, Any] | DictationProperties,
    regex_rules: list[ClinicRegexRule | dict[str, Any]] | None = None,
    category_set: ClinicCategorySet | None = None,
) -> DictationProperties:
    raw = data.to_dict() if isinstance(data, DictationProperties) else dict(data)
    norm = {GERMAN_FIELD_ALIASES.get(k, k): v for k, v in raw.items()}
    for req in ("author", "language", "dictation_name", "priority", "security", "use_speech_recognition", "pool", "with_approval"):
        if req not in norm or norm[req] is None or (isinstance(norm[req], str) and not norm[req].strip()):
            raise MissingFieldError(req)

    author = norm["author"].strip() if isinstance(norm["author"], str) else norm["author"]
    language, dictation_name = str(norm["language"]).strip(), str(norm["dictation_name"]).strip()
    raw_prio = norm["priority"]
    prio_key = str(raw_prio).strip().lower() if isinstance(raw_prio, str) else raw_prio
    if prio_key not in PRIORITY_NAME_MAP:
        raise InvalidPriorityError(raw_prio)
    priority = PRIORITY_NAME_MAP[prio_key]

    raw_sec = str(norm["security"]).strip().lower()
    if raw_sec not in SECURITY_NAME_MAP:
        raise InvalidSecurityError(norm["security"])
    security = SECURITY_NAME_MAP[raw_sec]

    use_asr = _parse_bool(norm["use_speech_recognition"], "use_speech_recognition")
    with_appr = _parse_bool(norm["with_approval"], "with_approval")
    pool_raw = str(norm["pool"]).strip()
    pool = AUTO_POOL_SENTINEL if is_auto_pool(pool_raw) else pool_raw
    typist = norm.get("typist")
    typist = typist.strip() if isinstance(typist, str) and typist.strip() else typist
    notes = str(norm.get("notes") or "").strip()

    if category_set is not None and category_set.is_required:
        validate_category_requirement(category_set, notes)
    if regex_rules:
        for rule in regex_rules:
            pattern = rule.pattern if hasattr(rule, "pattern") else rule["pattern"]
            target = rule.target_field if hasattr(rule, "target_field") else rule["target_field"]
            if target in FORBIDDEN_REGEX_TARGETS:
                raise ForbiddenTargetError(target)
            if target in ("dictation_name", "title") and not re.search(pattern, dictation_name):
                raise RegexValidationError("dictation_name", pattern, dictation_name)
            if target in ("notes", "remarks") and notes and not re.search(pattern, notes):
                raise RegexValidationError("notes", pattern, notes)

    return DictationProperties(
        author=author, language=language, dictation_name=dictation_name, notes=notes,
        priority=priority, security=security, use_speech_recognition=use_asr,
        pool=pool, typist=typist, with_approval=with_appr,
    )


# SQLAlchemy Models
def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ClinicPreset(db.Model):
    __tablename__ = "clinic_preset"
    id = Column(String(36), primary_key=True, default=_new_id)
    scope = Column(String(16), nullable=False)
    scope_id = Column(String(36), nullable=False, default=SYSTEM_SCOPE_ID)
    key = Column(String(64), nullable=False, index=True)
    value_json = Column(Text, nullable=False)
    is_locked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    __table_args__ = (
        CheckConstraint("scope IN ('system', 'pool', 'user')", name="ck_clinic_preset_scope"),
        CheckConstraint(
            "(scope = 'system' AND scope_id = '__system__') OR (scope <> 'system' AND scope_id <> '__system__')",
            name="ck_clinic_preset_scope_id",
        ),
        UniqueConstraint("scope", "scope_id", "key", name="uq_clinic_preset_scope_key"),
    )


class ClinicTemplate(db.Model):
    __tablename__ = "clinic_template"
    id = Column(String(36), primary_key=True, default=_new_id)
    scope = Column(String(16), nullable=False)
    scope_id = Column(String(36), nullable=False, default=SYSTEM_SCOPE_ID)
    name = Column(String(128), nullable=False)
    notes = Column(Text, nullable=True)
    insertion_mark = Column(String(128), nullable=True)
    original_source = Column(String(512), nullable=True)
    content_bytes = Column(LargeBinary, nullable=False)
    content_hash = Column(String(128), nullable=False)
    mime_type = Column(String(128), nullable=False, default="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    __table_args__ = (
        CheckConstraint("scope IN ('system', 'pool', 'user')", name="ck_clinic_template_scope"),
        UniqueConstraint("scope", "scope_id", "name", "version", name="uq_clinic_template_scope_name_ver"),
    )


class ClinicCategorySet(db.Model):
    __tablename__ = "clinic_category_set"
    id = Column(String(36), primary_key=True, default=_new_id)
    scope = Column(String(16), nullable=False, default="system")
    scope_id = Column(String(36), nullable=False, default=SYSTEM_SCOPE_ID)
    name = Column(String(128), nullable=False)
    categories_json = Column(Text, nullable=False)
    is_required = Column(Boolean, nullable=False, default=False)
    target_field = Column(String(64), nullable=False, default="notes")
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    __table_args__ = (CheckConstraint("scope IN ('system', 'pool', 'user')", name="ck_clinic_category_set_scope"),)

    @property
    def categories(self) -> list[str]:
        return json.loads(self.categories_json)


class ClinicMetadataField(db.Model):
    __tablename__ = "clinic_metadata_field"
    id = Column(String(36), primary_key=True, default=_new_id)
    name = Column(String(64), nullable=False, unique=True)
    label = Column(String(128), nullable=False)
    field_type = Column(String(32), nullable=False, default="string")
    source_type = Column(String(32), nullable=False, default="form")
    xml_path = Column(String(256), nullable=True)
    display_format = Column(String(64), nullable=True)
    is_required = Column(Boolean, nullable=False, default=False)
    default_value = Column(Text, nullable=True)
    validation_regex = Column(String(256), nullable=True)
    position = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ClinicRegexRule(db.Model):
    __tablename__ = "clinic_regex_rule"
    id = Column(String(36), primary_key=True, default=_new_id)
    scope = Column(String(16), nullable=False, default="system")
    scope_id = Column(String(36), nullable=False, default=SYSTEM_SCOPE_ID)
    name = Column(String(128), nullable=False)
    pattern = Column(String(512), nullable=False)
    target_field = Column(String(64), nullable=False)
    replacement = Column(String(256), nullable=True)
    priority_order = Column(Integer, nullable=False, default=100)
    max_input_length = Column(Integer, nullable=False, default=10000)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    __table_args__ = (
        CheckConstraint(
            "target_field IN ('title','dictation_name','remarks','notes','priority','urgency','deadline','due_date')",
            name="ck_clinic_regex_target",
        ),
    )


PRESET_TABLES = (ClinicPreset, ClinicTemplate, ClinicCategorySet, ClinicMetadataField, ClinicRegexRule)


def create_tables(bind=None) -> None:
    db.metadata.create_all(bind=bind or db.engine, tables=[m.__table__ for m in PRESET_TABLES], checkfirst=True)


# Hierarchy Resolution Service (PAR-CFG-001)
@dataclass
class ResolvedPresets:
    values: dict[str, Any]
    origins: dict[str, str]
    locked: set[str]

    def to_dict(self) -> dict[str, Any]:
        return {"values": self.values, "origins": self.origins, "locked": list(self.locked)}


def set_preset(
    key: str, value: Any, scope: str = "system", scope_id: str | int | None = None,
    is_locked: bool = False, session=None,
) -> ClinicPreset:
    s = session or db.session
    sid = SYSTEM_SCOPE_ID if scope == "system" else str(scope_id)
    if scope == "system" and scope_id is not None and scope_id != SYSTEM_SCOPE_ID:
        raise PresetError("System preset scope_id must be '__system__'.")
    if scope != "system" and (scope_id is None or str(scope_id).strip() == ""):
        raise PresetError(f"Scope '{scope}' requires a valid scope_id.")

    existing = s.query(ClinicPreset).filter_by(scope=scope, scope_id=sid, key=key).first()
    val_json = json.dumps(value)
    if existing:
        existing.value_json = val_json
        existing.is_locked = is_locked
        existing.updated_at = _utcnow()
        preset = existing
    else:
        preset = ClinicPreset(scope=scope, scope_id=sid, key=key, value_json=val_json, is_locked=is_locked)
        s.add(preset)
    s.flush()
    return preset


def resolve_presets(
    user_id: str | int | None = None, pool_id: str | None = None,
    local_overrides: Mapping[str, Any] | None = None, session=None,
) -> ResolvedPresets:
    s = session or db.session
    values = dict(SYSTEM_DEFAULTS)
    origins = {k: "system" for k in values}
    locked_keys: set[str] = set()
    locked_by_map: dict[str, str] = {}

    for row in s.query(ClinicPreset).filter_by(scope="system", scope_id=SYSTEM_SCOPE_ID).all():
        values[row.key] = json.loads(row.value_json)
        origins[row.key] = "system"
        if row.is_locked:
            locked_keys.add(row.key)
            locked_by_map[row.key] = "system"

    if pool_id:
        for row in s.query(ClinicPreset).filter_by(scope="pool", scope_id=str(pool_id)).all():
            if row.key in locked_keys and locked_by_map[row.key] == "system":
                continue
            values[row.key] = json.loads(row.value_json)
            origins[row.key] = "pool"
            if row.is_locked:
                locked_keys.add(row.key)
                locked_by_map[row.key] = "pool"

    if user_id:
        for row in s.query(ClinicPreset).filter_by(scope="user", scope_id=str(user_id)).all():
            if row.key in locked_keys:
                continue
            values[row.key] = json.loads(row.value_json)
            origins[row.key] = "user"

    if local_overrides:
        for k, v in local_overrides.items():
            ck = GERMAN_FIELD_ALIASES.get(k, k)
            if ck in locked_keys:
                if v != values.get(ck):
                    raise LockedFieldError(ck, locked_by=locked_by_map.get(ck, "system"))
            else:
                values[ck] = v
                origins[ck] = "local"

    return ResolvedPresets(values=values, origins=origins, locked=locked_keys)


# Templates Service (PAR-CFG-002)
def save_template(
    name: str, content_bytes: bytes, scope: str = "system", scope_id: str | int | None = None,
    notes: str | None = None, insertion_mark: str | None = None, original_source: str | None = None,
    mime_type: str = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    session=None,
) -> ClinicTemplate:
    s = session or db.session
    sid = SYSTEM_SCOPE_ID if scope == "system" else str(scope_id)
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    latest = s.query(ClinicTemplate).filter_by(scope=scope, scope_id=sid, name=name).order_by(ClinicTemplate.version.desc()).first()
    tmpl = ClinicTemplate(
        scope=scope, scope_id=sid, name=name, notes=notes, insertion_mark=insertion_mark,
        original_source=original_source, content_bytes=content_bytes, content_hash=content_hash,
        mime_type=mime_type, version=(latest.version + 1) if latest else 1, is_active=True,
    )
    s.add(tmpl)
    s.flush()
    return tmpl


def list_templates(
    user_id: str | int | None = None, pool_ids: list[str] | None = None,
    include_system: bool = True, session=None,
) -> list[ClinicTemplate]:
    s = session or db.session
    conds = []
    if include_system:
        conds.append((ClinicTemplate.scope == "system") & (ClinicTemplate.scope_id == SYSTEM_SCOPE_ID))
    if pool_ids:
        conds.append((ClinicTemplate.scope == "pool") & ClinicTemplate.scope_id.in_([str(p) for p in pool_ids]))
    if user_id:
        conds.append((ClinicTemplate.scope == "user") & (ClinicTemplate.scope_id == str(user_id)))
    if not conds:
        return []
    from sqlalchemy import or_
    return s.query(ClinicTemplate).filter(ClinicTemplate.is_active.is_(True), or_(*conds)).order_by(
        ClinicTemplate.name, ClinicTemplate.version.desc()
    ).all()


# Shipping Categories Service (PAR-CFG-003)
def save_category_set(
    name: str, categories: list[str], is_required: bool = False, scope: str = "system",
    scope_id: str | int | None = None, target_field: str = "notes", session=None,
) -> ClinicCategorySet:
    s = session or db.session
    sid = SYSTEM_SCOPE_ID if scope == "system" else str(scope_id)
    cset = ClinicCategorySet(
        name=name, categories_json=json.dumps(categories), is_required=is_required,
        scope=scope, scope_id=sid, target_field=target_field,
    )
    s.add(cset)
    s.flush()
    return cset


def validate_category_requirement(category_set: ClinicCategorySet, text_value: str) -> bool:
    if not category_set.is_required:
        return True
    cleaned = (text_value or "").strip().lower()
    if not cleaned:
        raise ValidationError(f"Category selection is mandatory for set '{category_set.name}'.", field=category_set.target_field)
    if any(cat.lower() in cleaned for cat in category_set.categories):
        return True
    raise ValidationError(
        f"Value '{text_value}' missing required category from '{category_set.name}' ({category_set.categories}).",
        field=category_set.target_field,
    )


# RegEx Rules Service (PAR-CFG-004)
def save_regex_rule(
    name: str, pattern: str, target_field: str, replacement: str | None = None,
    priority_order: int = 100, max_input_length: int = 10000, scope: str = "system",
    scope_id: str | int | None = None, session=None,
) -> ClinicRegexRule:
    if target_field in FORBIDDEN_REGEX_TARGETS:
        raise ForbiddenTargetError(target_field)
    if target_field not in ALLOWED_REGEX_TARGETS:
        raise ValidationError(f"Target '{target_field}' is not permitted.")
    try:
        re.compile(pattern)
    except re.error as e:
        raise RegexValidationError("pattern", pattern, str(e))

    s = session or db.session
    sid = SYSTEM_SCOPE_ID if scope == "system" else str(scope_id)
    rule = ClinicRegexRule(
        name=name, pattern=pattern, target_field=target_field, replacement=replacement,
        priority_order=priority_order, max_input_length=max_input_length,
        scope=scope, scope_id=sid, is_active=True,
    )
    s.add(rule)
    s.flush()
    return rule


def _execute_regex_rules(
    rules: list[ClinicRegexRule | dict[str, Any]], text: str, max_input_length: int = 10000,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    capped_len = max_input_length if max_input_length > 0 else 100000
    truncated = len(text) > capped_len
    target_text = text[:capped_len]

    def _get(rule, attr, default=None):
        return getattr(rule, attr, rule.get(attr, default) if isinstance(rule, dict) else default)

    sorted_rules = sorted(rules, key=lambda r: _get(r, "priority_order", 100))
    results: dict[str, Any] = {}
    matches_info: list[dict[str, Any]] = []

    for r in sorted_rules:
        target = _get(r, "target_field")
        if target in FORBIDDEN_REGEX_TARGETS:
            raise ForbiddenTargetError(target)
        if target not in ALLOWED_REGEX_TARGETS:
            raise ValidationError(f"Target '{target}' is not permitted.")

        pattern = _get(r, "pattern")
        try:
            compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        except re.error as e:
            raise RegexValidationError("pattern", pattern, str(e))

        m = compiled.search(target_text)
        if m:
            rep = _get(r, "replacement")
            val = m.expand(rep) if rep else (m.group(1) if m.groups() else m.group(0))
            if target in ("priority", "urgency"):
                final_val = PRIORITY_NAME_MAP.get(val.strip().lower(), PRIORITY_NORMAL)
            else:
                final_val = val.strip()

            results[target] = final_val
            matches_info.append({
                "rule_name": _get(r, "name", "rule"), "target_field": target,
                "matched_text": m.group(0), "extracted_value": val, "applied_value": final_val,
            })
    return results, matches_info, truncated


def apply_regex_rules(
    rules: list[ClinicRegexRule | dict[str, Any]], text: str, max_input_length: int = 10000,
) -> dict[str, Any]:
    return _execute_regex_rules(rules, text, max_input_length)[0]


def preview_regex_rules(
    rules: list[ClinicRegexRule | dict[str, Any]], sample_text: str, max_input_length: int = 10000,
) -> dict[str, Any]:
    res, info, trunc = _execute_regex_rules(rules, sample_text, max_input_length)
    return {"resulting_fields": res, "matches": info, "truncated": trunc}


# Metadata Fields & Safe XML Extraction (PAR-CFG-005)
def extract_xml_metadata(xml_data: str | bytes, field_definitions: list[ClinicMetadataField]) -> dict[str, Any]:
    xml_bytes = xml_data.encode("utf-8") if isinstance(xml_data, str) else xml_data
    if b"<!ENTITY" in xml_bytes or b"<!DOCTYPE" in xml_bytes:
        raise ValidationError("XML with DOCTYPE or entity declarations is rejected for security.")
    try:
        root = ET.fromstring(xml_bytes)  # nosec B314
    except ET.ParseError as e:
        raise ValidationError(f"Invalid XML metadata: {e}")

    extracted: dict[str, Any] = {}
    for fdef in field_definitions:
        if fdef.xml_path:
            elem = root.find(fdef.xml_path)
            if elem is not None and elem.text is not None:
                extracted[fdef.name] = elem.text.strip()
            elif fdef.default_value is not None:
                extracted[fdef.name] = fdef.default_value
    return extracted

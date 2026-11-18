"""ASR profiles per author, hotword sets and proven training runs (P10).

Two things are kept apart on purpose (clinic-spec/CLAUDE.md section 6):

* Configuration: ``ClinicAsrProfile`` (language, model, recognition options,
  hotword set references) and ``ClinicHotwordSet``. This is decoder bias and
  routing data. It is never acoustic training.
* Evidence: ``ClinicAsrTrainingRun`` holds only runs that really happened
  (time, data volume, a reference to the result). Nothing in this module
  creates a run as a side effect of a profile or hotword change.
  ``asr_status`` answers the two questions separately.

Every profile change appends a ``ClinicAsrProfileVersion`` row (who, when,
what changed, full snapshot). A call that changes nothing writes no version.

Hotword merge (``effective_hotwords``): the profile lists set ids in order.
Pool sets (only those of the pool the caller names, with current author
membership required in clinic mode) are applied first, then author sets.
Later wins per term, so an author set beats a pool set and a
later reference beats an earlier one inside the same scope. Terms compare
case-insensitively after Unicode NFC. An author set can list ``blocked``
terms; they remove the term from everything applied before it.

shortcut: history rows are append-only in the ORM only, not by DB trigger.
No route or UI is wired here.
"""

from __future__ import annotations

import json
import unicodedata
import uuid

from sqlalchemy import CheckConstraint, UniqueConstraint, event

from src.clinic.config import clinic_mode_enabled
from src.clinic.pools import AwareDateTime, ClinicAuthorPoolMember, _utcnow
from src.database import db
from src.models import User

SCOPES = ("author", "pool")
_SCOPE_RANK = {"pool": 0, "author": 1}
SAVE_RULES = ("never", "ask", "always")
SOURCES = ("headset", "recorder")
TRAINING_KINDS = ("acoustic", "language_model")
_NAME_MAX = 100
_LANG_MAX = 16
_MODEL_MAX = 200
_TERM_MAX = 200


class AsrProfileError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _new_id():
    return str(uuid.uuid4())


def _ts():
    return db.Column(AwareDateTime(), nullable=False, default=_utcnow)


class ClinicHotwordSet(db.Model):
    __tablename__ = "clinic_hotword_set"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    scope = db.Column(db.String(10), nullable=False)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    pool_id = db.Column(db.String(36), nullable=True)  # author-pool id, no FK (pools.py)
    # "u:<user id>" or "p:<pool id>": NULLs are distinct in a unique index
    scope_key = db.Column(db.String(60), nullable=False)
    name = db.Column(db.String(_NAME_MAX), nullable=False)
    words_json = db.Column(db.Text, nullable=False, default="[]")
    blocked_json = db.Column(db.Text, nullable=False, default="[]")
    revision = db.Column(db.Integer, nullable=False, default=1)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
    updated_at = _ts()

    __table_args__ = (
        CheckConstraint("scope in ('author','pool')", name="ck_clinic_hotword_scope"),
        CheckConstraint(
            "(scope = 'author' and owner_user_id is not null and pool_id is null)"
            " or (scope = 'pool' and pool_id is not null and owner_user_id is null)",
            name="ck_clinic_hotword_owner",
        ),
        UniqueConstraint("scope_key", "name", name="uq_clinic_hotword_set_name"),
    )

    @property
    def words(self):
        return json.loads(self.words_json)

    @property
    def blocked(self):
        return json.loads(self.blocked_json)


class ClinicAsrProfile(db.Model):
    __tablename__ = "clinic_asr_profile"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)
    language = db.Column(db.String(_LANG_MAX), nullable=True)
    model = db.Column(db.String(_MODEL_MAX), nullable=True)
    options_json = db.Column(db.Text, nullable=False, default="{}")
    hotword_set_ids_json = db.Column(db.Text, nullable=False, default="[]")
    version = db.Column(db.Integer, nullable=False, default=1)
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
    updated_at = _ts()

    @property
    def options(self):
        return json.loads(self.options_json)

    @property
    def hotword_set_ids(self):
        return json.loads(self.hotword_set_ids_json)


class ClinicAsrProfileVersion(db.Model):
    __tablename__ = "clinic_asr_profile_version"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    profile_id = db.Column(db.String(36), db.ForeignKey("clinic_asr_profile.id"),
                           nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    changed_at = _ts()
    changes_json = db.Column(db.Text, nullable=False)  # {field: [old, new]}
    snapshot_json = db.Column(db.Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("profile_id", "version", name="uq_clinic_asr_profile_version"),
    )

    @property
    def changes(self):
        return json.loads(self.changes_json)

    @property
    def snapshot(self):
        return json.loads(self.snapshot_json)


class ClinicAsrTrainingRun(db.Model):
    """A training or adaptation run that really took place. Evidence only."""

    __tablename__ = "clinic_asr_training_run"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    engine = db.Column(db.String(_MODEL_MAX), nullable=False)
    started_at = db.Column(AwareDateTime(), nullable=False)
    finished_at = db.Column(AwareDateTime(), nullable=False)
    audio_seconds = db.Column(db.Float, nullable=False)
    text_chars = db.Column(db.Integer, nullable=False)
    dictation_count = db.Column(db.Integer, nullable=False)
    result_ref = db.Column(db.String(500), nullable=False)
    recorded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    recorded_at = _ts()

    __table_args__ = (
        CheckConstraint("kind in ('acoustic','language_model')", name="ck_clinic_training_kind"),
        CheckConstraint("audio_seconds >= 0 and text_chars >= 0 and dictation_count >= 0",
                        name="ck_clinic_training_volume"),
        CheckConstraint("length(result_ref) > 0", name="ck_clinic_training_ref"),
    )


@event.listens_for(ClinicAsrProfileVersion, "before_update")
@event.listens_for(ClinicAsrProfileVersion, "before_delete")
@event.listens_for(ClinicAsrTrainingRun, "before_update")
@event.listens_for(ClinicAsrTrainingRun, "before_delete")
def _append_only(_mapper, _connection, _target):
    raise AsrProfileError("append_only")


# ---------------------------------------------------------------- helpers

def _norm(term):
    return unicodedata.normalize("NFC", term).strip()


def _key(term):
    return _norm(term).casefold()


def _clean_terms(terms):
    """Validate a list of terms; keep order, drop duplicates (first wins)."""
    if not isinstance(terms, (list, tuple)):
        raise AsrProfileError("terms_invalid")
    out, seen = [], set()
    for term in terms:
        if not isinstance(term, str):
            raise AsrProfileError("terms_invalid")
        term = _norm(term)
        if not term or len(term) > _TERM_MAX:
            raise AsrProfileError("terms_invalid")
        if _key(term) not in seen:
            seen.add(_key(term))
            out.append(term)
    return out


def _known_user(user_id):
    if db.session.get(User, user_id) is None:
        raise AsrProfileError("unknown_user")
    return user_id


def _can_use_pool(user_id: int, pool_id: str) -> bool:
    if not clinic_mode_enabled():
        return True
    return ClinicAuthorPoolMember.query.filter_by(pool_id=pool_id, user_id=user_id).first() is not None


def _clean_options(options):
    """Recognition options from the behaviour reference (PAR-ASR-002/003/006).

    Deliberately no option for "optimise profile from corrections": that would
    suggest training, which this engine does not do.
    """
    if not isinstance(options, dict):
        raise AsrProfileError("options_invalid")
    out = {}
    for name, value in options.items():
        if name == "separate_offline_profile":
            ok = isinstance(value, bool)
        elif name == "source":
            ok = value in SOURCES
        elif name == "save_rule":
            ok = value in SAVE_RULES
        elif name == "skip_wait_minutes":
            ok = value is None or (
                isinstance(value, int) and not isinstance(value, bool) and value > 0
            )
        else:
            raise AsrProfileError("option_unknown")
        if not ok:
            raise AsrProfileError("option_invalid")
        out[name] = value
    return out


def _snapshot(profile):
    return {
        "language": profile.language,
        "model": profile.model,
        "options": profile.options,
        "hotword_set_ids": profile.hotword_set_ids,
    }


# ---------------------------------------------------------------- hotword sets

def create_hotword_set(name, words, *, actor_id, owner_user_id=None, pool_id=None,
                       blocked=()):
    """Create a named set for one author (``owner_user_id``) or one pool (``pool_id``)."""
    if (owner_user_id is None) == (pool_id is None):
        raise AsrProfileError("scope_invalid")
    name = (name or "").strip()
    if not name or len(name) > _NAME_MAX:
        raise AsrProfileError("name_invalid")
    _known_user(actor_id)
    if owner_user_id is not None:
        _known_user(owner_user_id)
    blocked = _clean_terms(list(blocked))
    if pool_id is not None and blocked:
        raise AsrProfileError("blocked_author_only")
    row = ClinicHotwordSet(
        scope="author" if owner_user_id is not None else "pool",
        owner_user_id=owner_user_id, pool_id=pool_id, name=name,
        scope_key=f"u:{owner_user_id}" if owner_user_id is not None else f"p:{pool_id}",
        words_json=json.dumps(_clean_terms(words), ensure_ascii=False),
        blocked_json=json.dumps(blocked, ensure_ascii=False),
        created_by=actor_id,
    )
    db.session.add(row)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if exc.__class__.__name__ == "IntegrityError":
            raise AsrProfileError("name_taken") from exc
        raise
    return row


def update_hotword_set(set_id, *, words=None, blocked=None):
    row = db.session.get(ClinicHotwordSet, set_id)
    if row is None:
        raise AsrProfileError("set_unknown")
    if words is not None:
        row.words_json = json.dumps(_clean_terms(words), ensure_ascii=False)
    if blocked is not None:
        blocked = _clean_terms(list(blocked))
        if row.scope == "pool" and blocked:
            raise AsrProfileError("blocked_author_only")
        row.blocked_json = json.dumps(blocked, ensure_ascii=False)
    row.revision += 1
    row.updated_at = _utcnow()
    db.session.commit()
    return row


def effective_hotwords(user_id, pool_id=None):
    """Merged hotword list for an author (see module docstring for the rule).

    Returns terms in application order, the winner's spelling kept. Sets that
    are not owned by the author or do not belong to an authorized ``pool_id``
    are skipped.
    """
    profile = ClinicAsrProfile.query.filter_by(user_id=user_id).first()
    if profile is None:
        return []
    if pool_id is not None and not _can_use_pool(user_id, pool_id):
        pool_id = None
    sets = []
    for position, set_id in enumerate(profile.hotword_set_ids):
        row = db.session.get(ClinicHotwordSet, set_id)
        if row is None:
            continue
        if row.scope == "author" and row.owner_user_id != user_id:
            continue
        if row.scope == "pool" and (pool_id is None or row.pool_id != pool_id):
            continue
        sets.append((_SCOPE_RANK[row.scope], position, row))
    merged = {}
    for _rank, _pos, row in sorted(sets, key=lambda item: (item[0], item[1])):
        for term in row.blocked:
            merged.pop(_key(term), None)
        for term in row.words:
            merged.pop(_key(term), None)  # re-insert: moves to the winner's position
            merged[_key(term)] = term
    return list(merged.values())


# ---------------------------------------------------------------- profile

_UNSET = object()


def set_profile(user_id, *, actor_id, language=_UNSET, model=_UNSET, options=_UNSET,
                hotword_set_ids=_UNSET):
    """Create or change the author's profile. Only given fields change.

    ``options`` replaces the whole options dict. A call with no effective
    change returns the profile unchanged and writes no version row.
    """
    _known_user(user_id)
    _known_user(actor_id)
    profile = ClinicAsrProfile.query.filter_by(user_id=user_id).first()
    new = {}
    if language is not _UNSET:
        if language is not None and (not isinstance(language, str) or not language.strip()
                                     or len(language) > _LANG_MAX):
            raise AsrProfileError("language_invalid")
        new["language"] = language.strip() if language else None
    if model is not _UNSET:
        if model is not None and (not isinstance(model, str) or not model.strip()
                                  or len(model) > _MODEL_MAX):
            raise AsrProfileError("model_invalid")
        new["model"] = model.strip() if model else None
    if options is not _UNSET:
        new["options"] = _clean_options(options)
    if hotword_set_ids is not _UNSET:
        ids = list(hotword_set_ids)
        if len(set(ids)) != len(ids):
            raise AsrProfileError("sets_invalid")
        for set_id in ids:
            row = db.session.get(ClinicHotwordSet, set_id)
            if row is None:
                raise AsrProfileError("set_unknown")
            if row.scope == "author" and row.owner_user_id != user_id:
                raise AsrProfileError("set_foreign")
            if row.scope == "pool" and not _can_use_pool(user_id, row.pool_id):
                raise AsrProfileError("set_foreign")
        new["hotword_set_ids"] = ids

    created = profile is None
    if created:
        profile = ClinicAsrProfile(user_id=user_id, updated_by=actor_id, version=0)
        old = {"language": None, "model": None, "options": {}, "hotword_set_ids": []}
    else:
        old = _snapshot(profile)
    changes = {k: [old[k], v] for k, v in new.items() if old[k] != v}
    if not changes and not created:
        return profile
    profile.language = new.get("language", old["language"])
    profile.model = new.get("model", old["model"])
    profile.options_json = json.dumps(new.get("options", old["options"]), sort_keys=True)
    profile.hotword_set_ids_json = json.dumps(new.get("hotword_set_ids", old["hotword_set_ids"]))
    profile.version += 1
    profile.updated_by = actor_id
    profile.updated_at = _utcnow()
    db.session.add(profile)
    db.session.flush()
    db.session.add(ClinicAsrProfileVersion(
        profile_id=profile.id, version=profile.version, actor_user_id=actor_id,
        changes_json=json.dumps(changes, sort_keys=True, ensure_ascii=False),
        snapshot_json=json.dumps(_snapshot(profile), sort_keys=True, ensure_ascii=False),
    ))
    db.session.commit()
    return profile


def profile_history(user_id):
    profile = ClinicAsrProfile.query.filter_by(user_id=user_id).first()
    if profile is None:
        return []
    return (ClinicAsrProfileVersion.query.filter_by(profile_id=profile.id)
            .order_by(ClinicAsrProfileVersion.version).all())


# ---------------------------------------------------------------- training evidence

def record_training_run(user_id, *, kind, engine, started_at, finished_at, audio_seconds,
                        text_chars, dictation_count, result_ref, actor_id):
    """Record a run that took place. Needs a real result reference.

    This is the only way to create evidence. Profile calls never reach it.
    """
    _known_user(user_id)
    _known_user(actor_id)
    if kind not in TRAINING_KINDS:
        raise AsrProfileError("kind_invalid")
    if not isinstance(engine, str) or not engine.strip():
        raise AsrProfileError("engine_invalid")
    if not isinstance(result_ref, str) or not result_ref.strip():
        raise AsrProfileError("result_ref_required")
    for stamp in (started_at, finished_at):
        if stamp is None or stamp.tzinfo is None:
            raise AsrProfileError("naive_datetime")
    if finished_at < started_at:
        raise AsrProfileError("time_order")
    if audio_seconds < 0 or text_chars < 0 or dictation_count < 0:
        raise AsrProfileError("volume_invalid")
    run = ClinicAsrTrainingRun(
        user_id=user_id, kind=kind, engine=engine.strip(), started_at=started_at,
        finished_at=finished_at, audio_seconds=float(audio_seconds),
        text_chars=int(text_chars), dictation_count=int(dictation_count),
        result_ref=result_ref.strip(), recorded_by=actor_id,
    )
    db.session.add(run)
    db.session.commit()
    return run


def asr_status(user_id):
    """Two separate answers: profile configured, and training proven."""
    profile = ClinicAsrProfile.query.filter_by(user_id=user_id).first()
    runs = (ClinicAsrTrainingRun.query.filter_by(user_id=user_id)
            .order_by(ClinicAsrTrainingRun.finished_at).all())
    return {
        "profile_configured": profile is not None,
        "profile_version": profile.version if profile is not None else None,
        "training_proven": bool(runs),
        "training_runs": len(runs),
        "last_training_at": runs[-1].finished_at if runs else None,
        "note": "profile settings and hotwords are decoder bias, not acoustic training",
    }

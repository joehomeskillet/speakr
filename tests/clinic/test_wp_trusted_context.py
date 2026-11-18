"""Synthetic grant binding tests; these do not establish a real KIS contract."""

from dataclasses import replace
from datetime import timedelta

import pytest

from src.app import app, db
from src.clinic import context_grants as grants
from src.clinic.kis import AdapterTimeout, ContextCheck, DisabledAdapter, MockKisAdapter
from src.clinic.models import ClinicContextRevision
from src.clinic.release import ClinicReleaseSupersession, active_release
from src.models import User

from test_release_binding import T0, _dictation, _release, _setup


@pytest.fixture
def binding(clinic_on, monkeypatch):
    monkeypatch.setenv("CLINIC_PROFILE", "test")
    with app.app_context():
        user, dictation, audio, transcript, old = _setup()
        release = _release(user, dictation, audio, transcript, old)
        db.session.commit()
        evidence = grants.VerifiedContextGrant(
            "test-issuer", "clinic", user.id, dictation.id, "kis", "operator-a",
            "site", "case-2", "patient-2", "bind_existing", "nonce-a",
            T0 + timedelta(seconds=30),
        )
        adapter = MockKisAdapter(known_contexts={("kis", "site", "case-2", "patient-2")})
        args = dict(dictation_id=dictation.id, actor_user_id=user.id, tenant="site",
                    expected_version=0, issuer="test-issuer", audience="clinic", now=T0,
                    resolve=lambda opaque, actor: evidence if opaque == "opaque-a" else None,
                    adapter=adapter, authorize_binding=lambda row, actor: row.author_user_id == actor)
        yield args, evidence, dictation, old, release
        db.session.rollback()


def test_valid_binding_preserves_history_and_supersedes_release(binding):
    args, evidence, dictation, old, release = binding
    revision = grants.bind_context_grant("opaque-a", **args)
    db.session.commit()
    proof = db.session.get(grants.ClinicContextGrantUse, revision.id)
    assert (revision.dictation_id, revision.parent_revision_id) == (dictation.id, old.id)
    assert (revision.case_ref, revision.patient_ref, old.case_ref) == ("case-2", "patient-2", "case-1")
    assert (proof.issuer, proof.audience, proof.actor_user_id, proof.subject_reference) == (
        evidence.issuer, evidence.audience, evidence.user_id, evidence.subject_reference,
    )
    assert dictation.version_counter == 1
    assert release.context_revision_id == old.id
    assert active_release(dictation) is None
    assert ClinicReleaseSupersession.query.filter_by(release_id=release.id).one().reason == "context_changed"
    with pytest.raises(grants.ContextGrantError, match="grant_replayed"):
        grants.bind_context_grant("opaque-a", **dict(args, expected_version=1))
    with pytest.raises(grants.ContextGrantError, match="context_is_append_only"):
        revision.case_ref = "other"
        db.session.flush()


@pytest.mark.parametrize(("field", "value", "code"), [
    ("issuer", "other", "invalid_grant"),
    ("audience", "other", "invalid_grant"),
    ("user_id", -1, "invalid_grant"),
    ("tenant_reference", "other", "invalid_grant"),
    ("dictation_id", "other", "invalid_grant"),
    ("purpose", "new_dictation", "invalid_grant"),
    ("nonce", "", "invalid_grant"),
    ("expires_at", T0, "invalid_grant"),
    ("expires_at", T0 + timedelta(seconds=61), "invalid_grant"),
    ("case_reference", "wrong-case", "case_unresolved"),
    ("patient_reference", "wrong-patient", "case_unresolved"),
])
def test_refused_evidence_preserves_the_previous_state(binding, field, value, code):
    args, evidence, dictation, _, release = binding
    with pytest.raises(grants.ContextGrantError, match=code):
        grants.bind_context_grant("opaque-a", **dict(args, resolve=lambda *_: replace(evidence, **{field: value})))
    assert ClinicContextRevision.query.filter_by(dictation_id=dictation.id).count() == 1
    assert grants.ClinicContextGrantUse.query.count() == 0
    assert dictation.version_counter == 0
    assert active_release(dictation) is release


@pytest.mark.parametrize("raw", [{"X-KIS-Case": "case-2"}, "transcript: case-2", "window: case-2", "<div>case-2</div>"])
def test_browser_observations_are_not_evidence(binding, raw):
    args, *_ = binding
    with pytest.raises(grants.ContextGrantError, match="invalid_grant"):
        grants.bind_context_grant(raw, **args)


def test_stale_denied_or_unavailable_grant_can_be_retried(binding, monkeypatch):
    args, evidence, *_ = binding
    for overrides, code in [
        ({"expected_version": 1}, "stale_version"),
        ({"authorize_binding": lambda *_: False}, "binding_denied"),
        ({"adapter": DisabledAdapter()}, "context_capability_unavailable"),
    ]:
        with pytest.raises(grants.ContextGrantError, match=code):
            grants.bind_context_grant("opaque-a", **dict(args, **overrides))

    for name, value in [("CLINIC_MODE", "off"), ("CLINIC_PROFILE", "production")]:
        with monkeypatch.context() as patch:
            patch.setenv(name, value)
            with pytest.raises(grants.ContextGrantError, match="context_capability_unavailable"):
                grants.bind_context_grant("opaque-a", **args)

    def timeout(_context):
        raise AdapterTimeout

    with monkeypatch.context() as patch:
        patch.setattr(args["adapter"], "check_context", timeout)
        with pytest.raises(grants.ContextGrantError, match="context_capability_unavailable"):
            grants.bind_context_grant("opaque-a", **args)

    def check_actor(context):
        return ContextCheck(context["actor_user_id"] == evidence.user_id
                            and context["subject_reference"] == evidence.subject_reference
                            and context["purpose"] == "bind_existing")

    monkeypatch.setattr(args["adapter"], "check_context", lambda _: ContextCheck(False))
    with pytest.raises(grants.ContextGrantError, match="case_unresolved"):
        grants.bind_context_grant("opaque-a", **args)
    monkeypatch.setattr(args["adapter"], "check_context", check_actor)
    assert grants.bind_context_grant("opaque-a", **args).case_ref == "case-2"


def test_rollback_preserves_release_and_does_not_consume_grant(binding, monkeypatch):
    args, _, dictation, _, release = binding
    invalidate = grants.invalidate_on_change

    def fail_after_supersession(*args, **kwargs):
        invalidate(*args, **kwargs)
        raise RuntimeError("synthetic write failure")

    with monkeypatch.context() as patch:
        patch.setattr(grants, "invalidate_on_change", fail_after_supersession)
        with pytest.raises(RuntimeError, match="synthetic write failure"):
            grants.bind_context_grant("opaque-a", **args)
    db.session.commit()
    assert active_release(dictation) is release
    assert grants.ClinicContextGrantUse.query.count() == ClinicReleaseSupersession.query.count() == 0
    assert dictation.version_counter == 0
    grants.bind_context_grant("opaque-a", **args)
    db.session.rollback()
    assert active_release(dictation) is release
    assert grants.ClinicContextGrantUse.query.count() == 0


def test_unknown_first_case_remains_unresolved(binding):
    args, original_evidence, *_ = binding
    dictation = _dictation(db.session.get(User, original_evidence.user_id))
    evidence = replace(original_evidence, dictation_id=dictation.id, purpose="new_dictation")
    initial = dict(args, dictation_id=dictation.id, purpose="new_dictation")
    with pytest.raises(grants.ContextGrantError, match="case_unresolved"):
        grants.bind_context_grant("opaque-a", **dict(initial, resolve=lambda *_: replace(
            evidence, case_reference="unknown",
        )))
    assert ClinicContextRevision.query.filter_by(dictation_id=dictation.id).count() == 0
    revision = grants.bind_context_grant("opaque-a", **dict(initial, resolve=lambda *_: evidence))
    assert revision.parent_revision_id is None
    with pytest.raises(grants.ContextGrantError, match="grant_replayed"):
        grants.bind_context_grant("another-opaque-token", **dict(args, resolve=lambda *_: original_evidence))

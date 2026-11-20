"""Real P03/P07 release workflow using only synthetic accounts and the local MockKIS profile."""
from uuid import UUID, uuid4

import pytest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import test_wp_mount_correction as mount
from test_wp_mount_correction import live_server  # noqa: F401


@pytest.fixture
def review_scene(request, monkeypatch):
    """Capture the existing synthetic server's database for seed and final persistence checks."""
    from src.clinic import workflow
    from src.clinic.models import ClinicContextRevision, ClinicDictation
    from src.models import Recording

    database = {}
    original_env = mount._env

    def local_profile(path, clinic):
        env = original_env(path, clinic)
        database["url"] = env["SQLALCHEMY_DATABASE_URI"]
        return {**env, "CLINIC_PROFILE": "staging", "SPEAKR_SKIP_BACKGROUND_TASKS": "true"}

    monkeypatch.setattr(mount, "_env", local_profile)
    base, did = request.getfixturevalue("live_server")
    engine = create_engine(database["url"])
    with Session(engine) as session:
        row = session.get(ClinicDictation, did)
        # An imported case reference without a consumed KIS grant is untrusted.
        session.add(ClinicContextRevision(dictation_id=did, source_system="synthetic",
                                          tenant_scope="test", case_ref="SYN-0000", patient_ref="opaque"))
        rec = Recording(user_id=row.author_user_id, title="Separate synthetic rule")
        session.add(rec)
        session.flush()
        other = ClinicDictation(recording_id=rec.id, author_user_id=row.author_user_id, pool_id=row.pool_id)
        session.add(other)
        session.flush()
        session.add(workflow.ClinicWorkflowDictation(id=other.id, author_user_id=row.author_user_id,
                                                     pool_id=row.pool_id, status=workflow.DRAFT))
        other_id = other.id
        session.commit()
    try:
        yield base, did, other_id, engine
    finally:
        engine.dispose()


def _get(page, path):
    reply = page.request.get(path)
    assert reply.status == 200, reply.text()
    return reply.json()


def _headers(page, base):
    return {"X-CSRF-Token": _get(page, base + "/api/csrf-token")["csrf_token"],
            "Idempotency-Key": str(uuid4())}


def _publish(admin, base, path, approvers):
    policy = path + "/acceptance-policy"
    old = _get(admin, policy)["version"]
    reply = admin.request.put(policy, headers=_headers(admin, base) | {"If-Match": f'"{old}"'},
                              data={"acceptance_required": True, "approver_ids": approvers})
    assert reply.status == 200, reply.text()


def _command(page, base, did, review, name, fields):
    return page.request.post(base + "/api/clinic/v1/parity/commands", headers=_headers(page, base),
                             data={"command": name, "request_id": str(uuid4()),
                                   "expected_revision": review["revision"] + 1,
                                   "payload": {"dictation_id": did, **fields}})


def test_review_and_release_current_bound_versions(review_scene, tmp_path):
    from src.clinic.models import ClinicRelease

    base, did, other, engine = review_scene
    path = f"{base}/api/clinic/v1/dictations/{did}"
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = mount._login(browser, base, mount.TYPIST)
            admin = mount._login(browser, base, "admin213@local.test")
            detail = _get(page, path + "/detail")
            typist, author = detail["takeover"]["user_id"], detail["author"]["user_id"]
            initial = _get(page, path + "/review")
            # The caller is allowed to release but has not acquired any lease yet.
            body = initial["binding"]["release"] | {"lease_id": str(uuid4()), "lease_generation": 1,
                                                     "confirmed_content": True, "confirmed_context": True}
            refused = page.request.post(path + "/releases", data=body,
                                        headers=_headers(page, base) | {"If-Match": initial["etag"]})
            assert (refused.status, refused.json()["code"]) == (409, "LEASE_CONFLICT")

            page.get_by_test_id("open-link").filter(visible=True).first.click()
            text = page.get_by_test_id("text")
            page.locator("[data-action=acquire]").click()
            expect(text).to_be_editable()
            text.fill("Synthetischer und fachlich geprüfter Testtext.")
            page.get_by_test_id("save").click()
            expect(page.get_by_test_id("save-state")).to_have_attribute("data-state", "saved")

            current = _get(page, path + "/review")
            held = _get(page, path + "/leases")
            release = current["binding"]["release"] | {"confirmed_content": True, "confirmed_context": True,
                                                        "lease_id": held["lease_id"], "lease_generation": held["generation"]}
            denied = page.request.post(path + "/releases", data=release,
                                       headers=_headers(page, base) | {"If-Match": current["etag"]})
            assert (denied.status, denied.json()["code"]) == (422, "UNTRUSTED_CONTEXT")
            trusted = page.request.post(path + "/context", headers=_headers(page, base),
                                        data={"case_reference": "SYN-0130"})
            assert trusted.status == 201, trusted.text()

            _publish(admin, base, path, [author])
            stale = _get(page, path + "/review")["binding"]["acceptance_rule_id"]
            other_path = f"{base}/api/clinic/v1/dictations/{other}"
            _publish(admin, base, other_path, [typist])
            foreign = _get(page, other_path + "/detail")["binding"]["acceptanceRuleId"]
            _publish(admin, base, path, [typist])
            ready = _get(page, path + "/review")
            binding = ready["binding"]
            rule = binding["acceptance_rule_id"]
            assert len({rule, stale, foreign}) == 3 and all(UUID(i).version == 5 for i in (rule, stale, foreign))
            fields = {k: binding[k] for k in ("text_revision_id", "context_revision_id", "acceptance_rule_id")}
            for invalid in (stale, foreign):
                refused = _command(page, base, did, ready, "request_acceptance", fields | {"acceptance_rule_id": invalid})
                assert refused.status in (400, 409, 422), refused.text()
                assert _get(page, path + "/review")["revision"] == ready["revision"]

            page.get_by_test_id("clinic-dictation-link-detail").click()
            with page.expect_response(lambda r: r.url.endswith("/parity/commands") and r.request.method == "POST") as sent:
                page.get_by_role("button", name="Zur Abnahme einreichen", exact=True).click()
            assert sent.value.status == 200, sent.value.text()
            assert sent.value.request.post_data_json["payload"]["acceptance_rule_id"] == rule
            waiting = _get(page, path + "/review")
            assert waiting["status"] == "abzunehmen"
            outsider = mount._login(browser, base, mount.STRANGER)
            denied = _command(outsider, base, did, waiting, "decide_acceptance",
                              {"text_revision_id": binding["text_revision_id"], "decision": "accept"})
            assert (denied.status, denied.json()["code"]) == (403, "POLICY_DENIED")

            page.get_by_test_id("clinic-dictation-link-review").click()
            review = page.get_by_test_id("clinic-review-view")
            review.locator("[data-action=accept]").click()
            with page.expect_response(lambda r: r.url.endswith("/parity/commands") and r.request.method == "POST") as accepted:
                review.get_by_test_id("confirm").click()
            assert accepted.value.status == 200, accepted.value.text()
            assert _get(page, path + "/review")["status"] == "abgenommen"
            review.locator("[data-action=release]").click()
            for checkbox in review.get_by_role("checkbox").all():
                checkbox.check()
            page.set_viewport_size({"width": 1440, "height": 900})
            page.screenshot(path=tmp_path / "p07-confirm-1440.png")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=tmp_path / "p07-confirm-390.png")
            with page.expect_response(lambda r: r.url.endswith("/releases") and r.request.method == "POST") as released:
                review.get_by_test_id("confirm").click()
            assert released.value.status == 201, released.value.text()
            assert _get(page, path + "/review")["status"] == "fertiggestellt"
            with Session(engine) as session:
                row = session.query(ClinicRelease).filter_by(dictation_id=did).one()
                assert (row.audio_revision_id, row.transcript_revision_id, row.context_revision_id, row.actor_user_id) == (
                    binding["audio_revision_id"], binding["text_revision_id"], binding["context_revision_id"], typist)
            assert not page.js_errors
        finally:
            browser.close()

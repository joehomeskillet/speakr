"""D6. The review and archive command use the same live action grant on the released report."""

import sqlite3
from uuid import uuid4

from playwright.sync_api import expect

import v8_browser_support as support


RELEASE_READY = """
from datetime import datetime, timezone
from src.clinic import revisions
from src.clinic.context_grants import ClinicContextGrantUse
from src.clinic.models import ClinicContextRevision
with app.app_context():
    target = ClinicDictation.query.one()
    head = revisions.current_revision(target)
    revisions.add_human_revision(target, head, "Geprüfter Testtext.", target.author_user_id)
    context = ClinicContextRevision(dictation_id=target.id, source_system="test", tenant_scope="test",
                                    case_ref="SYN-0001", patient_ref="synthetic")
    db.session.add(context)
    db.session.flush()
    db.session.add(ClinicContextGrantUse(revision_id=context.id, issuer="kis", audience="speakr",
                   actor_user_id=target.author_user_id, subject_reference="synthetic", purpose="bind_existing",
                   verified_at=datetime.now(timezone.utc), nonce_digest=uuid.uuid4().hex.ljust(64, "0")))
    pools.allow(target.author_user_id, "own", actor_id=User.query.filter_by(is_admin=True).one().id,
                actions=("release",))
    db.session.commit()
"""


def test_grant_revoke_and_archive_real_release(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_PROFILE", "staging")  # synthetic local KIS adapter
    server = support.SERVER.replace("server = make_server", RELEASE_READY + "\nserver = make_server")
    with support.serve_app(tmp_path, server) as base, \
            browser.new_context(storage_state=support.login(browser, base, support.ROLES["admin"])) as admin, \
            browser.new_context(storage_state=support.login(browser, base, support.ROLES["author"])) as author:
        api = base + "/api/clinic/v1"
        did = author.request.get(api + "/recordings").json()["items"][0]["dictation_id"]
        path = api + f"/dictations/{did}"
        csrf = author.request.get(base + "/api/csrf-token").json()["csrf_token"]

        def post(endpoint, body, etag=None, key=None):
            headers = {"X-CSRFToken": csrf, "Idempotency-Key": key or str(uuid4())}
            if etag:
                headers["If-Match"] = etag
            return author.request.post(path + endpoint, data=body, headers=headers)

        lease = post("/leases", {}).json()
        detail = author.request.get(path + "/detail").json()
        assert "release" in detail["allowed_actions"] and "archive" not in detail["allowed_actions"]
        released = post("/releases", {**detail["binding"]["release"], "lease_id": lease["lease_id"],
                        "lease_generation": lease["generation"], "confirmed_content": True, "confirmed_context": True},
                        detail["etag"])
        assert released.status == 201, released.text()
        detail = author.request.get(path + "/detail").json()
        assert detail["status_axes"]["workflow"] == "fertiggestellt"
        etag = detail["etag"]
        body = {"action": "archive", "confirm": True}
        review = author.new_page()

        def open_review():
            review.goto(base + "/#/aufnahmen")
            review.get_by_test_id("open-link").first.click()
            review.get_by_test_id("clinic-dictation-link-review").click()
            expect(review.get_by_text("Revisionsnachweis", exact=True)).to_be_visible()

        archive = review.get_by_role("button", name="Archivieren", exact=True)
        open_review()
        expect(archive).to_have_count(0)
        assert post("/archive", body, etag).status == 404

        page = admin.new_page()
        page.goto(base + "/admin#/benutzer")
        users = page.get_by_test_id("clinic-users-view")
        users.get_by_role("button", name="Fachrechte", exact=True).click()
        grants = users.get_by_role("region", name="Fachrechte", exact=True)
        grants.get_by_label("Benutzer", exact=True).select_option(label="author-v860")
        grants.get_by_label("Geltungsbereich").select_option("own")

        def grant():
            grants.get_by_label("Archivieren", exact=True).check()
            with page.expect_response(lambda r: r.url.endswith("/grants") and r.request.method == "POST") as saved:
                grants.get_by_role("button", name="Speichern").click()
            assert saved.value.status == 200

        grant()
        open_review()
        expect(archive).to_be_enabled()
        assert "archive" in author.request.get(path + "/review").json()["allowed_actions"]
        entry = grants.get_by_role("listitem").filter(has_text="Archivieren")
        with page.expect_response(lambda r: r.request.method == "DELETE") as revoked:
            entry.get_by_role("button", name="Entziehen").click()
        assert revoked.value.status == 200
        # The already-open review still shows the former action; submission checks the live grant.
        archive.click()
        with review.expect_response(lambda r: r.url == path + "/archive" and r.request.method == "POST") as denied:
            review.get_by_test_id("confirm").click()
        assert denied.value.status == 404
        expect(review.get_by_text("Das Diktat wurde archiviert.")).to_have_count(0)
        open_review()
        expect(archive).to_have_count(0)
        grant()
        stale = post("/archive", body, f'"{did}:{detail["revision"] + 1}"')
        assert stale.status == 409
        assert post("/archive", {"action": "delete", "confirm": True}, etag).status == 404
        preview = post("/archive", {"action": "archive", "confirm": False}, etag)
        assert preview.status == 200 and preview.json()["applied"] is False
        with sqlite3.connect(tmp_path / "v860.db") as conn:
            assert conn.execute("SELECT count(*) FROM clinic_archive_entry").fetchone() == (0,)
        open_review()
        archive.click()
        with review.expect_response(lambda r: r.url == path + "/archive" and r.request.method == "POST") as result:
            review.get_by_test_id("confirm").click()
        assert result.value.status == 200, result.value.text()
        expect(review.get_by_role("status").filter(has_text="Das Diktat wurde archiviert.")).to_be_visible()
        review.get_by_role("link", name="Zum Archiv").click()
        expect(review.get_by_test_id("archive-open")).to_be_visible()
        archived = author.request.get(api + "/recordings?status=zu_archivieren").json()["items"]
        assert [row["dictation_id"] for row in archived] == [did]
        payload = result.value.json()
        assert payload["applied"] and payload["status"] == "zu_archivieren"
        assert author.request.get(path + "/detail").json()["etag"] == payload["etag"]
        assert post("/archive", body, etag, result.value.request.headers["idempotency-key"]).json() == payload
        with sqlite3.connect(tmp_path / "v860.db") as conn:
            assert conn.execute("""SELECT e.original_id = t.original_version_id AND e.release_id = r.id
                  AND e.audio_revision_id = r.audio_revision_id AND e.transcript_revision_id = r.transcript_revision_id
                  AND e.context_revision_id = r.context_revision_id
                FROM clinic_archive_entry e JOIN clinic_release r ON r.dictation_id = e.dictation_id
                JOIN clinic_transcript_revision t ON t.id = r.transcript_revision_id
                WHERE e.dictation_id = ?""", (did,)).fetchall() == [(1,)]
            assert conn.execute("SELECT count(*) FROM clinic_archive_result").fetchone() == (1,)

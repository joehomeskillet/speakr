"""Vocabulary routes (PAR-VOC-001/002/003/006), HTTP slice.

Positive, unauthorized, stale-version and failure cases against the real
``vocabulary`` service. BT-VOC-* stay in their own files.
"""

import pytest
from flask import Flask, g, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import pools, vocabulary
from src.clinic.routes_vocabulary import PREFIX, VocabularyApi, canonical_selection, ApiError
from src.database import db
from src.models import User

from test_pools import _suffix, _user, pool_db as pool_db


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    vocabulary.create_tables()
    admin, author, other = _user(admin=True), _user(), _user()
    pool = pools.create_author_pool("V-" + _suffix(), actor_id=admin.id)
    foreign = pools.create_author_pool("W-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(pool.id, author.id, actor_id=admin.id)
    pools.add_author_member(pool.id, other.id, actor_id=admin.id)
    writers = {author.id}
    api = VocabularyApi(lambda uid, pid: uid in writers)
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic",
                        SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(api.blueprint)

    def client(user):
        c = flask.test_client()
        g.pop("csrf_token", None)  # one app context is shared: force a token per client session
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    outer = db.session()
    outer.commit()
    with flask.app_context():
        yield dict(client=client, author=author, other=other, pool=pool, foreign=foreign, admin=admin)
        outer.rollback()
        db.session.rollback()
        for model in (vocabulary.ClinicVocabularyEntry,):
            db.session.query(model).filter(
                (model.user_id.in_([admin.id, author.id, other.id]))
                | (model.pool_id.in_([pool.id, foreign.id]))).delete(synchronize_session=False)
        db.session.commit()


def _add(c, h, **body):
    return c.post(PREFIX + "/additions", headers=h, json=body)


def test_add_search_export_and_import(world):
    c, h = world["client"](world["author"])
    r = _add(c, h, scope="user", written="  Metoprolol  succinat ", spoken="Metoprolol")
    assert r.status_code == 201 and r.json["entry"]["written"] == "Metoprolol succinat"
    found = c.get(PREFIX + "/search?scope=user&q=metopro").json["entries"]
    assert [e["written"] for e in found] == ["Metoprolol succinat"]
    assert c.get(PREFIX + "/export?scope=user").json["content"] == "Metoprolol succinat\tMetoprolol"
    imp = c.post(PREFIX + "/import", headers=h, json={"scope": "user", "content": "Alpha\n#c\nBeta\tbeta\n" + "x" * 300})
    assert (imp.status_code, imp.json["added"], len(imp.json["errors"])) == (200, 2, 1)
    dup = c.post(PREFIX + "/import", headers=h, json={"scope": "user", "content": "Gamma\nAlpha", "duplicate_rule": "error"})
    assert (dup.status_code, dup.json["code"]) == (409, "duplicate_entry")
    assert not [e for e in c.get(PREFIX + "/search?scope=user&q=Gamma").json["entries"]]  # rolled back


def _b64(text, encoding):
    import base64
    return base64.b64encode(text.encode(encoding)).decode()


def _names(c, q):
    return [e["written"] for e in c.get(PREFIX + "/search?scope=user&q=" + q).json["entries"]]


@pytest.mark.parametrize("encoding", ["utf-8", "cp1252", "latin-1"])
def test_import_encodings_keep_umlauts_and_dry_run_changes_nothing(world, encoding):
    c, h = world["client"](world["author"])
    text = "Ärztin\tÄrztin\n#c\nÜberweisung\nÄrztin\n" + "x" * 300
    post = lambda **extra: c.post(PREFIX + "/import", headers=h, json={
        "scope": "user", "content_base64": _b64(text, encoding), "encoding": encoding, **extra})
    dry = post(dry_run=True)
    assert (dry.status_code, dry.json["dry_run"], dry.json["added"], dry.json["updated"]) == (200, True, 2, 1)
    assert dry.json["errors"] == [{"line": 5, "reason": "form_too_long"}]
    assert _names(c, "rzt") == [] and _names(c, "berw") == []
    real = post(duplicate_rule="skip")
    assert (real.json["added"], real.json["skipped"], real.json["dry_run"]) == (2, 1, False)
    assert _names(c, "rztin") == ["Ärztin"] and _names(c, "berweisung") == ["Überweisung"]


def test_import_invalid_bytes_and_payloads_are_deterministic(world):
    import base64
    c, h = world["client"](world["author"])
    raw = base64.b64encode("Alpha\n".encode() + "Bär".encode("latin-1") + b"\nGamma").decode()
    r = c.post(PREFIX + "/import", headers=h, json={"scope": "user", "content_base64": raw, "dry_run": True})
    assert (r.json["added"], r.json["errors"]) == (2, [{"line": 2, "reason": "invalid_encoding"}])
    for bad in ({"content_base64": "!!"}, {"content_base64": raw, "encoding": "utf-16"},
                {"content": "A", "content_base64": raw}, {"content": "A", "encoding": "utf-8"},
                {"content": "A", "dry_run": "yes"}):
        assert c.post(PREFIX + "/import", headers=h, json={"scope": "user", **bad}).status_code == 400


def test_effective_export_user_overrides_pool(world):
    c, h = world["client"](world["author"])
    pid = world["pool"].id
    _add(c, h, scope="pool", pool_id=pid, written="Konsil", spoken="pool")
    _add(c, h, scope="user", written="Konsil", spoken="user")
    assert c.get(PREFIX + "/export?scope=effective").json["content"] == "Konsil\tuser"


def test_suppress_reactivate_and_stale_version(world):
    c, h = world["client"](world["author"])
    entry = _add(c, h, scope="user", written="Dosis").json["entry"]
    url = f"{PREFIX}/entries/{entry['id']}/suppress"
    off = c.post(url, headers=h, json={"expected_version": entry["version"]})
    assert off.status_code == 200 and off.json["entry"]["status"] == "suppressed"
    stale = c.post(url, headers=h, json={"expected_version": entry["version"]})
    assert (stale.status_code, stale.json["code"]) == (409, "stale_version")
    assert c.get(PREFIX + "/search?scope=user&q=Dosis").json["entries"] == []
    on = c.post(f"{PREFIX}/entries/{entry['id']}/reactivate", headers=h,
                json={"expected_version": off.json["entry"]["version"]})
    assert on.json["entry"]["status"] == "active"


def test_pool_membership_and_write_policy(world):
    pid = world["pool"].id
    c, h = world["client"](world["author"])
    assert _add(c, h, scope="pool", pool_id=pid, written="Pool-Wort").status_code == 201
    # Foreign pool: no membership.
    assert _add(c, h, scope="pool", pool_id=world["foreign"].id, written="X").status_code == 403
    # Member without write rights: may read, not write.
    c2, h2 = world["client"](world["other"])
    assert c2.get(f"{PREFIX}/search?scope=pool&pool_id={pid}").json["entries"][0]["written"] == "Pool-Wort"
    assert _add(c2, h2, scope="pool", pool_id=pid, written="Y").status_code == 403
    entry_id = c2.get(f"{PREFIX}/search?scope=pool&pool_id={pid}").json["entries"][0]
    r = c2.post(f"{PREFIX}/entries/{entry_id['id']}/suppress", headers=h2, json={"expected_version": entry_id["version"]})
    assert r.status_code == 404
    # Membership is rechecked per request: removal takes effect immediately.
    pools.remove_author_member(pid, world["author"].id, actor_id=world["admin"].id)
    c3, _ = world["client"](world["author"])
    assert c3.get(f"{PREFIX}/search?scope=pool&pool_id={pid}").status_code == 403


def test_foreign_user_entry_is_hidden(world):
    c, h = world["client"](world["author"])
    entry = _add(c, h, scope="user", written="Privat").json["entry"]
    c2, h2 = world["client"](world["other"])
    r = c2.post(f"{PREFIX}/entries/{entry['id']}/suppress", headers=h2, json={"expected_version": entry["version"]})
    assert r.status_code == 404
    assert c2.get(PREFIX + "/search?scope=user&q=Privat").json["entries"] == []


def test_selection_bounds_and_untrusted_payloads(world):
    c, h = world["client"](world["author"])
    for bad in ("", "   ", "a\x00b", "x" * 256, 5, None):
        assert _add(c, h, scope="user", written=bad).status_code == 400
    assert canonical_selection("a \n b") == "a b"
    with pytest.raises(ApiError):
        canonical_selection("a\x07")
    for key in ("actor_id", "user_id", "role", "is_admin"):
        assert _add(c, h, scope="user", written="Ok", **{key: world["other"].id}).status_code == 400
    assert _add(c, h, scope="group", written="Ok").status_code == 400
    assert _add(c, h, scope="user", pool_id=world["pool"].id, written="Ok").status_code == 400
    assert c.get(PREFIX + "/search?scope=user&mode=regex").status_code == 400


def test_search_field_covers_spoken_written_and_both(world):
    c, h = world["client"](world["author"])
    _add(c, h, scope="user", written="Hypertonie", spoken="Bluthochdruck")
    _add(c, h, scope="user", written="Blutbild", spoken="Labor")
    _add(c, h, scope="user", written="Anamnese")

    def names(field, q, mode):
        found = c.get(PREFIX + f"/search?scope=user&field={field}&mode={mode}&q={q}").json["entries"]
        return [e["written"] for e in found]

    assert names("written", "Hyper", "prefix") == ["Hypertonie"]
    assert names("written", "druck", "suffix") == []
    assert names("written", "tonie", "contains") == ["Hypertonie"]
    assert names("written", "Anamnese", "exact") == ["Anamnese"]

    assert names("spoken", "Blut", "prefix") == ["Hypertonie"]
    assert names("spoken", "druck", "suffix") == ["Hypertonie"]
    assert names("spoken", "hoch", "contains") == ["Hypertonie"]
    assert names("spoken", "Labor", "exact") == ["Blutbild"]

    assert names("all", "Blut", "prefix") == ["Blutbild", "Hypertonie"]
    assert names("all", "druck", "suffix") == ["Hypertonie"]
    assert names("all", "bor", "contains") == ["Blutbild"]
    assert names("all", "Anamnese", "exact") == ["Anamnese"]

    # The old default stays the written form, and an unknown field is refused.
    assert names("written", "Blut", "prefix") == ["Blutbild"]
    assert c.get(PREFIX + "/search?scope=user&q=Blut").json["entries"][0]["written"] == "Blutbild"
    assert c.get(PREFIX + "/search?scope=user&field=regex").status_code == 400


def _bulk(c, h, action, entries):
    return c.post(PREFIX + "/entries/bulk", headers=h, json={"action": action, "confirm": True, "entries": entries})


def test_bulk_suppress_and_delete_check_version_and_permission(world):
    c, h = world["client"](world["author"])
    first = _add(c, h, scope="user", written="Alpha", spoken="a").json["entry"]
    second = _add(c, h, scope="user", written="Beta", spoken="b").json["entry"]
    items = [{"id": e["id"], "expected_version": e["version"]} for e in (first, second)]

    # One stale version: neither entry changes.
    stale = [{"id": first["id"], "expected_version": "1999-01-01T00:00:00+00:00"}, items[1]]
    refused = _bulk(c, h, "suppress", stale)
    assert (refused.status_code, refused.json["code"]) == (409, "stale_version")
    assert [e["status"] for e in c.get(PREFIX + "/search?scope=user&status=all&q=").json["entries"]] == ["active", "active"]

    done = _bulk(c, h, "suppress", items)
    assert done.status_code == 200
    assert [e["status"] for e in done.json["entries"]] == ["suppressed", "suppressed"]
    # The versions just used are now stale, so the same body cannot delete.
    assert _bulk(c, h, "delete", items).status_code == 409
    fresh = [{"id": e["id"], "expected_version": e["version"]} for e in done.json["entries"]]
    deleted = _bulk(c, h, "delete", fresh)
    assert (deleted.status_code, deleted.json["deleted"]) == (200, 2)
    assert c.get(PREFIX + "/search?scope=user&status=all&q=").json["entries"] == []

    # A foreign entry, and a pool entry the caller may read but not write, both refuse
    # the whole list and look like a missing entry.
    own = _add(c, h, scope="user", written="Eigen").json["entry"]
    c2, h2 = world["client"](world["other"])
    foreign = _add(c2, h2, scope="user", written="Fremd").json["entry"]
    # Logging the other user in replaced the shared session, so the author needs a fresh client.
    c, h = world["client"](world["author"])
    mixed = _bulk(c, h, "delete", [
        {"id": own["id"], "expected_version": own["version"]},
        {"id": foreign["id"], "expected_version": foreign["version"]},
    ])
    assert mixed.status_code == 404
    assert c.get(PREFIX + "/search?scope=user&q=Eigen").json["entries"][0]["status"] == "active"

    pid = world["pool"].id
    pool_entry = _add(c, h, scope="pool", pool_id=pid, written="Pool-Wort").json["entry"]
    c2, h2 = world["client"](world["other"])
    reader = _bulk(c2, h2, "suppress", [{"id": pool_entry["id"], "expected_version": pool_entry["version"]}])
    assert reader.status_code == 404
    c, h = world["client"](world["author"])
    assert c.get(PREFIX + f"/search?scope=pool&pool_id={pid}&q=Pool").json["entries"][0]["status"] == "active"

    # confirm is mandatory, and a body may not name an actor.
    assert c.post(PREFIX + "/entries/bulk", headers=h, json={"action": "delete", "entries": [
        {"id": own["id"], "expected_version": own["version"]}]}).status_code == 400
    assert _bulk(c, h, "delete", [{"id": own["id"], "expected_version": own["version"], "user_id": 1}]).status_code == 400


def test_unauthorized_csrf_and_clinic_mode(world, monkeypatch):
    anon = world["client"](world["author"])[0].application.test_client()
    assert anon.get(PREFIX + "/search?scope=user").status_code == 401
    c, _ = world["client"](world["author"])
    no_csrf = c.post(PREFIX + "/additions", json={"scope": "user", "written": "A"})
    assert (no_csrf.status_code, no_csrf.mimetype) == (403, "application/problem+json")
    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(PREFIX + "/search?scope=user").status_code == 404

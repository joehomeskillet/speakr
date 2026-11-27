"""PAR-VOC-004 (P-192, Q-19): administration of the global baseline and pool caution lists.

Real services and routes on synthetic data. The site admin owns the global baseline, pool
writers own their pool's additions; the effective list feeds the W11 matcher of the release gate.
"""

import uuid

import pytest
from flask import Flask, g, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import caution_rules, caution_words, pools, vocabulary
from src.clinic.routes_vocabulary import PREFIX, VocabularyApi
from src.database import db
from src.models import User

from test_pools import _suffix, _user, pool_db as pool_db  # noqa: F401
from test_wp_caution_words import _scene, clinic_db as clinic_db  # noqa: F401

URL = PREFIX + "/caution-words"


def _purge():
    db.session.rollback()
    db.session.query(caution_rules.ClinicCautionRuleVersion).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def world(pool_db, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CLINIC_MODE", "1")
    vocabulary.create_tables()
    caution_rules.create_tables()
    _purge()
    admin, lead, member, outsider = _user(admin=True), _user(), _user(), _user()
    pool = pools.create_author_pool("C-" + _suffix(), actor_id=admin.id)
    foreign = pools.create_author_pool("D-" + _suffix(), actor_id=admin.id)
    for user in (lead, member):
        pools.add_author_member(pool.id, user.id, actor_id=admin.id)
    pools.add_author_member(foreign.id, outsider.id, actor_id=admin.id)
    writers = {lead.id}
    api = VocabularyApi(lambda uid, pid: uid in writers and pid == pool.id)
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic",
                        SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.before_request
    def _fresh_user():
        g.pop("_login_user", None)  # the shared app context outlives each request

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(api.blueprint)

    def client(user):
        c = flask.test_client()
        g.pop("csrf_token", None)
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    outer = db.session()
    outer.commit()
    with flask.app_context():
        yield dict(client=client, admin=admin, lead=lead, member=member, outsider=outsider,
                   pool=pool, foreign=foreign)
        outer.rollback()
        _purge()


def _publish_global(c, h, words, version):
    return c.post(URL + "/global", headers=h, json={"words": words, "expected_version": version})


def _publish_pool(c, h, pool_id, words, version):
    return c.post(URL + "/pool", headers=h, json={"pool_id": pool_id, "words": words, "expected_version": version})


def test_global_baseline_is_site_admin_only_and_attributable(world):
    lead, lh = world["client"](world["lead"])
    assert _publish_global(lead, lh, ["Verdacht"], 0).status_code == 403
    admin, ah = world["client"](world["admin"])
    first = _publish_global(admin, ah, ["verdacht", "Allergie", "Verdacht"], 0)
    assert first.status_code == 201
    assert (first.json["version"], first.json["words"]) == (1, ["Allergie", "verdacht"])
    assert first.json["published_by"] == world["admin"].id
    assert _publish_global(admin, ah, ["Embolie"], 1).status_code == 201
    stale = _publish_global(admin, ah, ["Nope"], 1)
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (409, "stale_version", 2)
    listed = lead.get(URL + "?scope=global").json
    assert (listed["version"], listed["words"], listed["can_write"]) == (2, ["Embolie"], False)
    assert [(h["version"], h["count"], h["published_by"]) for h in listed["history"]] == [
        (2, 1, world["admin"].id), (1, 2, world["admin"].id)]
    assert admin.get(URL + "?scope=global").json["can_write"] is True


def test_pool_scope_is_enforced_on_reads_and_writes(world):
    lead, lh = world["client"](world["lead"])
    ok = _publish_pool(lead, lh, world["pool"].id, ["Marcumar"], 0)
    assert (ok.status_code, ok.json["words"]) == (201, ["Marcumar"])
    member, mh = world["client"](world["member"])
    pool_url = URL + "?scope=pool&pool_id=" + world["pool"].id
    read = member.get(pool_url).json
    assert (read["words"], read["can_write"]) == (["Marcumar"], False)
    assert _publish_pool(member, mh, world["pool"].id, ["X"], 1).status_code == 403
    outsider, oh = world["client"](world["outsider"])
    assert outsider.get(pool_url).status_code == 403
    assert _publish_pool(outsider, oh, world["pool"].id, ["X"], 1).status_code == 403
    assert _publish_pool(lead, lh, world["foreign"].id, ["X"], 0).status_code == 403
    assert lead.get(URL + "?scope=pool&pool_id=" + world["foreign"].id).status_code == 403


def test_publish_two_patterns_preview_as_author_and_unrelated_pool(world):
    admin, ah = world["client"](world["admin"])
    lead, lh = world["client"](world["lead"])
    assert _publish_global(admin, ah, ["Verdacht"], 0).status_code == 201
    assert _publish_pool(lead, lh, world["pool"].id, ["Allergie"], 0).status_code == 201
    member, mh = world["client"](world["member"])
    text = "Verdacht auf Allergie."
    shown = member.post(URL + "/preview", headers=mh, json={"pool_id": world["pool"].id, "text": text})
    assert shown.status_code == 200
    assert shown.json == {"version": "g1.p1", "hits": [{"word": "Verdacht", "offset": 0}, {"word": "Allergie", "offset": 13}]}
    outsider, oh = world["client"](world["outsider"])
    assert outsider.post(URL + "/preview", headers=oh, json={"pool_id": world["pool"].id, "text": text}).status_code == 403
    other = outsider.post(URL + "/preview", headers=oh, json={"pool_id": world["foreign"].id, "text": text})
    assert other.json == {"version": "g1.p0", "hits": [{"word": "Verdacht", "offset": 0}]}


def test_invalid_payloads_csrf_and_clinic_mode(world, monkeypatch):
    admin, ah = world["client"](world["admin"])
    for words in ("Verdacht", ["a|b"], [""], [1], ["x" * 65], ["w%d" % i for i in range(201)]):
        assert _publish_global(admin, ah, words, 0).status_code == 400
    assert admin.post(URL + "/global", headers=ah, json={"words": [], "expected_version": "0"}).status_code == 400
    assert admin.post(URL + "/global", headers=ah, json={"words": [], "expected_version": 0, "user_id": 1}).status_code == 400
    assert admin.post(URL + "/global", json={"words": [], "expected_version": 0}).status_code == 403
    assert admin.get(URL + "?scope=pool").status_code == 400
    assert admin.post(URL + "/preview", headers=ah, json={"pool_id": world["pool"].id, "text": 5}).status_code == 400
    assert _publish_global(admin, ah, [], 0).status_code == 201  # an empty list is allowed
    monkeypatch.delenv("CLINIC_MODE")
    assert admin.get(URL + "?scope=global").status_code == 404


def test_effective_list_feeds_the_w11_matcher_and_stays_in_its_pool(clinic_db):  # noqa: F811
    caution_rules.create_tables()
    pool_a, pool_b = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        _purge()
        caution_rules.publish(caution_rules.GLOBAL_KEY, ["Verdacht"], 1, 0)
        caution_rules.publish(caution_rules.pool_key(pool_a), ["Embolie"], 1, 0)
        s = _scene(pool_a)
        rules = caution_rules.effective_rule_set(pool_a)
        assert (rules.version, rules.words, rules.authorized) == ("g1.p1", ("Embolie", "Verdacht"), True)
        hits = caution_words.inspect(s.dictation, rules, s.user.id)
        assert [(h.word, h.offset) for h in hits] == [("Verdacht", 0), ("Verdacht", 56), ("Embolie", 69)]
        assert caution_rules.effective_rule_set(pool_b).words == ("Verdacht",)
        assert caution_words.inspect(s.dictation, caution_rules.effective_rule_set(pool_b), s.user.id) == []
    finally:
        _purge()

"""PAR-ADM-004: explicit authors and deny exceptions on the versioned grants API.

An admin saves pool A except Beta. The response carries stable author user ids.
In the running app Beta and Gamma stay closed for list, search, direct fetch,
audio and export. Alpha in pool A stays open for those object decisions.
"""

import json

from src.app import app, db
from src.clinic import policy, pool_policy, pools
from src.clinic.models import ClinicDictation
from src.models import Recording

from clinic_support import login
from test_pools import _dictation, _suffix, _user
from test_wp_grants_api import _account, _change, _login, _url, _writes, grants_app, pool_db

ACTIONS = ["view", "play", "export", "audio_export"]
SURFACES = ("view", "play", "audio_export", "export")


def _item(body, effect):
    return next(item for item in body["items"] if item["effect"] == effect)


def _mark(dictation, marker):
    recording = db.session.get(Recording, dictation.recording_id)
    recording.title = marker
    recording.notes = marker
    return recording


def test_pool_exception_and_explicit_authors_keep_stable_ids(grants_app):
    admin = _account(_user(admin=True))
    alpha, beta, gamma, writer, clerk = _user(), _user(), _user(), _user(), _user()
    pool_a = pools.create_author_pool("A-" + _suffix(), actor_id=admin.id)
    pool_b = pools.create_author_pool("B-" + _suffix(), actor_id=admin.id)
    marked = {
        "alpha": _mark(_dictation(alpha, pool_a.id), "adm004-alpha-" + _suffix()),
        "beta": _mark(_dictation(beta, pool_a.id), "adm004-beta-" + _suffix()),
        "gamma": _mark(_dictation(gamma, pool_b.id), "adm004-gamma-" + _suffix()),
    }
    db.session.commit()
    client, headers = _login(grants_app, admin)
    url = _url(writer)

    before = _writes()
    stale = client.post(url, headers=headers, json=_change(
        "deny", scope="pool", pool_id=pool_a.id, actions=ACTIONS, rev=4, author_ids=[beta.id]))
    assert (stale.status_code, stale.json["code"]) == (409, "REVISION_CONFLICT")
    for body in (
        _change(scope="own", actions=ACTIONS, author_ids=[alpha.id]),
        _change(scope="all", pool_id=pool_a.id, actions=ACTIONS, author_ids=[alpha.id]),
        _change(scope="pool", pool_id=pool_a.id, actions=ACTIONS, author_ids=[]),
        _change(scope="pool", pool_id=pool_a.id, actions=ACTIONS, author_ids=[beta.username]),
        _change(scope="pool", pool_id=pool_a.id, actions=ACTIONS, author_ids=[beta.id, beta.id]),
        _change(scope="pool", pool_id=pool_a.id, actions=ACTIONS, author_ids=[0]),
        _change("deny", scope="pool", pool_id=pool_a.id, actions=ACTIONS, author_ids=[999_999_991]),
    ):
        refused = client.post(url, headers=headers, json=body)
        assert (refused.status_code, refused.json["code"]) == (400, "INVALID_PAYLOAD"), body
    assert _writes() == before

    explicit = client.post(_url(clerk), headers=headers, json=_change(
        scope="all", actions=["view"], author_ids=[alpha.id]))
    assert explicit.status_code == 200
    assert _item(explicit.json, "allow")["author_ids"] == [alpha.id]

    opened = client.post(url, headers=headers, json=_change(
        scope="pool", pool_id=pool_a.id, actions=ACTIONS, rev=explicit.json["revision"]))
    assert opened.status_code == 200
    assert "author_ids" not in _item(opened.json, "allow")

    saved = client.post(url, headers=headers, json=_change(
        "deny", scope="pool", pool_id=pool_a.id, actions=ACTIONS,
        rev=opened.json["revision"], author_ids=[beta.id]))
    assert saved.status_code == 200
    denial = _item(saved.json, "deny")
    assert denial["author_ids"] == [beta.id]
    assert denial["pool_id"] == pool_a.id
    assert denial["scope"] == "pool"

    beta.username = ("z" + beta.username)[:20]
    db.session.commit()
    listed = _item(client.get(url + "?entries=1").json, "deny")
    assert listed["author_ids"] == [beta.id]
    assert listed["author_names"] == [beta.username]

    _running_app_blocks(writer, marked)


def _listed(response, key):
    """JSON of one result array. Search echoes the query outside ``results``."""
    body = response.get_json(silent=True) or {}
    assert key in body, response.get_data(as_text=True)[:300]
    return json.dumps(body[key])


def _running_app_blocks(writer, marked):
    """List, search, direct fetch, audio and export on the app that mounts the guard."""
    db.session.commit()
    client = login(writer.id)
    listed = ""
    for path in ("/api/recordings", "/api/v1/recordings"):
        response = client.get(path)
        assert response.status_code == 200, path
        listed += _listed(response, "recordings")
    for key in ("alpha", "beta", "gamma"):
        assert marked[key].title not in listed
        found = client.get("/api/v1/search?q=" + marked[key].title)
        assert found.status_code == 200, (key, found.status_code, found.get_data(as_text=True)[:300])
        assert marked[key].title not in _listed(found, "results")

    def code(path):
        body = client.get(path).get_json(silent=True) or {}
        return body.get("code")

    for key in ("beta", "gamma"):
        recording_id = marked[key].id
        assert code(f"/api/v1/recordings/{recording_id}") == "not_found"
        assert code(f"/audio/{recording_id}") == "not_found"
        assert code(f"/api/v1/recordings/{recording_id}/audio") == "not_found"
    alpha_id = marked["alpha"].id
    assert code(f"/api/v1/recordings/{alpha_id}") != "not_found"
    assert code(f"/audio/{alpha_id}") != "not_found"
    assert code(f"/api/v1/recordings/{alpha_id}/audio") != "not_found"

    if "clinic_worklist.rows" in app.view_functions:
        rows = client.get("/api/clinic/v1/worklist/rows?search=" + marked["beta"].title)
        items = _listed(rows, "items")
        assert marked["beta"].title not in items
        assert marked["gamma"].title not in items

    db.session.expire_all()
    subject = policy.build_subject(writer.id)
    dictations = {
        key: ClinicDictation.query.filter_by(recording_id=marked[key].id).one() for key in marked
    }
    for action in SURFACES:
        assert pool_policy.decide(subject, action, dictations["alpha"]).allowed
        assert not pool_policy.decide(subject, action, dictations["beta"]).allowed
        assert not pool_policy.decide(subject, action, dictations["gamma"]).allowed

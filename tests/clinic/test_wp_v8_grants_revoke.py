"""Revocation is scoped to the selected user and checked against the shared version."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from test_wp_grants_api import _account, _change, _login, _url, _user, _writes, grants_app, pool_db

from src.clinic import pools


def test_revoke_exact_deny_with_shared_revision_and_audit(grants_app):
    admin, target, other = _account(_user(admin=True)), _user(), _user()
    client, headers = _login(grants_app, admin)
    denied, = pools.set_author_scope(target.id, actor_id=admin.id, deny_user_ids=[other.id], actions=["play"])
    allowed = pools.allow(other.id, "own", actor_id=admin.id, actions=["edit"])
    url = _url(target)
    snapshot = client.get(url + "?entries=1").json
    assert snapshot["items"][0]["id"] == denied.id
    assert snapshot["items"][0]["author_names"] == [other.username]
    assert snapshot["revision"] == pools.grant_version() == 2
    body = {"expected_revision": snapshot["revision"]}
    revoke = f"{url}/{denied.id}"
    before = _writes()
    plain, plain_headers = _login(grants_app, target)
    assert "author_names" not in plain.get(url + "?entries=1").json["items"][0]
    assert plain.delete(revoke, headers=plain_headers, json=body).status_code == 403
    assert client.delete(revoke, json=body).status_code == 403
    assert client.delete(revoke, headers=headers, json={}).status_code == 400
    assert client.delete(f"{url}/{allowed.id}", headers=headers, json=body).status_code == 404
    assert client.delete(revoke, headers=headers, json={"expected_revision": 1}).status_code == 409
    assert _writes() == before
    result = client.delete(revoke, headers=headers, json=body)
    assert result.status_code == 200
    assert result.json == {"user_id": target.id, "items": [], "revision": 3}
    assert client.delete(revoke, headers=headers, json=body).status_code == 409
    assert client.post(url, headers=headers, json=_change(rev=0)).status_code == 409
    assert client.get(_url(other) + "?entries=1").json["items"][0]["id"] == allowed.id
    revoked = [row for row in pools.history() if row["change"] == "revoked"]
    assert [(row["entity_id"], row["actor_user_id"]) for row in revoked] == [(denied.id, admin.id)]


def test_two_first_writes_for_different_users_share_one_revision(grants_app):
    admin = _account(_user(admin=True))
    requests = [(*_login(grants_app, admin), _url(_user())) for _ in range(2)]
    start = Barrier(2)

    def submit(args):
        client, headers, url = args
        start.wait(timeout=10)
        return client.post(url, headers=headers, json=_change(rev=0)).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(submit, requests)) == [200, 409]
    assert pools.grant_version() == 1

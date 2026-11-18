"""Finding an account to add to a group.

The group page's add-member goes through src/services/user_lookup.py: a group
admin names the person by the full username or the full email address (the
email compares ignoring case). Part of a name or address finds nobody, so no
answer tells whether such an account exists, and the member list of a group
carries no email address. A site admin may also add by user id.

SHARED-DB: every row made here is removed afterwards, and every assertion
is about names carrying the test's own random token.
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.api.groups as groups_module
from src.app import app, db
from src.models import Group, GroupMembership, User
from src.services import user_lookup

app.config['WTF_CSRF_ENABLED'] = False


class World:
    """me, a colleague in a shared group, a stranger, and a site admin."""

    def __init__(self):
        self.tok = uuid.uuid4().hex[:8]
        self.user_ids = []
        self.group_ids = []

    def user(self, name, email=None, is_admin=False):
        user = User(username=name, email=email or f"{name}@local.test", password="x", is_admin=is_admin)
        db.session.add(user)
        db.session.commit()
        self.user_ids.append(user.id)
        return user.id

    def group(self, name, members, admins=()):
        group = Group(name=name)
        db.session.add(group)
        db.session.commit()
        for user_id in members:
            db.session.add(GroupMembership(group_id=group.id, user_id=user_id,
                                           role='admin' if user_id in admins else 'member'))
        db.session.commit()
        self.group_ids.append(group.id)
        return group.id

    def cleanup(self):
        with app.app_context():
            GroupMembership.query.filter(GroupMembership.user_id.in_(self.user_ids)).delete(synchronize_session=False)
            Group.query.filter(Group.id.in_(self.group_ids)).delete(synchronize_session=False)
            User.query.filter(User.id.in_(self.user_ids)).delete(synchronize_session=False)
            db.session.commit()


@pytest.fixture
def world():
    w = World()
    t = w.tok
    with app.app_context():
        w.me = w.user(f"{t}me", f"{t}me@Corp.local.test")
        w.mate = w.user(f"{t}mate", f"{t}Mate@Corp.local.test")
        w.stranger = w.user(f"{t}stranger", f"{t}stranger@corp.local.test")
        w.admin = w.user(f"{t}root", f"{t}root@corp.local.test", is_admin=True)
        w.shared = w.group(f"{t} shared", [w.me, w.mate], admins=[w.me])
        w.other = w.group(f"{t} other", [w.stranger])
    try:
        yield w
    finally:
        w.cleanup()


def _client(user_id):
    client = app.test_client()
    with client.session_transaction() as s:
        s['_user_id'] = str(user_id)
        s['_fresh'] = True
    return client


def _add(actor_id, group_id, **body):
    return _client(actor_id).post(f"/api/admin/groups/{group_id}/members", json=body)


def _member_ids(group_id):
    with app.app_context():
        return {m.user_id for m in GroupMembership.query.filter_by(group_id=group_id).all()}


def test_a_group_admin_adds_by_full_username_or_email(world):
    t = world.tok
    with app.app_context():
        second = world.user(f"{t}second", f"{t}Second@corp.local.test")
    resp = _add(world.me, world.shared, identifier=f"{t}stranger")
    assert resp.status_code == 201, resp.data
    assert 'email' not in resp.get_json()
    assert _add(world.me, world.shared, identifier=f" {t}SECOND@CORP.local.test ").status_code == 201
    assert {world.stranger, second} <= _member_ids(world.shared)


def test_a_group_admin_cannot_add_by_part_of_a_name_or_by_id(world):
    t = world.tok
    for body in ({'identifier': f"{t}stran"}, {'identifier': f"{t}stranger@corp"},
                 {'identifier': '@corp.local.test'}, {'identifier': f"{t}STRANGER"}):
        resp = _add(world.me, world.shared, **body)
        assert resp.status_code == 404 and resp.get_json()['code'] == 'user_not_found', body
    resp = _add(world.me, world.shared, user_id=world.stranger)
    assert resp.status_code == 400 and resp.get_json()['code'] == 'member_identifier_required'
    assert world.stranger not in _member_ids(world.shared)


def test_a_group_member_who_is_not_its_admin_cannot_add(world):
    resp = _add(world.mate, world.shared, identifier=f"{world.tok}stranger")
    assert resp.status_code == 403


def test_a_site_admin_still_adds_by_user_id(world):
    assert _add(world.admin, world.shared, user_id=world.stranger).status_code == 201
    assert world.stranger in _member_ids(world.shared)


def test_the_group_route_uses_the_one_lookup(world):
    assert groups_module._find_user_exactly.__module__ == groups_module.__name__
    t = world.tok
    with app.app_context():
        assert groups_module._find_user_exactly(f"{t}mate").id == world.mate
        assert user_lookup.find_user_exactly(f"{t}MATE@corp.local.test").id == world.mate
        assert user_lookup.find_user_exactly(f"{t}mat") is None
        assert user_lookup.find_user_exactly('') is None
        assert user_lookup.find_user_exactly(None) is None


def test_the_member_list_of_a_group_has_no_email(world):
    resp = _client(world.me).get(f'/api/admin/groups/{world.shared}')
    assert resp.status_code == 200
    body = resp.get_json()
    assert {m['username'] for m in body['members']} == {f"{world.tok}me", f"{world.tok}mate"}
    assert 'local.test' not in resp.get_data(as_text=True)
    # A member who is not an admin of the group does not get the list
    assert _client(world.mate).get(f'/api/admin/groups/{world.shared}').status_code == 403

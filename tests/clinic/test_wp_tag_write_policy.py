"""W7 tag writes: team-admin rules plus central clinic action rights, never client claims."""

import pytest

from clinic_support import login
from src.app import app, db
from src.clinic import pools
from src.clinic.route_policy import AUTH, ROUTE_POLICY, RouteRule
from src.models import Group, GroupMembership, RecordingTag, Tag, User
from test_pools import _dictation, _suffix, _user, pool_db as pool_db


@pytest.fixture
def world(pool_db, monkeypatch):
    # The deployed guard still gates internal_sharing; exercise the owned handler.
    monkeypatch.setitem(ROUTE_POLICY, 'tags.create_group_tag', RouteRule(AUTH))
    member, team_admin, site_admin = _user(), _user(), _user(admin=True)
    team = Group(name=f'Team-{_suffix()}')
    other_team = Group(name=f'Other-{_suffix()}')
    db.session.add_all([team, other_team])
    db.session.flush()
    db.session.add_all([
        GroupMembership(group_id=team.id, user_id=member.id, role='member'),
        GroupMembership(group_id=team.id, user_id=team_admin.id, role='admin'),
    ])
    shared = Tag(name='Shared', user_id=team_admin.id, group_id=team.id)
    mine = Tag(name='Mine', user_id=team_admin.id)
    db.session.add_all([shared, mine])
    db.session.commit()
    yield member.id, team_admin.id, site_admin.id, team.id, other_team.id, shared.id, mine.id
    db.session.rollback()
    ids = [t.id for t in Tag.query.filter(Tag.user_id.in_([member.id, team_admin.id, site_admin.id]))]
    RecordingTag.query.filter(RecordingTag.tag_id.in_(ids)).delete(synchronize_session=False)
    Tag.query.filter(Tag.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()


def _call(user_id, method, path, body=None):
    with app.app_context():
        return getattr(login(user_id), method)(path, json=body)


@pytest.mark.parametrize('mode', ['clinic_on', 'clinic_off'])
def test_group_tag_mutation_needs_team_admin(request, mode, world):
    request.getfixturevalue(mode)
    member, team_admin, site_admin, team, other_team, shared, _ = world
    url = f'/api/tags/{shared}'
    # Client-supplied can_edit and foreign group ids authorize nothing.
    assert _call(member, 'put', url, {'name': 'X', 'can_edit': True, 'user_role': 'admin'}).status_code == 403
    assert _call(member, 'post', '/api/tags', {'name': 'N', 'group_id': team, 'can_edit': True}).status_code == 403
    assert _call(member, 'post', f'/api/groups/{team}/tags', {'name': 'N'}).status_code == 403
    assert _call(member, 'delete', url).status_code == 403
    assert _call(team_admin, 'post', '/api/tags', {'name': 'N', 'group_id': other_team}).status_code == 403
    assert _call(site_admin, 'put', url, {'name': 'X'}).status_code == 403  # admin flag is no team role
    assert _call(site_admin, 'delete', url).status_code == 403

    assert _call(team_admin, 'put', url, {'name': 'Renamed'}).status_code == 200
    assert _call(team_admin, 'post', f'/api/groups/{team}/tags', {'name': 'G2'}).status_code == 201
    created = _call(team_admin, 'post', '/api/tags', {'name': 'G3', 'group_id': team})
    assert created.status_code == 201
    assert _call(team_admin, 'delete', f"/api/tags/{created.json['id']}").status_code == 200
    # A duplicate name fails; the retry with a free name succeeds.
    assert _call(team_admin, 'post', '/api/tags', {'name': 'G2', 'group_id': team}).status_code == 400
    assert _call(team_admin, 'post', '/api/tags', {'name': 'G4', 'group_id': team}).status_code == 201


@pytest.mark.parametrize('mode', ['clinic_on', 'clinic_off'])
def test_stale_membership_is_rechecked_per_request(request, mode, world):
    request.getfixturevalue(mode)
    _, team_admin, _, team, _, shared, _ = world
    assert _call(team_admin, 'put', f'/api/tags/{shared}', {'name': 'First'}).status_code == 200
    GroupMembership.query.filter_by(group_id=team, user_id=team_admin).update({'role': 'member'})
    db.session.commit()
    assert _call(team_admin, 'put', f'/api/tags/{shared}', {'name': 'Second'}).status_code == 403
    assert _call(team_admin, 'delete', f'/api/tags/{shared}').status_code == 403
    GroupMembership.query.filter_by(group_id=team, user_id=team_admin).delete()
    db.session.commit()
    assert _call(team_admin, 'post', '/api/tags', {'name': 'N', 'group_id': team}).status_code == 403
    assert db.session.get(Tag, shared).name == 'First'


def test_clinic_rejects_non_integer_group_ids(clinic_on, world):
    _, team_admin, _, team, _, _, mine = world
    assert _call(team_admin, 'post', '/api/tags', {'name': 'N', 'group_id': 'abc'}).status_code == 403
    assert _call(team_admin, 'post', '/api/tags', {'name': 'N', 'group_id': True}).status_code == 403
    assert _call(team_admin, 'put', f'/api/tags/{mine}', {'group_id': 'abc'}).status_code == 403
    assert _call(team_admin, 'post', '/api/tags', {'name': 'N2', 'group_id': str(team)}).status_code == 201


def test_regroup_uses_central_edit_right(clinic_on, world):
    _, team_admin, site_admin, team, _, _, mine = world
    admin = db.session.get(User, team_admin)
    target = _dictation(admin, None)
    db.session.add(RecordingTag(recording_id=target.recording_id, tag_id=mine))
    db.session.commit()
    pools.deny(team_admin, 'own', actor_id=site_admin, actions=['edit'])
    response = _call(team_admin, 'put', f'/api/tags/{mine}', {'group_id': team})
    assert response.status_code == 403
    assert db.session.get(Tag, mine).group_id is None


def test_regroup_legacy_stays_owner_based(clinic_off, world):
    _, team_admin, _, team, _, _, mine = world
    admin = db.session.get(User, team_admin)
    target = _dictation(admin, None)
    db.session.add(RecordingTag(recording_id=target.recording_id, tag_id=mine))
    db.session.commit()
    assert _call(team_admin, 'put', f'/api/tags/{mine}', {'group_id': team}).status_code == 200
    assert db.session.get(Tag, mine).group_id == team

"""W7 tag reads require membership; counts also require recording view rights."""

import pytest

from clinic_support import login
from src.app import app, db
from src.clinic import pools
from src.clinic.models import ClinicDictation
from src.clinic.route_policy import AUTH, ROUTE_POLICY, RouteRule
from src.models import Group, GroupMembership, RecordingTag, Tag
from test_pools import _dictation, _suffix, _user, pool_db as pool_db


@pytest.fixture
def tagged(pool_db):
    member, team_admin, outsider = _user(), _user(), _user(admin=True)
    team = Group(name=f'Team-{_suffix()}')
    db.session.add(team)
    db.session.flush()
    db.session.add_all([
        GroupMembership(group_id=team.id, user_id=member.id, role='member'),
        GroupMembership(group_id=team.id, user_id=team_admin.id, role='admin'),
    ])
    author_pool = pools.create_author_pool(f'Authors-{_suffix()}', actor_id=outsider.id)
    staff_pool = pools.create_staff_pool(f'Staff-{_suffix()}', actor_id=outsider.id)
    pools.add_staff_member(staff_pool.id, outsider.id, actor_id=outsider.id)
    own, foreign = _dictation(member, None), _dictation(team_admin, author_pool.id)
    personal = Tag(name='Personal', user_id=member.id)
    shared = Tag(name='Team', user_id=team_admin.id, group_id=team.id)
    db.session.add_all([personal, shared])
    db.session.flush()
    tag_ids = [personal.id, shared.id]
    db.session.add_all([
        RecordingTag(recording_id=target.recording_id, tag_id=tag_id)
        for target in (own, foreign) for tag_id in tag_ids
    ])
    db.session.commit()
    yield member.id, team_admin.id, outsider.id, team.id, author_pool.id, foreign.id
    db.session.rollback()
    RecordingTag.query.filter(RecordingTag.tag_id.in_(tag_ids)).delete(synchronize_session=False)
    Tag.query.filter(Tag.id.in_(tag_ids)).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def group_handler(monkeypatch):
    # The deployed guard still gates internal_sharing. Exercise this owned
    # handler behind an authenticated route without changing the shipping policy.
    monkeypatch.setitem(ROUTE_POLICY, 'tags.get_group_tags', RouteRule(AUTH))


def _read(user_id, path='/api/tags'):
    with app.app_context():
        return login(user_id).get(path)


def _tag_rows(response):
    assert response.status_code == 200
    return [(tag['name'], tag['recording_count'], tag['can_edit'], tag['user_role'])
            for tag in response.get_json()]


def test_clinic_counts_and_group_roles(clinic_on, tagged, group_handler):
    member, team_admin, outsider, team, pool, target_id = tagged
    group_url = f'/api/groups/{team}/tags'
    assert _tag_rows(_read(member)) == [('Personal', 1, True, None), ('Team', 1, False, 'member')]
    assert _tag_rows(_read(team_admin)) == [('Team', 1, True, 'admin')]
    response = _read(member, group_url)
    assert response.status_code == 200
    assert [(tag['name'], tag['recording_count']) for tag in response.json['tags']] == [('Team', 1)]

    target = db.session.get(ClinicDictation, target_id)
    detail = f'/api/recordings/{target.recording_id}'
    assert _read(member, detail).status_code == 404
    assert _read(outsider, detail).status_code == 404

    pools.allow(member, 'pool', actor_id=outsider, pool_id=pool, actions=['view'])
    assert _tag_rows(_read(member))[-1] == ('Team', 2, False, 'member')
    pools.deny(member, 'pool', actor_id=outsider, pool_id=pool, actions=['view'])
    assert _read(member, group_url).json['tags'][0]['recording_count'] == 1
    pools.deny(member, 'own', actor_id=outsider, actions=['view'])
    assert _tag_rows(_read(member)) == [('Personal', 0, True, None), ('Team', 0, False, 'member')]

    # Even an explicit dictation grant is not a team tag grant.
    pools.allow(outsider, 'all', actor_id=outsider, actions=['view'])
    assert _tag_rows(_read(outsider)) == []
    assert _read(outsider, group_url).status_code == 403


def test_retry_reads_current_scope_after_provider_failure(clinic_on, tagged, monkeypatch):
    member, team_admin, outsider, _, pool, target_id = tagged
    pools.allow(member, 'pool', actor_id=outsider, pool_id=pool, actions=['view'])
    client = login(member)
    assert _tag_rows(client.get('/api/tags'))[-1][1] == 2

    def unavailable(*args):
        raise RuntimeError('pool lookup failed')

    with monkeypatch.context() as patch:
        patch.setattr(pools, 'grants_for', unavailable)
        patch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', True)
        with pytest.raises(RuntimeError, match='pool lookup failed'):
            client.get('/api/tags')

    target = db.session.get(ClinicDictation, target_id)
    ClinicDictation.query.filter_by(id=target_id).update(
        {'pool_id': None, 'version_counter': target.version_counter + 1}, synchronize_session=False,
    )
    assert target.pool_id == pool  # The retry must disregard the stale ORM object.
    assert _tag_rows(client.get('/api/tags'))[-1][1] == 1


def test_legacy_counts_stay_unfiltered(clinic_off, tagged):
    member, _, outsider, team, _, _ = tagged
    group_url = f'/api/groups/{team}/tags'
    assert _tag_rows(_read(member)) == [('Personal', 2, True, None), ('Team', 2, False, 'member')]
    assert _read(member, group_url).json['tags'][0]['recording_count'] == 2
    assert _read(outsider, group_url).status_code == 403

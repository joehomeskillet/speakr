"""Startup and consumer coverage for live pool policy decisions."""

from unittest.mock import Mock

import pytest
from flask import Flask

from src.app import app, db
from src.clinic import policy, pool_policy, pools
from src.clinic.models import ClinicDictation
from src.clinic.worklist_filters import query_worklist

from test_pools import _dictation, _suffix, _user, pool_db as pool_db
from test_s01_review_findings import _guard, features_open as features_open


def test_startup_policy_is_shared_by_worklist_and_route_guard(clinic_on, features_open):
    author, typist = _user(), _user()
    author_pool = pools.create_author_pool(f'A-{_suffix()}', actor_id=author.id)
    staff_pool = pools.create_staff_pool(f'S-{_suffix()}', actor_id=author.id)
    pools.link_staff_pool(author_pool.id, staff_pool.id, actor_id=author.id)
    target = _dictation(author, author_pool.id)
    user_id, rec_id = typist.id, target.recording_id
    subject = policy.build_subject(user_id)
    rows = [{'id': rec_id, 'owner_id': author.id, 'title': 'Synthetic dictation'}]
    detail = f'/api/recordings/{rec_id}'
    audio = f'/audio/{rec_id}'
    export = f'/recording/{rec_id}/download/transcript'

    assert query_worklist(rows, subject).total == 0
    assert _guard(user_id, 'GET', detail).status_code == 404
    pools.add_staff_member(staff_pool.id, user_id, actor_id=author.id)
    assert query_worklist(rows, subject).total == 1
    assert query_worklist(rows, subject, query='dictation').total == 1
    assert _guard(user_id, 'GET', detail) is None
    assert _guard(user_id, 'GET', audio).status_code == 404

    pools.allow(user_id, 'pool', actor_id=author.id, pool_id=author_pool.id, actions=['play', 'export'])
    pools.deny(user_id, 'pool', actor_id=author.id, pool_id=author_pool.id, actions=['play'])
    assert _guard(user_id, 'GET', audio).status_code == 404
    assert _guard(user_id, 'GET', export) is None
    pools.remove_staff_member(staff_pool.id, user_id, actor_id=author.id)
    assert query_worklist(rows, subject, query='dictation').total == 0
    assert _guard(user_id, 'GET', detail).status_code == 404
    assert _guard(user_id, 'GET', export) is None

    # Change the stored version without updating the row already loaded by this session.
    ClinicDictation.query.filter_by(id=target.id).update(
        {'pool_id': None, 'version_counter': target.version_counter + 1}, synchronize_session=False,
    )
    assert target.pool_id == author_pool.id
    assert _guard(user_id, 'GET', export).status_code == 404


def test_installation_is_app_local_and_idempotent(clinic_on, monkeypatch):
    author, viewer = _user(), _user()
    target = _dictation(author, None)
    pools.allow(viewer.id, 'all', actor_id=author.id, actions=['export'])
    subject = policy.build_subject(viewer.id)
    facts = policy.build_facts(target.recording_id, author.id)
    other_app = Flask('pool-provider-test')
    other_app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI']
    db.init_app(other_app)

    with other_app.app_context():
        assert not policy.decide(subject, 'export', facts).allowed
        adapter = Mock(wraps=pool_policy.decide)
        monkeypatch.setattr(pool_policy, 'decide', adapter)
        policy.install_pool_policy(other_app)
        policy.install_pool_policy(other_app)
        assert policy.decide(subject, 'export', facts).allowed
        adapter.assert_called_once()

    assert policy.decide(subject, 'export', facts).allowed
    adapter.assert_called_once()
    monkeypatch.delenv('CLINIC_MODE')
    assert not policy.decide(subject, 'export', facts).allowed


def test_provider_error_never_falls_back_and_retry_uses_live_policy(clinic_on, monkeypatch):
    author = _user()
    target = _dictation(author, None)
    subject = policy.build_subject(author.id)
    facts = policy.build_facts(target.recording_id, author.id)

    def unavailable(*args):
        raise RuntimeError('pool lookup failed')

    with monkeypatch.context() as patch:
        patch.setattr(pools, 'grants_for', unavailable)
        with pytest.raises(RuntimeError, match='pool lookup failed'):
            policy.decide(subject, 'view', facts)

    pools.deny(author.id, 'own', actor_id=author.id, actions=['view'])
    assert policy.decide(subject, 'view', facts) == policy.Decision(False, 'deny_exception', visible=True)
    assert policy.decide(subject, 'play', facts).allowed

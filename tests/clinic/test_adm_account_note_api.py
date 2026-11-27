"""Synthetic HTTP/DB evidence for versioned account notes (PAR-ADM-001)."""

import json

import pytest

from src.app import app, db
from src.clinic import identity
from src.clinic.models import ClinicDictation
from src.clinic.presets import ClinicPreset, set_preset
from src.models import AuditEvent, Recording, User
from test_wp_identity_admin_api import admin_client, command  # noqa: F401


@pytest.fixture
def target(admin_client):
    client, _actor = admin_client
    created = command(client, 'POST', '/admin/users', username='note_target',
                      email='note-target@example.invalid', name='Synthetic account')
    assert created.status_code == 201
    uid = created.json['id']
    with app.app_context():
        recording = Recording(user_id=uid, title='Synthetic dictation', original_filename='note.wav')
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=uid)
        db.session.add(dictation)
        db.session.commit()
        rid, did = recording.id, dictation.id
    return uid, f'/admin/users/{uid}', rid, did


def assert_stored(target, note, version, *, name='Synthetic account'):
    uid, _path, rid, did = target
    with app.app_context():
        assert identity.account_notes([uid]).get(uid, '') == note
        policy = db.session.get(identity.ClinicAccountPolicy, uid)
        assert policy.version == version
        assert policy.security_version == 0
        assert db.session.get(User, uid).name == name
        assert db.session.get(Recording, rid).user_id == uid
        assert db.session.get(ClinicDictation, did).author_user_id == uid


def test_save_reload_noop_clear_and_audit(admin_client, target):
    client, actor = admin_client
    uid, path, *_ = target
    with app.app_context():
        set_preset('account_note', 'Must not inherit', scope='system')
        db.session.commit()
    assert client.get('/admin/users?search=note_target').json['users'][0]['account_note'] == ''
    note = '  Synthetische Anmerkung: äöü\nZweite Zeile <text>  '
    saved = command(client, 'PUT', path, expected_version=1, account_note=note, name='Changed account')
    assert saved.status_code == 200
    assert (saved.json['account_note'], saved.json['version']) == (note, 2)
    assert_stored(target, note, 2, name='Changed account')
    listing = client.get('/admin/users?search=note_target').json['users'][0]
    assert (listing['account_note'], listing['version']) == (note, 2)
    with app.app_context():
        preset = ClinicPreset.query.filter_by(scope='user', scope_id=str(uid), key='account_note').one()
        assert json.loads(preset.value_json) == note
        event = AuditEvent.query.filter_by(event='admin.user.changed', target_user_id=uid).one()
        assert event.actor_user_id == actor
        assert event.details == {'fields': ['account_note', 'name']}
        assert note not in str(event.details)
    unchanged = command(client, 'PUT', path, expected_version=2, account_note=note)
    assert unchanged.status_code == 200 and unchanged.json['version'] == 2
    cleared = command(client, 'PUT', path, expected_version=2, account_note='')
    assert cleared.status_code == 200 and cleared.json['version'] == 3
    assert client.get('/admin/users?search=note_target').json['users'][0]['account_note'] == ''
    assert_stored(target, '', 3, name='Changed account')


def test_stale_version_preserves_note_profile_audit_and_dictations(admin_client, target):
    client, _actor = admin_client
    uid, path, *_ = target
    assert command(client, 'PUT', path, expected_version=1, account_note='Current').status_code == 200
    stale = command(client, 'PUT', path, expected_version=1, account_note='Stale', name='Must not change')
    assert stale.status_code == 409 and stale.json['code'] == 'version_conflict'
    assert_stored(target, 'Current', 2)
    with app.app_context():
        assert AuditEvent.query.filter_by(event='admin.user.changed', target_user_id=uid).count() == 1


@pytest.mark.parametrize('note', [None, True, 42, [], {}, 'a' * 4001, 'bad\x00note'])
def test_invalid_note_rolls_back_entire_edit(admin_client, target, note):
    client, _actor = admin_client
    uid, path, *_ = target
    response = command(client, 'PUT', path, expected_version=1, account_note=note,
                       name='Must not change', status='disabled')
    assert response.status_code == 400 and response.json['code'] == 'invalid_profile'
    assert_stored(target, '', 1)
    with app.app_context():
        assert db.session.get(identity.ClinicAccountPolicy, uid).status == 'pending_activation'
        assert AuditEvent.query.filter_by(event='admin.user.changed', target_user_id=uid).count() == 0


@pytest.mark.parametrize('missing_right', ['is_identity_admin', 'is_admin'])
def test_missing_management_right_cannot_write_or_read_note(admin_client, target, missing_right):
    client, actor = admin_client
    _uid, path, *_ = target
    assert command(client, 'PUT', path, expected_version=1, account_note='Admin note').status_code == 200
    with app.app_context():
        model = identity.ClinicAccountPolicy if missing_right == 'is_identity_admin' else User
        setattr(db.session.get(model, actor), missing_right, False)
        db.session.commit()
    response = command(client, 'PUT', path, expected_version=2, account_note='Forbidden')
    assert response.status_code == 403
    listing = client.get('/admin/users?search=note_target')
    if listing.status_code == 200:
        assert all('account_note' not in row for row in listing.json['users'])
    else:
        assert listing.status_code == 403
    assert_stored(target, 'Admin note', 2)


def test_csrf_recent_auth_and_expected_version_required(admin_client, target):
    client, _actor = admin_client
    _uid, path, *_ = target
    assert client.put(path, json={'expected_version': 1, 'account_note': 'Forbidden'}).status_code == 400
    for extra in ({}, {'expected_version': True}, {'expected_version': 0}):
        response = command(client, 'PUT', path, account_note='Forbidden', **extra)
        assert response.status_code == 400 and response.json['code'] == 'expected_version_required'
    with client.session_transaction() as session:
        session['clinic_reauth_at'] -= 301
    response = command(client, 'PUT', path, expected_version=1, account_note='Forbidden')
    assert response.status_code == 403 and response.json['code'] == 'reauth_required'
    assert_stored(target, '', 1)


def test_note_not_silently_ignored_by_other_commands(admin_client, target):
    client, _actor = admin_client
    _uid, path, *_ = target
    assert command(client, 'POST', '/admin/users', username='unexpected',
                   email='unexpected@example.invalid', account_note='Note').status_code == 400
    assert command(client, 'PUT', path, expected_version=1, issue_recovery=True,
                   account_note='Note').status_code == 400
    assert command(client, 'POST', path + '/toggle-admin', expected_version=1,
                   account_note='Note').status_code == 400
    assert_stored(target, '', 1)


def test_maximum_length_and_omitted_note_preserved(admin_client, target):
    client, _actor = admin_client
    _uid, path, *_ = target
    assert command(client, 'PUT', path, expected_version=1, account_note='ü' * 4000).status_code == 200
    response = command(client, 'PUT', path, expected_version=2, name='Changed account')
    assert response.status_code == 200
    assert_stored(target, 'ü' * 4000, 3, name='Changed account')

"""HTTP tests for the guarded admin dictation list and content routes (w308).

The real application is used with its clinic guard enabled. Synthetic rows only.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func

from clinic_support import login
from src.app import app, db
from src.clinic.identity import ClinicAccountPolicy, create_tables as _create_identity_tables
from src.clinic.route_policy import ADMIN, ROUTE_POLICY
from src.clinic.workflow import TO_WRITE, ClinicWorkflowDictation, resolve_deadline
from src.models import AuditEvent, Recording, User
from src.services import audit

LIST = '/api/admin/clinic/dictations'
SECRET = 'SECRET-TRANSCRIPT-TEXT'
_BASELINE = [0]  # audit rows of earlier tests may name reused ids
ENDPOINTS = (
    'admin.admin_clinic_dictations',
    'clinic_admin_dictations.audio',
    'clinic_admin_dictations.transcript',
)


def _mount_clinic_admin_dictations():
    """The app is imported with clinic mode off, so the blueprint that register_clinic_identity
    mounts in clinic mode is mounted here (collection time, before any request) with its policy rows."""
    from src.clinic import routes_admin_dictations
    from src.clinic.route_policy import RouteRule
    if routes_admin_dictations.blueprint.name not in app.blueprints:
        app.register_blueprint(routes_admin_dictations.blueprint)
    for endpoint in ENDPOINTS[1:]:
        ROUTE_POLICY.setdefault(endpoint, RouteRule(ADMIN))


_mount_clinic_admin_dictations()


@pytest.fixture(autouse=True)
def _env(clinic_on):
    with app.app_context():
        _create_identity_tables()
    audit.flush()
    audit.reset_memory()
    with app.app_context():
        _BASELINE[0] = db.session.query(func.max(AuditEvent.id)).scalar() or 0
    yield
    audit.flush()
    audit.reset_memory()


def _user(name, *, is_admin=False, status='active', team_admin=False):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f'w308_{name}_{tag}', email=f'{name}_{tag}@example.invalid',
                password='x', is_admin=is_admin)
    db.session.add(user)
    db.session.commit()
    db.session.add(ClinicAccountPolicy(user_id=user.id, status=status, is_identity_admin=team_admin))
    db.session.commit()
    return user.id


def _recording(owner_id, title='Diktat', audio_path=None, transcription=SECRET):
    rec = Recording(user_id=owner_id, title=title, status='COMPLETED', original_filename='secret-name.wav',
                    transcription=transcription, audio_path=audio_path, mime_type='audio/wav')
    db.session.add(rec)
    db.session.commit()
    return rec.id


def _audit_count(user_id, recording_id):
    audit.flush()
    return AuditEvent.query.filter(
        AuditEvent.id > _BASELINE[0], AuditEvent.actor_user_id == user_id, AuditEvent.recording_id == recording_id
    ).count()


def test_routes_are_registered_as_admin_in_policy():
    for endpoint in ENDPOINTS:
        assert ROUTE_POLICY[endpoint].kind == ADMIN
    assert {rule.endpoint for rule in app.url_map.iter_rules()} >= set(ENDPOINTS)


def test_admin_lists_cross_user_metadata_without_content():
    with app.app_context():
        admin, author = _user('admin', is_admin=True), _user('author')
        rec_id = _recording(author, 'Befund')
    response = login(admin).get(f'{LIST}?author_user_id={author}')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    body = response.get_json()
    assert [item['recording_id'] for item in body['items']] == [rec_id]
    assert body['total'] == 1 and body['page'] == 1
    text = response.get_data(as_text=True)
    assert SECRET not in text and 'secret-name.wav' not in text
    assert 'transcription' not in text and 'audio_path' not in text


def test_every_declared_filter_and_empty_result():
    with app.app_context():
        admin, author, typist = _user('admin', is_admin=True), _user('author'), _user('typist')
        rec_id = _recording(author, 'Gallenblase links')
        db.session.add(ClinicWorkflowDictation(
            id=str(uuid.uuid4()), author_user_id=author, assignee_user_id=typist, pool_id='pool-w308',
            status=TO_WRITE, priority=128, deadline_date=date(2030, 1, 2), deadline_at=resolve_deadline(date(2030, 1, 2), None)))
        db.session.commit()
    client = login(admin)
    today = date.today().isoformat()
    cases = {
        'name=Gallenblase': {rec_id}, 'author_user_id=%d' % author: {rec_id},
        'created_on=' + today: {rec_id}, 'updated_on=' + today: {rec_id},
    }
    for query, expected in cases.items():
        got = client.get(f'{LIST}?{query}&author_user_id={author}').get_json()
        assert {i['recording_id'] for i in got['items'] if i['recording_id']} == expected, query
    workflow = client.get(
        f'{LIST}?typist_user_id={typist}&pool_id=pool-w308&status=zu_schreiben&priority=128&deadline_on=2030-01-02'
    ).get_json()
    assert workflow['total'] == 1 and workflow['items'][0]['kind'] == 'workflow'
    empty = client.get(f'{LIST}?name=Gibtesnicht').get_json()
    assert empty['items'] == [] and empty['total'] == 0


def test_paging_limits_and_invalid_input():
    with app.app_context():
        admin, author = _user('admin', is_admin=True), _user('author')
        for index in range(3):
            _recording(author, f'Seite {index}')
    client = login(admin)
    page = client.get(f'{LIST}?author_user_id={author}&page=2&page_size=2').get_json()
    assert page['total'] == 3 and len(page['items']) == 1 and page['page_size'] == 2
    for query, code in (
        ('page_size=101', 'invalid_pagination'), ('page=0', 'invalid_pagination'),
        ('page=abc', 'invalid_pagination'), ('author_user_id=x', 'invalid_filter'),
        ('priority=5', 'invalid_filter'), ('status=bogus', 'invalid_filter'),
        ('created_on=2030-13-45', 'invalid_filter'), ('colour=red', 'invalid_filter'),
        ('transcript=x', 'forbidden_field'), ('audio_path=x', 'forbidden_field'),
    ):
        response = client.get(f'{LIST}?{query}')
        assert response.status_code == 400, query
        assert response.get_json()['code'] == code, query
        assert response.headers['Cache-Control'] == 'no-store'


def test_non_admin_callers_get_403_on_every_endpoint():
    with app.app_context():
        author = _user('author')
        rec_id = _recording(author)
        callers = {
            'author': _user('plain'), 'team_admin': _user('team', team_admin=True),
            'statistics': _user('stats'),
        }
    urls = (LIST, f'{LIST}/{rec_id}/audio', f'{LIST}/{rec_id}/transcript')
    for name, uid in callers.items():
        client = login(uid)
        for url in urls:
            assert client.get(url).status_code == 403, (name, url)
    with app.app_context():
        assert _audit_count(callers['author'], rec_id) == 0


def test_anonymous_is_not_served():
    assert app.test_client().get(LIST).status_code in (302, 401)


def test_revoked_admin_is_refused_after_revocation():
    with app.app_context():
        admin, author = _user('admin', is_admin=True), _user('author')
        rec_id = _recording(author)
    client = login(admin)
    assert client.get(LIST).status_code == 200
    assert client.get(f'{LIST}/{rec_id}/transcript').status_code == 200
    with app.app_context():
        db.session.get(ClinicAccountPolicy, admin).status = 'disabled'
        db.session.commit()
    assert client.get(LIST).status_code == 403
    assert client.get(f'{LIST}/{rec_id}/transcript').status_code == 403


def test_direct_transcript_open_is_audited_each_time():
    with app.app_context():
        admin, author = _user('admin', is_admin=True), _user('author')
        rec_id = _recording(author)
    client = login(admin)
    for expected in (1, 2):
        response = client.get(f'{LIST}/{rec_id}/transcript')
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.get_json() == {'recording_id': rec_id, 'transcription': SECRET}
        with app.app_context():
            assert _audit_count(admin, rec_id) == expected


def test_direct_audio_open_serves_file_and_audits(tmp_path):
    audio = tmp_path / 'synthetic.wav'
    audio.write_bytes(b'RIFF0000WAVEfmt synthetic')
    with app.app_context():
        admin, author = _user('admin', is_admin=True), _user('author')
        rec_id = _recording(author, audio_path=str(audio))
    response = login(admin).get(f'{LIST}/{rec_id}/audio')
    assert response.status_code == 200
    assert response.data.startswith(b'RIFF0000')
    assert response.headers['Cache-Control'] == 'no-store'
    with app.app_context():
        assert _audit_count(admin, rec_id) == 1


def test_missing_objects_and_payloads_are_404_without_audit(tmp_path):
    with app.app_context():
        admin, author = _user('admin', is_admin=True), _user('author')
        no_audio = _recording(author)
        no_text = _recording(author, transcription=None)
    client = login(admin)
    assert client.get(f'{LIST}/999999/audio').status_code == 404
    assert client.get(f'{LIST}/999999/transcript').status_code == 404
    assert client.get(f'{LIST}/{no_audio}/audio').status_code == 404
    assert client.get(f'{LIST}/{no_text}/transcript').status_code == 404
    with app.app_context():
        assert _audit_count(admin, no_audio) == 0 and _audit_count(admin, no_text) == 0


def test_no_content_when_audit_write_fails(monkeypatch):
    with app.app_context():
        admin, author = _user('admin', is_admin=True), _user('author')
        rec_id = _recording(author)
    client = login(admin)
    monkeypatch.setattr(audit, 'record', lambda *args, **kwargs: False)
    response = client.get(f'{LIST}/{rec_id}/transcript')
    assert response.status_code == 503
    assert response.get_json()['code'] == 'audit_unavailable'
    assert SECRET not in response.get_data(as_text=True)
    monkeypatch.setattr(audit, 'flush', lambda *args, **kwargs: False)
    assert client.get(f'{LIST}/{rec_id}/transcript').status_code == 503
    def boom(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(audit, 'record', boom)
    response = client.get(f'{LIST}/{rec_id}/transcript')
    assert response.status_code == 503 and SECRET not in response.get_data(as_text=True)


def test_routes_are_absent_when_clinic_mode_is_off(monkeypatch):
    with app.app_context():
        admin, author = _user('admin', is_admin=True), _user('author')
        rec_id = _recording(author)
    monkeypatch.delenv('CLINIC_MODE', raising=False)
    client = login(admin)
    for url in (LIST, f'{LIST}/{rec_id}/audio', f'{LIST}/{rec_id}/transcript'):
        assert client.get(url).status_code == 404, url
    with app.app_context():
        assert _audit_count(admin, rec_id) == 0

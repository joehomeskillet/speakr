"""Exercise the clinic edit fences on stock Flask routes with synthetic rows."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from clinic_support import login
from src.app import app, db
from src.clinic import lease, live_session
from src.clinic.models import ClinicDictation
from src.models import Recording


def _link(recording_id, user_id):
    row = ClinicDictation(recording_id=recording_id, author_user_id=user_id)
    db.session.add(row)
    db.session.flush()
    return row.id


@pytest.mark.parametrize(('alias', 'edit'), [
    ('update_transcription', {'transcription': 'Geprüfter Text.'}),
    ('update_transcript', {'transcript_data': [{'text': 'Geprüfter Text.', 'start': 0, 'end': 1}]}),
])
def test_alias_needs_live_holder_even_with_review_lease(clinic_on, world, alias, edit):
    owner, other, rec_id = world['user_a'], world['user_b'], world['rec_a']
    now = datetime.now(timezone.utc)
    with app.app_context():
        dictation_id = _link(rec_id, owner)
        draft = live_session.start(dictation_id, other, now)
        session_id = draft.id
        generation = lease.acquire(dictation_id, owner, now).generation
        before = db.session.get(Recording, rec_id).transcription
        db.session.commit()

    url = f'/recording/{rec_id}/{alias}'
    body = {**edit, 'lease_generation': generation}
    client = login(owner)
    response = client.post(url, json=body)
    assert (response.status_code, response.json['code']) == (409, 'speech_lock_held')
    assert client.get(f'/api/recordings/{rec_id}').status_code == 200
    assert login(other).post(url, json=body).status_code == 404

    with app.app_context():
        assert db.session.get(Recording, rec_id).transcription == before
        live_session.end(session_id, other, datetime.now(timezone.utc))
        db.session.commit()
    assert client.post(url, json=body).status_code == 200
    with app.app_context():
        saved = db.session.get(Recording, rec_id).transcription
        if alias == 'update_transcript':
            assert json.loads(saved)[0]['text'] == 'Geprüfter Text.'
        else:
            assert saved == 'Geprüfter Text.'


def test_expired_generation_and_stale_live_retry_leave_metadata_unchanged(clinic_on, world):
    owner, rec_id = world['user_a'], world['rec_a']
    now = datetime.now(timezone.utc)
    then = now - timedelta(minutes=10)
    with app.app_context():
        dictation_id = _link(rec_id, owner)
        old_session = live_session.start(dictation_id, owner, then).id
        old_generation = lease.acquire(dictation_id, owner, then, duration=1).generation
        before = db.session.get(Recording, rec_id).title
        db.session.commit()

    client = login(owner)
    edit = {'id': rec_id, 'title': 'Neuer Titel'}
    response = client.post('/save', json=edit)
    assert (response.status_code, response.json['code']) == (400, 'invalid_lease_generation')

    def refused(code, **proof):
        response = client.post('/save', json={**edit, **proof})
        assert (response.status_code, response.json['code']) == (409, code)
        with app.app_context():
            assert db.session.get(Recording, rec_id).title == before

    refused('expired', lease_generation=old_generation)
    with app.app_context():
        generation = lease.acquire(dictation_id, owner, now).generation
        db.session.commit()
    refused('stale_live_session', lease_generation=generation, live_session_id=old_session)

    with app.app_context():
        current_session = live_session.start(dictation_id, owner, now).id
        db.session.commit()
    refused('stale_generation', lease_generation=old_generation, live_session_id=current_session)
    refused('stale_live_session', lease_generation=generation, live_session_id=old_session)
    response = client.post('/save', json={**edit, 'lease_generation': generation,
                                         'live_session_id': current_session})
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Recording, rec_id).title == 'Neuer Titel'


def test_batch_refusal_leaves_even_the_uncontested_first_target_unchanged(clinic_on, world):
    owner, other = world['user_a'], world['user_b']
    now = datetime.now(timezone.utc)
    with app.app_context():
        first = Recording(user_id=owner, title='Synthetisches Diktat', status='COMPLETED', is_inbox=False)
        db.session.add(first)
        db.session.flush()
        recording_ids = [first.id, world['rec_a']]
        first_id = _link(first.id, owner)
        dictation_id = _link(world['rec_a'], owner)
        generation = lease.acquire(dictation_id, owner, now).generation
        assert lease.acquire(first_id, owner, now).generation == generation
        held = live_session.start(dictation_id, other, now).id
        db.session.commit()

    client = login(owner)
    response = client.post(f'/api/recording/{world["rec_a"]}/process_chunks',
                           data={'lease_generation': str(generation)})
    assert (response.status_code, response.json['code']) == (409, 'speech_lock_held')
    edit = {'recording_ids': recording_ids, 'field': 'inbox', 'value': True, 'lease_generation': generation}
    response = client.post('/api/recordings/bulk-toggle', json=edit)
    assert (response.status_code, response.json['code']) == (409, 'speech_lock_held')
    with app.app_context():
        assert db.session.get(Recording, recording_ids[0]).is_inbox is False
        live_session.end(held, other, datetime.now(timezone.utc))
        db.session.commit()
    response = client.post('/api/recordings/bulk-toggle', json=edit)
    assert response.status_code == 200
    assert response.json['affected_ids'] == recording_ids

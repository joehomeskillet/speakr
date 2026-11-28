"""Speaker suggestions for synthetic imports through the real Flask and database paths."""

import io
import os
import struct
import subprocess
import sys
import wave

from src.app import app, db
from src.clinic import import_centre as imports
from src.clinic import import_source_config, pools, speaker_client, voice_consent, workflow
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.models import GroupMembership, ProcessingJob, VoiceEmbeddingSpace

from test_import_centre import _env as _env, _finalize, _upload, _user, env as env
from test_wp_v8_import_routes import _client, _get


def test_suggestion_is_scoped_consent_checked_and_read_only(env, monkeypatch, request):
    if 'clinic_imports_web' not in app.blueprints:
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', request.node.nodeid, '-q', '-p', 'no:cacheprovider', '--timeout=120'],
            env={**os.environ, 'CLINIC_MODE': '1', 'PYTEST_DATABASE_URI': ''},
            capture_output=True, text=True, timeout=110, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return

    admin, author, lead = (env[key] for key in ('admin', 'author', 'other'))
    peer, outsider = _user('peer'), _user('outside')
    admin.is_admin = True
    db.session.add_all(ClinicAccountPolicy(user_id=u.id, status='active')
                       for u in (admin, author, lead, peer, outsider))
    db.session.commit()
    source_pool = pools.create_author_pool('Source authors', actor_id=admin.id)
    other_pool = pools.create_author_pool('Other authors', actor_id=admin.id)
    staff_pool = pools.create_staff_pool(f'Synthetic suggestion typists {admin.username}', actor_id=admin.id)
    pools.link_staff_pool(source_pool.id, staff_pool.id, actor_id=admin.id)
    db.session.add(GroupMembership(group_id=staff_pool.group_id, user_id=lead.id, role='admin'))
    space = VoiceEmbeddingSpace(dimension=3, backend_fingerprint='speaker-service:synthetic')
    db.session.add(space)
    db.session.commit()
    seen_at = space.last_seen_at
    for user, pool in ((author, source_pool), (peer, source_pool), (outsider, other_pool)):
        pools.add_author_member(pool.id, user.id, actor_id=admin.id)
        voice_consent.grant_consent(actor_user_id=user.id, user_id=user.id, statement_text='Synthetic consent')
        voice_consent.enrol(user.id, voice_consent.EmbeddingRef(struct.pack('<3f', 1, 0, 0), 3, space.id))

    monkeypatch.setenv('CLINIC_IMPORT_ROOT_ALLOWLIST', '/synthetic-imports')
    import_source_config.create_source_config(
        actor_user_id=admin.id, source_kind='device', source_ref='usb-1', approved_root='/synthetic-imports',
        target_author_user_id=admin.id, target_pool_id=source_pool.id,
    )
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x00' * 64_000)
    receipts = [_finalize(env, *_upload(env, audio.getvalue(), root=root, name='dictation.wav')[:2])
                for root in ('usb-1', 'usb-2')]
    assert all(receipt['import']['status'] == 'UNASSIGNED' for receipt in receipts)
    scoped, foreign = [receipt['import']['delivery_id'] for receipt in receipts]

    service_calls = []
    service_model = {'status': 'ok', 'model': 'synthetic'}
    segments = [{'start': 0, 'end': 4, 'speaker': 'SPEAKER_00'}]
    vector = [1, 0, 0]
    revoke_on_embed = None

    def speaker_response(method, path, *, body, timeout):
        service_calls.append(path)
        if path != '/healthz':
            assert method == 'POST' and body.startswith(b'RIFF')
        if path == '/embed' and revoke_on_embed is not None:
            with app.app_context():
                voice_consent.revoke_consent(actor_user_id=revoke_on_embed, user_id=revoke_on_embed)
        return {'/diarize': {'segments': segments}, '/healthz': service_model,
                '/embed': {'embedding': vector}}[path]

    monkeypatch.setattr(speaker_client, '_request', speaker_response)
    lead_client = _client(lead)

    def suggestion(client=lead_client, delivery=scoped):
        return _get(client, f'unassigned/{delivery}/suggestion')

    assert suggestion(app.test_client()).status_code == 401
    assert suggestion(_client(author)).status_code == 403
    assert suggestion(delivery=foreign).status_code == 404
    assert not service_calls

    response = suggestion()
    assert response.status_code == 200
    assert response.cache_control.no_store and response.cache_control.private
    assert response.json == {'candidates': [author.id, peer.id], 'band': 'choice', 'reasons': ['ambiguous_match']}
    vector[:] = [0, 1, 0]
    assert suggestion().json == {'candidates': [], 'band': 'none', 'reasons': ['below_threshold']}
    vector[:] = [1, 0, 0]

    voice_consent.revoke_consent(actor_user_id=peer.id, user_id=peer.id)
    assert suggestion().json == {'candidates': [author.id], 'band': 'single', 'reasons': ['single_match']}
    revoke_on_embed = author.id
    assert suggestion().json == {'candidates': [], 'band': 'none', 'reasons': ['no_candidates']}
    revoke_on_embed = None

    vector[:] = [1, 0]
    failure = suggestion()
    assert failure.status_code == 503 and failure.json['code'] == 'DEPENDENCY_UNAVAILABLE'

    service_model['model'] = 'unenrolled-model'
    assert suggestion().json == {'candidates': [], 'band': 'none', 'reasons': ['no_candidates']}
    segments[:] = [{'start': 0, 'end': 2, 'speaker': label} for label in ('SPEAKER_00', 'SPEAKER_01')]
    service_calls.clear()
    assert suggestion().json == {'candidates': [], 'band': 'none', 'reasons': ['speech_too_short']}
    assert '/embed' not in service_calls

    for delivery_id in (scoped, foreign):
        delivery = db.session.get(imports.ClinicImportDelivery, delivery_id, populate_existing=True)
        assert (delivery.status, delivery.intent_id, delivery.assigned_at) == ('UNASSIGNED', None, None)
        original = db.session.get(imports.ClinicImportOriginal, delivery_id)
        assert env['service'].file_path(original.storage_key).read_bytes() == audio.getvalue()
    assert imports.ClinicImportIntent.query.count() == ClinicDictation.query.count() == 0
    assert ProcessingJob.query.count() == workflow.ClinicWorkflowDictation.query.count() == 0
    assert VoiceEmbeddingSpace.query.count() == voice_consent.ClinicVoiceEmbedding.query.count() == 1
    db.session.refresh(space)
    assert space.last_seen_at == seen_at

    delivery = db.session.get(imports.ClinicImportDelivery, scoped)
    delivery.status = 'MATCHED'
    db.session.commit()
    service_calls.clear()
    assert suggestion().status_code == 404
    assert not service_calls

    with monkeypatch.context() as patch:
        patch.delenv('CLINIC_MODE')
        assert suggestion().status_code == 404

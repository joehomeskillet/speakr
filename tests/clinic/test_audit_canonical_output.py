"""Synthetic HTTP and database evidence for the mounted canonical output paths."""

import hashlib
import wave
from dataclasses import replace
from uuid import uuid4

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import generate_csrf

from src.app import app as real_app, register_clinic_audio
from src.clinic import config, parity_receipts, revisions, richtext_formats
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.models import ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.output import FORMAT_FORMATTED, FORMAT_PLAIN, list_outputs
from src.clinic.output_attempts import FAILED, SUCCEEDED, UNKNOWN
from src.clinic.parity_dispatch import ParityDispatch
from src.clinic.pools import ClinicVisibilityGrant
from src.clinic.richtext import CanonicalDocument, ClinicRichTextSnapshot, Node, Segment
from src.clinic.sdict_bundle import read_bundle
from src.database import db
from src.models import User


@pytest.fixture
def output_case(world, clinic_on, tmp_path, monkeypatch):
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path))
    path = tmp_path / 'synthetic.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x10\x01\xff\xfe' * 800)
    wav = path.read_bytes()
    actor = world['user_a']
    with real_app.app_context():
        dictation = ClinicDictation(recording_id=world['rec_a'], author_user_id=actor)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(
            dictation_id=dictation.id, content_hash=hashlib.sha256(wav).hexdigest(),
            size_bytes=len(wav), storage_key=path.name, receipt='stored', origin_ref='test')
        db.session.add_all([original, ClinicContextRevision(
            dictation_id=dictation.id, source_system='synthetic', tenant_scope='site',
            case_ref='case', patient_ref='patient')])
        db.session.flush()
        init_audio_revision(dictation, original, 100)
        head = revisions.add_human_revision(dictation, None, 'Befund & Verlauf', actor,
                                            original_version=original)
        older = CanonicalDocument((Node('n1', 'paragraph', (Segment(id='s1', text=head.body),)),))
        latest = CanonicalDocument((replace(
            older.nodes[0], text_align='center', line_spacing=1.5,
            runs=(replace(older.nodes[0].runs[0], bold=True, font_family='serif', font_size_pt=14),)),))
        db.session.add_all([ClinicRichTextSnapshot(revision_id=head.id, version=version,
                                                  payload=document.to_payload())
                            for version, document in enumerate((older, latest), 1)])
        db.session.add(ClinicVisibilityGrant(user_id=actor, created_by=world['user_admin'],
                                             effect='allow', scope='own', actions='export'))
        db.session.commit()
        yield dictation.id, head.id, latest, wav


@pytest.fixture
def client_for(output_case):
    def make_client(user_id):
        worker = Flask(__name__)
        worker.config.update(TESTING=True, SECRET_KEY='synthetic',
                             SQLALCHEMY_DATABASE_URI=real_app.config['SQLALCHEMY_DATABASE_URI'])
        db.init_app(worker)
        LoginManager(worker).user_loader(lambda uid: db.session.get(User, int(uid)))
        run_once = parity_receipts.run_once
        worker.extensions.update(clinic_run_once=run_once, clinic_parity_dispatch=ParityDispatch(run_once))
        register_clinic_audio(worker)

        @worker.get('/login')
        def login():
            login_user(db.session.get(User, user_id))
            return jsonify(csrf=generate_csrf())

        client = worker.test_client()
        return client, {'X-CSRF-Token': client.get('/login').json['csrf']}

    return make_client


def test_bundle_uses_latest_snapshot_and_preserves_binary_audio(output_case, client_for, world):
    dictation_id, _, snapshot, wav = output_case
    client, _ = client_for(world['user_a'])
    url = f'/api/clinic/v1/bundles/{dictation_id}/export'
    result = client.get(url, query_string={'version': 1})
    assert result.status_code == 200
    assert result.headers['ETag'] == '"1"'
    assert read_bundle(result.data).files['audio/a.wav'] == wav
    assert richtext_formats.reload_bundle(result.data, expected_version=1, allowed=True) == snapshot
    assert client.get(url, query_string={'format': 'txt'}).data == b'Befund & Verlauf'
    assert b'\\b ' in client.get(url, query_string={'format': 'rtf'}).data


def test_clipboard_copies_both_formats_and_records_them_across_workers(output_case, client_for, world):
    dictation_id, revision_id, _, _ = output_case
    url = f'/api/clinic/v1/dictations/{dictation_id}/clipboard/{revision_id}'
    prepare, _ = client_for(world['user_a'])
    report, csrf = client_for(world['user_a'])
    for output_format in ('formatted', 'plain'):
        key = {'Idempotency-Key': str(uuid4())}
        result = prepare.get(url, query_string={'format': output_format}, headers=key)
        assert result.status_code == 200 and result.json['status'] == UNKNOWN
        assert result.json['text/plain'] == 'Befund & Verlauf'
        if output_format == 'formatted':
            assert '<strong>Befund &amp; Verlauf</strong>' in result.json['text/html']
            assert 'text-align:center;line-height:1.5' in result.json['text/html']
            assert 'font-family:serif;font-size:14pt' in result.json['text/html']
        else:
            assert 'text/html' not in result.json
        done = report.post(url + '/outcome', headers=key | csrf, json={'outcome': 'copied'})
        assert done.status_code == 200 and done.json['status'] == SUCCEEDED
        assert report.post(url + '/outcome', headers=key | csrf, json={'outcome': 'copied'}).status_code == 409
    receipts = list_outputs(db.session.get(ClinicDictation, dictation_id))
    assert [(row.transcript_revision_id, row.text_format) for row in receipts] == [
        (revision_id, FORMAT_FORMATTED), (revision_id, FORMAT_PLAIN)]


def test_clipboard_key_binds_format_and_snapshot(output_case, client_for, world):
    dictation_id, revision_id, snapshot, _ = output_case
    url = f'/api/clinic/v1/dictations/{dictation_id}/clipboard/{revision_id}'
    client, csrf = client_for(world['user_a'])
    key = {'Idempotency-Key': str(uuid4())}
    assert client.get(url + '?format=unknown', headers=key).status_code == 400
    assert client.get(url + '?format=formatted', headers=key).status_code == 200
    denied = client.post(url + '/outcome', headers=key | csrf, json={'outcome': 'denied'})
    assert denied.status_code == 200 and denied.json['status'] == FAILED
    assert client.get(url + '?format=plain', headers=key).status_code == 409

    updated = CanonicalDocument((replace(snapshot.nodes[0], text_align='right'),))
    db.session.add(ClinicRichTextSnapshot(revision_id=revision_id, version=3, payload=updated.to_payload()))
    db.session.commit()
    assert client.get(url + '?format=formatted', headers=key).status_code == 409
    fresh = client.get(url + '?format=formatted', headers={'Idempotency-Key': str(uuid4())})
    assert fresh.status_code == 200 and 'text-align:right' in fresh.json['text/html']


def test_clipboard_outcome_can_only_complete_the_prepared_revision(output_case, client_for, world):
    dictation_id, revision_id, _, _ = output_case
    base = f'/api/clinic/v1/dictations/{dictation_id}/clipboard'
    client, csrf = client_for(world['user_a'])
    key = {'Idempotency-Key': str(uuid4())}
    assert client.get(f'{base}/{revision_id}?format=plain', headers=key).status_code == 200

    dictation = db.session.get(ClinicDictation, dictation_id)
    head = db.session.get(ClinicTranscriptRevision, revision_id)
    newer = revisions.add_human_revision(
        dictation, head, 'Neuer Text', world['user_a'],
        original_version=db.session.get(ClinicOriginal, head.original_version_id))
    db.session.commit()
    assert client.get(f'{base}/{revision_id}', headers={'Idempotency-Key': str(uuid4())}).status_code == 409
    wrong = client.post(f'{base}/{newer.id}/outcome', headers=key | csrf, json={'outcome': 'copied'})
    assert wrong.status_code == 409 and list_outputs(dictation) == []
    correct = client.post(f'{base}/{revision_id}/outcome', headers=key | csrf, json={'outcome': 'copied'})
    assert correct.status_code == 200 and correct.json['status'] == SUCCEEDED
    assert [row.transcript_revision_id for row in list_outputs(dictation)] == [revision_id]


def test_output_permissions_and_version_gates(output_case, client_for, world, monkeypatch):
    dictation_id, revision_id, _, _ = output_case
    bundle = f'/api/clinic/v1/bundles/{dictation_id}/export'
    base = f'/api/clinic/v1/dictations/{dictation_id}'
    clipboard = f'{base}/clipboard/{revision_id}?format=plain'
    key = {'Idempotency-Key': str(uuid4())}
    owner, csrf = client_for(world['user_a'])
    other, _ = client_for(world['user_b'])
    assert other.get(bundle).status_code == 404
    assert other.get(clipboard, headers=key).status_code == 404
    assert owner.get(bundle + '?version=0').status_code == 409
    released = owner.post(base + '/exports', headers=csrf | key,
                          json={'revision_id': revision_id, 'format': 'txt'})
    assert released.status_code == 409 and released.json['code'] == 'NOT_RELEASED'
    monkeypatch.setattr(config, 'client_profile', lambda: 'byod_internal')
    assert owner.get(bundle).status_code == 403
    assert owner.get(clipboard, headers=key).status_code == 403

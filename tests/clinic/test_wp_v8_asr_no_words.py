"""A6: synthetic ASR filler is a receipt, not successful recognition.

SQLite projections and real HTTP submit; the PCM pipeline uses the local stub.
These tests do not claim engine accuracy or deployed QA49 evidence.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import event

from src.clinic import policy, recognition_state, recordings_list, revisions, workflow, worklist_entries
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.database import db
from test_wp_v8_139_processing_state import linked, queries, world  # noqa: F401
from test_wp_v8_asr_link_and_receipt import _run, upload, author, originals  # noqa: F401


FILLER = ['', ' \t\r\n\u00a0\u2003\u202f', '.', '...', '…', ', ', '!',
          '。 、，！：；', '،؛۔', '„“ «» \'"']
CONTENT = ['A', '5', 'ä', 'a\u0308', '漢字', '١', 'Dr.', '5 mg/dl',
           '+', '?', '§', '−', '-', '/', '%', '±', '°', 'µg', '... A ...']


def _stored(d, original, body):
    # Direct insertion represents existing immutable rows, bypassing ingest.
    row = ClinicTranscriptRevision(dictation_id=d.id, kind='ASR', body=body,
        normalised_text_hash=revisions.text_hash(body), original_version_id=original.id,
        model_ref='synthetic')
    db.session.add(row)
    db.session.flush()
    return row


@pytest.mark.parametrize('body,recognized', [(s, False) for s in FILLER] + [(s, True) for s in CONTENT])
def test_existing_rows_shared_projection_and_settle(world, body, recognized):
    d, source, job, original = linked(world, 'completed')
    source.status = db.session.get(type(source), d.recording_id).status = 'COMPLETED'
    row = _stored(d, original, body)
    entries = worklist_entries.load_entries(world.alice.id)
    expected = 'SUCCEEDED' if recognized else 'SKIPPED'
    assert next(e.row for e in entries if e.row['dictation_id'] == d.id)['asr_state'] == expected
    assert worklist_entries.dictation_snapshot(d)['asr_state'] == expected
    result = recordings_list.list_recordings(entries, policy.build_subject(world.alice.id))
    assert next(r for r in result['items'] if r['dictation_id'] == d.id)['processing_status'] == (
        'COMPLETED' if recognized else 'SKIPPED')
    assert recognition_state.asr_outcome(d) == ('done' if recognized else 'none')
    workflow.transition(db.session, d.id, 'send_online')
    assert recognition_state.settle(db.session, d.id).status == (
        workflow.TO_CORRECT if recognized else workflow.TO_WRITE)
    db.session.refresh(row)
    assert row.body == body and row.normalised_text_hash == revisions.text_hash(body)


@pytest.mark.parametrize('state', ['queued', 'processing', 'failed'])
def test_filler_preserves_job_state_and_prior_meaningful_result(world, state):
    d, source, job, original = linked(world, state)
    first = _stored(d, original, '...')
    assert recognition_state.asr_outcome(d, detailed=True) == state
    workflow.transition(db.session, d.id, 'send_online')
    assert recognition_state.settle(db.session, d.id).status == (
        workflow.TO_WRITE if state == 'failed' else workflow.PREP_ONLINE)
    human = revisions.add_human_revision(d, first, 'Menschlicher Text.', world.alice.id)
    assert recognition_state.asr_outcome(d, detailed=True) == state
    revisions.add_asr_revision(d, original, '5', 'synthetic')
    revisions.add_asr_revision(d, original, '!', 'synthetic')
    assert recognition_state.asr_outcome(d) == 'done'
    assert revisions.current_revision(d).id == human.id
    assert [r.body for r in revisions.history(d)] == ['...', 'Menschlicher Text.', '5', '!']


@pytest.mark.parametrize('job_type', ['transcribe', 'reprocess_transcription', 'convert', 'stitch', 'merge'])
@pytest.mark.parametrize('state', ['completed', 'failed'])
def test_terminal_job_settles_only_after_recognition_or_failure(world, job_type, state):
    d, source, job, original = linked(world, 'processing')
    job.job_type = job_type
    row = workflow.transition(db.session, d.id, 'send_online')
    db.session.commit()
    job.status = state
    db.session.commit()
    expected = workflow.TO_WRITE if state == 'failed' or job_type in (
        'transcribe', 'reprocess_transcription') else workflow.PREP_ONLINE
    assert row.status == expected


def test_batch_does_not_select_or_bind_transcripts(world):
    rows = [linked(world, 'completed') for _ in range(20)]
    for d, source, job, original in rows:
        _stored(d, original, '...' * 10000)
    bound = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        bound.extend(parameters)
    event.listen(db.engine, 'before_cursor_execute', capture)
    try:
        with queries() as statements:
            outcomes = recognition_state.asr_outcomes(d for d, *_ in rows)
    finally:
        event.remove(db.engine, 'before_cursor_execute', capture)
    assert len(statements) == 4
    assert set(outcomes.values()) == {'skipped'}
    for statement in statements:
        selected = statement.lower().split('from', 1)[0]
        assert '.body as ' not in selected and 'recording.transcription' not in selected
    assert 'regexp' in statements[-1].lower()
    assert all(len(value) < 200 for value in bound if isinstance(value, str))


@pytest.mark.parametrize('body', ['.', '...', '…', '!', 'A', '5', 'ä', '+', '?', '§'])
def test_incoming_pcm_result_settles_and_keeps_receipt(upload, monkeypatch, body):
    d, job = upload
    monkeypatch.setattr('src.services.transcription.connectors.clinic_stub.SYNTHETIC_SENTENCES', [body])
    row = workflow.transition(db.session, d.id, 'send_online')
    _run(job)
    assert job.status == 'completed', job.error_message
    assert row.status == (workflow.TO_WRITE if body in FILLER else workflow.TO_CORRECT)
    asr, = revisions.history(d)
    assert asr.body == body and asr.kind == revisions.KIND_ASR
    _run(job)
    assert [r.id for r in revisions.history(d)] == [asr.id]


def test_actual_submit(tmp_path):
    from test_wp_v8_send_route import ROOT

    env = {**os.environ, 'CLINIC_MODE': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1', 'PYTHONPATH': str(ROOT),
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "send.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads')}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise_submit():
    from uuid import uuid4
    from src.app import app
    from src.init_db import initialize_database
    from src.clinic import pools
    from src.clinic.models import ClinicOriginal
    from test_wp_v8_send_route import _client, _draft, _user

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        author_id, admin_id = _user('a6-author'), _user('a6-admin', admin=True)
        pool = pools.create_author_pool('A6 Autoren', actor_id=admin_id).id
        pools.add_author_member(pool, author_id, actor_id=admin_id)
        staff = pools.create_staff_pool('A6 Schreibdienst', actor_id=admin_id)
        pools.link_staff_pool(pool, staff.id, actor_id=admin_id)
        targets = []
        for body in ['...', 'A', '5', 'ä', '+', '?', '§', '。']:
            target = _draft(author_id, pool)
            original = ClinicOriginal(dictation_id=target, content_hash='b' * 64, size_bytes=1,
                                      storage_key='synthetic', receipt='stored', origin_ref='synthetic')
            db.session.add(original)
            db.session.flush()
            revisions.add_asr_revision(db.session.get(ClinicDictation, target), original, body, 'synthetic')
            targets.append((target, workflow.TO_WRITE if body in ('...', '。') else workflow.TO_CORRECT))
        db.session.commit()
    client, csrf = _client(app, author_id)
    for target, status in targets:
        response = client.post(f'/api/clinic/v1/dictations/{target}/send', json={},
            headers={**csrf, 'Idempotency-Key': str(uuid4()), 'If-Match': f'"{target}:0"'})
        assert response.status_code == 200, response.json
        assert response.json['status'] == status, response.json
    response = client.get('/api/clinic/v1/worklist/rows')
    assert response.status_code == 200
    assert {row['dictation_id']: row['status'] for row in response.json['items']} == dict(targets)


if __name__ == '__main__':
    _exercise_submit()

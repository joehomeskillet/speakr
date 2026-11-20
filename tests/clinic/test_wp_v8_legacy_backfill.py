"""Legacy recording backfill (V8 v8-95): dry run, apply, idempotency, visibility.

Synthetic recordings and files only. SHARED-DB: assertions are scoped to the rows the test created.
"""
from __future__ import annotations

import hashlib
import importlib.util
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_login import LoginManager, login_user

from src.app import app, db
from src.clinic import legacy_backfill, policy, worklist_entries, workflow
from src.clinic.identity import create_tables as _create_identity_tables
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.clinic.revisions import KIND_ASR, current_revision
from src.clinic.routes_recordings import PREFIX, create_blueprint
from src.models import AuditEvent, Recording, User
from src.services import audit

ROOT = Path(__file__).resolve().parents[2]
AUDIO = b'\x03ds2' + b'\x00' * 2048
TEXT = 'Synthetischer Befund ohne Bezug.'
MEETING = datetime(2026, 11, 17, 9, 30)
CREATED = datetime(2026, 11, 18, 14, 5)


@pytest.fixture(autouse=True)
def _setup(clinic_on, tmp_path, monkeypatch):
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path / 'originals'))
    with app.app_context():
        _create_identity_tables()
    audit.flush()
    audit.reset_memory()
    yield
    audit.flush()
    audit.reset_memory()


def _snapshot(recording):
    return {c.name: getattr(recording, c.name) for c in Recording.__table__.columns}


@pytest.fixture
def scene(tmp_path):
    """Owner in a pool, owner without a pool, four legacy recordings, one already linked."""
    with app.app_context():
        tag = uuid.uuid4().hex[:8]
        owner = User(username=f'lb_o_{tag}', email=f'lb_o_{tag}@local.test', password='x', name='Dr. Synthetisch')
        bare = User(username=f'lb_n_{tag}', email=f'lb_n_{tag}@local.test', password='x')
        db.session.add_all([owner, bare])
        db.session.flush()
        pool = ClinicAuthorPool(name=f'lb pool {tag}', created_by=owner.id)
        db.session.add(pool)
        db.session.flush()
        db.session.add(ClinicAuthorPoolMember(pool_id=pool.id, user_id=owner.id, created_by=owner.id))

        def make(user, name, content, **fields):
            path = tmp_path / 'legacy' / name
            path.parent.mkdir(exist_ok=True)
            if content is not None:
                path.write_bytes(content)
            fields.setdefault('status', 'COMPLETED')
            recording = Recording(user_id=user.id, original_filename=name,
                                  audio_path=str(path), created_at=CREATED, **fields)
            db.session.add(recording)
            db.session.flush()
            return recording

        done = make(owner, 'done.ds2', AUDIO + b'1', meeting_date=MEETING, transcription=TEXT,
                    audio_duration_seconds=42.0)
        failed = make(owner, 'failed.ds2', AUDIO + b'2', status='FAILED')
        nopool = make(bare, 'nopool.ds2', AUDIO + b'3', transcription=TEXT)
        missing = make(owner, 'missing.ds2', None)
        busy = make(owner, 'busy.ds2', AUDIO + b'5', status='SUMMARIZING', transcription=TEXT)
        # already linked through the web-ingest receipt of an earlier upload
        linked = make(owner, 'linked.ds2', AUDIO + b'4')
        db.session.commit()
        from src.clinic.ingest import IngestService
        from src.clinic import startup
        service = IngestService(startup.originals_root(app))
        with open(linked.audio_path, 'rb') as stream:
            service.store_original(
                stream, declared_sha256=hashlib.sha256(AUDIO + b'4').hexdigest(), declared_size=len(AUDIO) + 1,
                origin='web-upload', origin_ref=f'upload:recording:{linked.id}:time=upload',
                author_user_id=owner.id, idempotency_key=f'web:{owner.id}:x', now=CREATED.replace(
                    tzinfo=__import__('datetime').timezone.utc))
        ids = SimpleNamespace(owner=owner.id, bare=bare.id, pool=pool.id, done=done.id, failed=failed.id,
                              nopool=nopool.id, missing=missing.id, linked=linked.id, busy=busy.id)
        yield ids
        db.session.rollback()


def _mine(rows, ids):
    mine = {ids.done, ids.failed, ids.nopool, ids.missing, ids.linked, ids.busy}
    return {row.recording_id: row for row in rows if row.recording_id in mine}


def _bytes_and_columns(ids):
    with app.app_context():
        out = {}
        for rid in (ids.done, ids.failed, ids.nopool, ids.missing, ids.linked, ids.busy):
            recording = db.session.get(Recording, rid)
            path = Path(recording.audio_path)
            out[rid] = (_snapshot(recording), path.read_bytes() if path.exists() else None)
        return out


def test_dry_run_lists_every_candidate_and_changes_nothing(scene):
    before = _bytes_and_columns(scene)
    with app.app_context():
        count = ClinicDictation.query.count()
        rows = _mine(legacy_backfill.plan(), scene)
        assert ClinicDictation.query.count() == count
    assert {rid: (r.action, r.reason) for rid, r in rows.items()} == {
        scene.done: ('create', 'with_transcription'),
        scene.failed: ('create', 'without_transcription'),
        scene.nopool: ('skip', 'no_author_pool'),
        scene.missing: ('skip', 'no_audio_file'),
        scene.linked: ('skip', 'already_linked'),
        scene.busy: ('skip', 'not_finished'),
    }
    assert rows[scene.done].owner_id == scene.owner and rows[scene.done].pool_id == scene.pool
    assert _bytes_and_columns(scene) == before


def test_apply_creates_dictations_with_author_pool_time_and_text_then_nothing(scene):
    before = _bytes_and_columns(scene)
    with app.app_context():
        created = {r.recording_id: r for r in legacy_backfill.apply() if r.dictation_id}
        assert set(created) >= {scene.done, scene.failed}
        assert {scene.nopool, scene.missing, scene.linked, scene.busy}.isdisjoint(created)

        done = db.session.get(ClinicDictation, created[scene.done].dictation_id)
        assert done.author_user_id == scene.owner and done.pool_id == scene.pool
        assert done.created_at.replace(tzinfo=None) == MEETING
        anchor = db.session.get(Recording, done.recording_id)
        assert anchor.meeting_date == MEETING and anchor.status == 'COMPLETED' and not anchor.audio_path
        original = ClinicOriginal.query.filter_by(dictation_id=done.id).one()
        assert original.origin_ref == f'legacy:recording:{scene.done}:time=recording'
        assert original.content_hash == hashlib.sha256(AUDIO + b'1').hexdigest()
        head = current_revision(done)
        assert head.kind == KIND_ASR and head.body == TEXT
        flow = db.session.get(workflow.ClinicWorkflowDictation, done.id)
        assert flow.status == workflow.DRAFT and flow.pool_id == scene.pool and flow.audio_seconds == 42

        failed = db.session.get(ClinicDictation, created[scene.failed].dictation_id)
        assert failed.created_at.replace(tzinfo=None) == CREATED  # upload time fallback
        assert ClinicOriginal.query.filter_by(dictation_id=failed.id).one().origin_ref.endswith(':time=upload')
        assert db.session.get(Recording, failed.recording_id).meeting_date is None
        assert db.session.get(Recording, failed.recording_id).status == 'FAILED'
        assert current_revision(failed) is None  # no text for a failed recording
        done_id, failed_id = done.id, failed.id

        count = ClinicDictation.query.count()
        second = legacy_backfill.apply()
        assert [r.dictation_id for r in second if r.dictation_id] == []
        assert ClinicDictation.query.count() == count
        assert _mine(second, scene)[scene.done].reason == 'already_linked'
    audit.flush()
    with app.app_context():
        events = AuditEvent.query.filter_by(event='recording.edited', actor_kind='system').all()
        anchors = {db.session.get(ClinicDictation, i).recording_id for i in (done_id, failed_id)}
        assert anchors <= {e.recording_id for e in events}
    assert _bytes_and_columns(scene) == before  # source rows and files are untouched


def test_asr_failure_rolls_back_the_dictation_and_a_rerun_retries(scene, monkeypatch):
    from src.clinic import correction
    real = correction.ingest_asr_result

    def boom(*args, **kwargs):
        raise RuntimeError('synthetic')

    monkeypatch.setattr(correction, 'ingest_asr_result', boom)
    with app.app_context():
        rows = _mine(legacy_backfill.apply(), scene)
        assert (rows[scene.done].action, rows[scene.done].reason) == ('skip', 'asr_failed')
        assert rows[scene.done].dictation_id is None
        assert ClinicOriginal.query.filter(ClinicOriginal.origin_ref.like(f'legacy:recording:{scene.done}:%')).count() == 0
        monkeypatch.setattr(correction, 'ingest_asr_result', real)
        again = _mine(legacy_backfill.apply(), scene)
        dictation = db.session.get(ClinicDictation, again[scene.done].dictation_id)
        assert current_revision(dictation).body == TEXT


def test_created_dictation_is_listed_and_in_the_worklist_for_the_author(scene):
    with app.app_context():
        created = {r.recording_id: r.dictation_id for r in legacy_backfill.apply() if r.dictation_id}
        flask = Flask(__name__)
        flask.config.update(TESTING=True, SECRET_KEY='synthetic-v895',
                            SQLALCHEMY_DATABASE_URI=app.config['SQLALCHEMY_DATABASE_URI'],
                            SQLALCHEMY_ENGINE_OPTIONS=app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}))
        db.init_app(flask)
        policy.install_pool_policy(flask)
        login = LoginManager(flask)
        login.user_loader(lambda uid: db.session.get(User, int(uid)))
        flask.register_blueprint(create_blueprint(worklist_entries.load_entries))

        @flask.get('/synthetic-login/<int:uid>')
        def browser(uid):
            login_user(db.session.get(User, uid))
            return {}

        entries = {e.row['dictation_id']: e for e in worklist_entries.load_entries(scene.owner)}
        assert {created[scene.done], created[scene.failed]} <= set(entries)
        assert entries[created[scene.done]].row['status'] == workflow.DRAFT
    client = flask.test_client()
    assert client.get(f'/synthetic-login/{scene.owner}').status_code == 200
    response = client.get(PREFIX)
    assert response.status_code == 200
    rows = {row['dictation_id']: row for row in response.json['items']}
    done, failed = rows[created[scene.done]], rows[created[scene.failed]]
    assert done['time_source'] == 'recorded' and done['sort_time'] == '2026-11-17T09:30:00Z'
    assert done['processing_status'] == 'COMPLETED' and done['dictated_by']['display_name'] == 'Dr. Synthetisch'
    assert failed['time_source'] == 'created' and failed['processing_status'] == 'FAILED'
    assert failed['recorded_at'] is None and done['duration_ms'] == 42000


def _runner():
    spec = importlib.util.spec_from_file_location('clinic_backfill_runner', ROOT / 'scripts' / 'clinic_backfill_dictations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_defaults_to_dry_run_and_apply_prints_created_ids(scene, capsys):
    runner = _runner()
    with app.app_context():
        count = ClinicDictation.query.count()
    assert runner.run([]) == 0
    out = capsys.readouterr().out
    assert f'recording={scene.done} owner={scene.owner} action=create reason=with_transcription' in out
    assert f'recording={scene.nopool} owner={scene.bare} action=skip reason=no_author_pool' in out
    assert 'nothing changed' in out
    with app.app_context():
        assert ClinicDictation.query.count() == count
    assert runner.run(['--apply']) == 0
    out = capsys.readouterr().out
    assert 'created dictations: ' in out and 'none' not in out
    assert TEXT not in out and 'Dr. Synthetisch' not in out  # ids and reasons only
    assert runner.run(['--apply']) == 0
    assert 'created dictations: none' in capsys.readouterr().out


def test_runner_refuses_when_clinic_mode_is_off(scene, monkeypatch, capsys):
    monkeypatch.delenv('CLINIC_MODE')
    assert _runner().run(['--apply']) == 2
    with app.app_context():
        assert ClinicOriginal.query.filter(ClinicOriginal.origin_ref.like(f'legacy:recording:{scene.done}:%')).count() == 0

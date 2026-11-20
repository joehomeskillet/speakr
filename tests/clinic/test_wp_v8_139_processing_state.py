"""Real database projections, upload links, visibility and bounded query counts."""
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from src.clinic import policy, pools, recognition_state, recordings_list, routes_jobs, workflow, worklist_entries
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.pools import ClinicAuthorPool
from src.clinic.web_ingest import dictations_for_recordings
from src.database import db
from src.models import ProcessingJob, Recording
from test_wp_v8_jobs_route import world  # noqa: F401


def linked(world, state='queued'):
    if not hasattr(world, 'pool'):
        world.pool = ClinicAuthorPool(name='Synthetic authors', created_by=world.admin.id)
        db.session.add(world.pool)
        db.session.flush()
    anchor = Recording(user_id=world.alice.id, status='PENDING')
    source = Recording(user_id=world.alice.id, status='PENDING')
    db.session.add_all([anchor, source])
    db.session.flush()
    d = ClinicDictation(recording_id=anchor.id, author_user_id=world.alice.id, pool_id=world.pool.id)
    db.session.add(d)
    db.session.flush()
    original = ClinicOriginal(dictation_id=d.id, content_hash='a' * 64, size_bytes=1,
                              storage_key='synthetic', receipt='{}',
                              origin_ref=f'upload:recording:{source.id}:time=upload')
    job = ProcessingJob(user_id=world.alice.id, recording_id=source.id,
                        job_type='transcribe', status=state)
    db.session.add_all([original, job, workflow.ClinicWorkflowDictation(
        id=d.id, author_user_id=world.alice.id, pool_id=world.pool.id, status=workflow.DRAFT)])
    db.session.flush()
    return d, source, job, original


@contextmanager
def queries():
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.engine, 'before_cursor_execute', capture)
    try:
        yield statements
    finally:
        event.remove(db.engine, 'before_cursor_execute', capture)


@pytest.mark.parametrize('state,asr', [('queued', 'PENDING'), ('processing', 'RUNNING'), ('failed', 'FAILED')])
def test_lists_and_snapshot_use_upload_job_not_stale_anchor(world, state, asr):
    d, source, job, original = linked(world, state)
    entries = worklist_entries.load_entries(world.alice.id)
    assert next(e.row for e in entries if e.row['dictation_id'] == d.id)['asr_state'] == asr
    assert worklist_entries.dictation_snapshot(d)['asr_state'] == asr
    result = recordings_list.list_recordings(entries, policy.build_subject(world.alice.id))
    assert next(r for r in result['items'] if r['dictation_id'] == d.id)['processing_status'] == state.upper()
    assert recognition_state.asr_outcome(d) == ('failed' if state == 'failed' else 'running')


def test_late_asr_keeps_completion_with_human_head(world):
    d, source, job, original = linked(world, 'failed')
    asr = ClinicTranscriptRevision(dictation_id=d.id, kind='ASR', body='synthetic',
        normalised_text_hash='a', original_version_id=original.id, model_ref='local-test')
    db.session.add(asr)
    db.session.flush()
    human = ClinicTranscriptRevision(dictation_id=d.id, kind='HUMAN', body='edited',
        normalised_text_hash='b', original_version_id=original.id, parent_revision_id=asr.id)
    db.session.add(human)
    db.session.flush()
    assert recognition_state.asr_outcome(d, detailed=True) == 'completed'
    assert recognition_state.asr_outcome(d) == 'done'
    assert worklist_entries.dictation_snapshot(d)['asr_state'] == 'SUCCEEDED'


def test_batch_queries_do_not_grow_per_row(world):
    rows = [linked(world) for _ in range(20)]
    for count in (1, 20):
        with queries() as statements:
            result = recognition_state.asr_outcomes(r[0] for r in rows[:count])
        assert len(statements) == 4
        assert set(result.values()) == {'queued'}
        with queries() as statements:
            mapping = dictations_for_recordings(r[1].id for r in rows[:count])
        assert len(statements) == 2
        assert len(mapping) == count


def test_job_links_preserve_owner_and_anchor_priority(world):
    d, source, job, original = linked(world, 'failed')
    db.session.add(ClinicOriginal(dictation_id=d.id, content_hash='b' * 64, size_bytes=1,
        storage_key='foreign', receipt='{}', origin_ref=f'upload:recording:{world.rec_b.id}:time=upload'))
    db.session.flush()
    mapping = dictations_for_recordings([source.id, d.recording_id, world.rec_b.id])
    assert mapping[source.id] is d and mapping[d.recording_id] is d
    assert mapping[world.rec_b.id].author_user_id == world.bob.id
    for user, visible in ((world.alice, True), (world.bob, False), (world.admin, True)):
        data = routes_jobs.health_payload(user)
        assert (job.id in {r['id'] for r in data['jobs']}) is visible
        assert (d.id in {r['dictation_id'] for r in data['recent_failures']}) is visible
    direct = ClinicDictation(recording_id=source.id, author_user_id=world.alice.id)
    db.session.add(direct)
    db.session.flush()
    assert dictations_for_recordings([source.id])[source.id] is direct


def test_linked_upload_jobs_follow_pool_grants_and_explicit_denies(world):
    d, source, job, original = linked(world)
    pools.allow(world.bob.id, 'pool', actor_id=world.admin.id, pool_id=world.pool.id, actions=('view',))
    assert job.id in {r['id'] for r in routes_jobs.health_payload(world.bob)['jobs']}
    pools.deny(world.bob.id, 'pool', actor_id=world.admin.id, pool_id=world.pool.id, actions=('view',))
    assert job.id not in {r['id'] for r in routes_jobs.health_payload(world.bob)['jobs']}

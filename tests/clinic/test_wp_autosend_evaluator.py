"""Synthetic stable snapshots through the registered import-agent routes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.app import db
from src.clinic import agent_channel as ch
from src.clinic import import_centre as ic
from src.clinic import workflow
from src.clinic.author_aliases import add_author_alias
from src.clinic.import_source_config import create_source_config, update_source_config
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.pools import add_author_member, create_author_pool
from src.models import ProcessingJob

from test_wp_author_aliases_wire import machine  # noqa: F401 - registered-route fixture
from test_wp_v8_agent_routes import FINGERPRINT, SECRET, _begin, _finalize, _item, _json, _put_chunks, _user


@pytest.fixture
def autosend(machine, monkeypatch, tmp_path):
    monkeypatch.setenv('CLINIC_IMPORT_ROOT_ALLOWLIST', str(tmp_path))
    admin, author, other = (_user() for _ in range(3))
    admin.is_admin = True
    db.session.commit()
    policy = ch.ChannelPolicy(master_secret=SECRET, authorise=lambda *_: True)
    agent = ch.register_agent(policy, actor_user_id=admin.id, display_name='Synthetic recorder',
                              allowed_sources=['usb-1', 'usb-2'], credential_fingerprint=FINGERPRINT)

    def source(root='usb-1', bound_author=None, **settings):
        config = create_source_config(
            actor_user_id=admin.id, source_kind='device', source_ref=root, approved_root=str(tmp_path / root),
            target_author_user_id=author.id, may_assign_author=lambda *_: True, **settings,
        )
        if bound_author is not None:
            add_author_alias(actor_user_id=admin.id, kind='device', code=root, author_user_id=bound_author,
                             provenance='Synthetic approval', may_assign_author=lambda *_: True)
        return config

    return {**machine, 'agent_id': agent['agent_id'], 'admin_id': admin.id, 'author_id': author.id,
            'other_id': other.id, 'policy': policy, 'source': source}


def _eol(*, data=b'synthetic audio', snapshot='eol', **changes):
    return {**_item(data, snapshot), 'completion_source': 'philips_eol',
            'observed_completion': 'completed', 'evidence_level': 'fixture', **changes}


def _report(env, *items, status=200):
    reply = _json(env, 'put', '/inventory', {'items': items})
    assert reply.status_code == status, reply.get_json()
    if status == 200:
        assert reply.get_json() == {'accepted': len(items)}


def _poll(env):
    reply = _json(env, 'get', '/intents')
    assert reply.status_code == 200, reply.get_json()
    return reply.get_json()['intents']


def test_eol_only_queues_one_signed_snapshot_for_a_resolved_author(autosend):
    env = autosend
    env['source'](bound_author=env['author_id'], autosend_enabled=True)
    env['source']('usb-2', autosend_enabled=True)
    add_author_alias(actor_user_id=env['admin_id'], kind='author', code='QA-A', author_user_id=env['author_id'],
                     provenance='Synthetic approval', may_assign_author=lambda *_: True)
    eligible = _eol()
    _report(env, eligible, eligible, _item(snapshot='non-eol'), _eol(snapshot='unstable', file_stable=False),
            _eol(snapshot='empty', data=b''), _eol(snapshot='spoofed', evidence_level='unverified'),
            _eol(snapshot='mtime', basis='mtime'), _eol(snapshot='draft', observed_completion='draft'),
            _eol(snapshot='document', relative_path='Inbox/Scan.PDF'),
            _eol(snapshot='unknown-author', root_id='usb-2', claimed_author_code='QA-A'))
    _report(env, eligible)
    intent, = _poll(env)
    assert intent['post_step'] == 'none'
    assert (intent['agent_id'], intent['source_id'], intent['snapshot_version']) == (env['agent_id'], 'src-1', 'eol')
    assert ch.verify_intent_wire(ch.derive_agent_key(env['policy'], env['agent_id'], 1), intent,
                                 expected_agent_id=env['agent_id'], now=ch.utcnow())
    assert ch.ClinicAgentIntent.query.count() == ic.ClinicImportIntent.query.count() == 1
    assert ch.ClinicAgentInventory.query.filter_by(snapshot_version='unknown-author').one().root_id == 'usb-2'
    assert ic.ClinicImportDelivery.query.count() == 0


@pytest.mark.parametrize(('autosend_enabled', 'eol_filter_enabled', 'expected'), [
    (False, False, []), (True, True, []), (True, False, ['non-eol']),
])
def test_filter_off_is_explicit_non_eol_opt_in(autosend, autosend_enabled, eol_filter_enabled, expected):
    env = autosend
    env['source'](bound_author=env['author_id'], autosend_enabled=autosend_enabled,
                  eol_filter_enabled=eol_filter_enabled)
    _report(env, _item(snapshot='non-eol'), {**_item(snapshot='unstable'), 'file_stable': False})
    assert [wire['snapshot_version'] for wire in _poll(env)] == expected
    saved = ch.ClinicAgentInventory.query.filter_by(snapshot_version='non-eol').one()
    assert (saved.completion_source, saved.evidence_level) == ('unknown', 'unverified')


@pytest.mark.parametrize('ambiguous_root', [False, True])
def test_mismatching_or_ambiguously_owned_device_never_queues(autosend, ambiguous_root):
    env = autosend
    env['source'](bound_author=env['author_id'] if ambiguous_root else env['other_id'], autosend_enabled=True)
    if ambiguous_root:
        ch.register_agent(env['policy'], actor_user_id=env['admin_id'], display_name='Second synthetic recorder',
                          allowed_sources=['USB-1'], credential_fingerprint='bb' * 20)
    _report(env, _eol())
    assert _poll(env) == []
    assert ic.ClinicImportIntent.query.count() == 0


def test_replay_after_cancellation_cannot_reissue_or_rebind(autosend):
    env = autosend
    env['source'](bound_author=env['author_id'], autosend_enabled=True)
    _report(env, _eol())
    wire, = _poll(env)
    _report(env, _eol(data=b'different bytes'), status=409)
    row = ch.ClinicAgentInventory.query.filter_by(snapshot_version='eol').one()
    assert row.sha256 == _eol()['sha256']
    ch.cancel_intent(env['policy'], actor_user_id=env['admin_id'], intent_id=wire['intent_id'])
    _report(env, _eol())
    assert _poll(env) == []
    assert ch.ClinicAgentIntent.query.count() == ic.ClinicImportIntent.query.count() == 1


def test_concurrent_replays_share_one_logical_intent(autosend):
    env = autosend
    env['source'](bound_author=env['author_id'], autosend_enabled=True)
    ready = Barrier(2)

    def report(_):
        request_env = {**env, 'client': env['client'].application.test_client()}
        ready.wait(timeout=10)
        _report(request_env, _eol())

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(report, range(2)))
    assert len(_poll(env)) == 1
    assert ch.ClinicAgentInventory.query.count() == 1
    assert ch.ClinicAgentIntent.query.count() == ic.ClinicImportIntent.query.count() == 1


def test_failed_pair_write_rolls_back_the_report(autosend, monkeypatch):
    env = autosend
    env['source'](bound_author=env['author_id'], autosend_enabled=True)
    add = db.session.add

    def fail_pair_link(row, *args, **kwargs):
        if isinstance(row, ic.ClinicAutosendIntent):
            raise SQLAlchemyError('synthetic autosend link write failure')
        return add(row, *args, **kwargs)

    with monkeypatch.context() as failing:
        failing.setattr(db.session, 'add', fail_pair_link)
        _report(env, _eol(), status=503)
    assert ch.ClinicAgentInventory.query.count() == 0
    assert ch.ClinicAgentIntent.query.count() == ic.ClinicImportIntent.query.count() == 0
    _report(env, _eol())
    assert len(_poll(env)) == 1


@pytest.mark.parametrize(('eol', 'verified_conflict'), [(True, False), (True, True), (False, False)])
def test_finalize_uses_exact_pair_and_preserves_verified_author_check(autosend, eol, verified_conflict):
    env = autosend
    pool = create_author_pool('Synthetic routing', actor_id=env['admin_id'])
    add_author_member(pool.id, env['author_id'], actor_id=env['admin_id'])
    config = env['source'](bound_author=env['author_id'], autosend_enabled=True, eol_filter_enabled=eol,
                           target_pool_id=pool.id, language_id=1033, priority=128)
    add_author_alias(actor_user_id=env['admin_id'], kind='author', code='QA-B', author_user_id=env['other_id'],
                     provenance='Synthetic approval', may_assign_author=lambda *_: True)
    manual = ic.create_import_intent(actor_user_id=env['admin_id'], target_author_user_id=env['author_id'],
                                     may_assign_author=lambda *_: True, source_kind='device', source_ref='usb-1',
                                     expected_files=['dict1.ds2'])
    header = bytearray(1536)
    header[:4] = b'\x03ds2'
    if verified_conflict:
        header[0x0c:0x10] = b'QA-B'
        header[0x240:0x246] = b'Author'
        header[0x25e:0x262] = b'QA-B'
    audio = bytes(header) + bytes(512)

    def item(snapshot):
        return _eol(data=audio, snapshot=snapshot) if eol else _item(audio, snapshot)

    _report(env, item('first'), item('second'))
    wire = next(w for w in _poll(env) if w['snapshot_version'] == 'second')
    link = ic.ClinicAutosendIntent.query.filter_by(agent_intent_id=wire['intent_id']).one()
    # A later policy change limits future reports. Issued signed intents keep their TTL.
    update_source_config(actor_user_id=env['admin_id'], config_id=config['id'], expected_version=1,
                         autosend_enabled=False)
    _report(env, item('disabled'))
    assert {w['snapshot_version'] for w in _poll(env)} == {'first', 'second'}
    begun = _begin(env, wire, audio)
    assert begun.status_code == 201, begun.get_json()
    ingest_id = begun.get_json()['ingest_id']
    chunks = _put_chunks(env, ingest_id, audio)
    reply = _finalize(env, ingest_id, chunks, audio)
    assert reply.status_code == 200, reply.get_json()
    receipt = reply.get_json()
    assert _finalize(env, ingest_id, chunks, audio).get_json() == receipt
    assert db.session.get(ic.ClinicImportIntent, manual['id']).status == 'OPEN'
    assert ic.ClinicImportIntent.query.filter_by(status='OPEN').count() == (3 if verified_conflict else 2)
    if verified_conflict:
        assert receipt['state'] == 'UNKNOWN'
        assert receipt['import']['status'] == 'UNASSIGNED'
        assert receipt['import']['intent_id'] is None
        assert ProcessingJob.query.count() == 0
    else:
        assert receipt['state'] == 'STORED'
        assert receipt['import']['status'] == 'MATCHED'
        assert receipt['import']['intent_id'] == link.import_intent_id
        assert receipt['import']['language_id'] == 1033
        dictation = db.session.get(ClinicDictation, receipt['dictation_id'])
        assert dictation.author_user_id == env['author_id']
        assert dictation.pool_id == pool.id
        assert db.session.get(workflow.ClinicWorkflowDictation, dictation.id).priority == 128
        job = ProcessingJob.query.filter_by(user_id=env['author_id'], job_type='transcribe').one()
        original = db.session.get(ClinicOriginal, receipt['original_id'])
        assert original.origin_ref == f'upload:recording:{job.recording_id}:time=upload'
    saved = ch.ClinicAgentInventory.query.filter_by(snapshot_version='second').one()
    assert saved.completion_source == ('philips_eol' if eol else 'unknown')

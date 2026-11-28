"""Exercise dropfolder reports and uploads through the real machine API."""

import json
import wave

import pytest
from sqlalchemy import text

from src.app import db
from src.clinic import agent_channel as ch, import_centre as ic, workflow
from src.clinic.asr_capabilities import AsrCapability, AsrCapabilityRegistry, CapabilityEvidence
from src.clinic.asr_job_profile import asr_capability_target
from src.clinic.asr_profiles import set_profile
from src.clinic.import_source_config import create_source_config, update_source_config
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.pools import add_author_member, create_author_pool
from src.models import ProcessingJob

from test_par_import_intent_contract import OLD_ID, _legacy_engine
from test_wp_author_aliases_wire import machine  # noqa: F401 - registered-route fixture
from test_wp_autosend_evaluator import _poll, _report
from test_wp_device_send_metadata import _tagged_audio
from test_wp_v8_agent_routes import FINGERPRINT, SECRET, _begin, _finalize, _item, _json, _put_chunks, _user


@pytest.fixture
def dropfolders(machine, monkeypatch, tmp_path):
    monkeypatch.setenv('CLINIC_IMPORT_ROOT_ALLOWLIST', str(tmp_path))
    monkeypatch.setenv('ASR_BASE_URL', 'http://127.0.0.1:9000')
    admin = _user()
    admin.is_admin = True
    db.session.commit()
    policy = ch.ChannelPolicy(master_secret=SECRET, authorise=lambda *_: True)
    ch.register_agent(policy, actor_user_id=admin.id, display_name='Synthetic folders',
                      allowed_sources=['incoming-a', 'incoming-b'], credential_fingerprint=FINGERPRINT)
    return machine, admin.id


def _folder(env, admin_id, root, pattern, priority, lang_id, language, model):
    author_id = _user().id
    pool = create_author_pool(f'Pool {root}', actor_id=admin_id)
    add_author_member(pool.id, author_id, actor_id=admin_id)
    # The profile is German even when the folder explicitly selects English.
    profile = set_profile(author_id, actor_id=admin_id, language='de', model=model,
                          options={'source': 'headset'})
    config = create_source_config(
        actor_user_id=admin_id, source_kind='folder', source_ref=root, approved_root=str(env['root'] / root),
        target_author_user_id=author_id, target_pool_id=pool.id, language_id=lang_id, priority=priority,
        asr_profile_id=profile.id, autosend_enabled=True, eol_filter_enabled=False, file_pattern=pattern,
        may_assign_author=lambda *_: True,
    )
    evidence = CapabilityEvidence(AsrCapability.BATCH, asr_capability_target(language, model),
                                  'dropfolder-fixture', 'fixture:batch', 'runtime_passed')
    return config, evidence


def _wav(root, name):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x00' * 800)
    return path.read_bytes()


def _upload(env, wire, audio):
    snapshot = wire['snapshot_version']
    begin = _begin(env, wire, audio, key=f'{snapshot}-begin')
    assert begin.status_code == 201, begin.get_json()
    ingest_id = begin.get_json()['ingest_id']
    chunks = _put_chunks(env, ingest_id, audio)
    result = _finalize(env, ingest_id, chunks, audio, key=f'{snapshot}-finalize')
    assert result.status_code == 200, result.get_json()
    return result.get_json()


def test_two_dropfolders_use_bound_settings_and_replay_once(dropfolders, monkeypatch):
    env, admin_id = dropfolders
    first, en = _folder(env, admin_id, 'incoming-a', 'A-*.wav', 128, 1033, 'en', 'large-v3')
    second, de = _folder(env, admin_id, 'incoming-b', 'B-*.wav', 0, 1031, 'de', 'small')
    monkeypatch.setitem(env['client'].application.extensions, 'clinic_capability_registry',
                        AsrCapabilityRegistry([en, de]))
    manual = ic.create_import_intent(
        actor_user_id=admin_id, source_kind='folder', source_ref='incoming-a', expected_files=['a-take.wav'],
        target_author_user_id=first['target_author_user_id'], target_pool_id=first['target_pool_id'],
        may_assign_author=lambda *_: True,
    )
    audio = _wav(env['root'] / 'incoming-a', 'A-take.WAV')
    other_audio = _wav(env['root'] / 'incoming-b', 'B-take.wav')
    nested_audio = _wav(env['root'] / 'incoming-a', 'sub/A-take.wav')
    wrong_audio = _wav(env['root'] / 'incoming-b', 'A-ignore.wav')
    items = [
        {**_item(audio, 'first', 'one'), 'root_id': 'incoming-a', 'relative_path': 'A-take.WAV'},
        {**_item(other_audio, 'second', 'two'), 'root_id': 'incoming-b', 'relative_path': 'B-take.wav'},
        {**_item(nested_audio, 'nested', 'sub'), 'root_id': 'incoming-a', 'relative_path': 'sub/A-take.wav'},
        {**_item(nested_audio, 'nested-win', 'sub-win'), 'root_id': 'incoming-a', 'relative_path': 'sub\\A-take.wav'},
        {**_item(wrong_audio, 'wrong', 'skip'), 'root_id': 'incoming-b', 'relative_path': 'A-ignore.wav'},
        {**_item(audio, 'unstable', 'pending'), 'root_id': 'incoming-a', 'relative_path': 'A-pending.wav',
         'file_stable': False},
    ]
    _report(env, *items)
    wires = {wire['snapshot_version']: wire for wire in _poll(env)}
    assert set(wires) == {'first', 'second'}
    restarted = {**env, 'client': env['client'].application.test_client()}
    _report(restarted, *items)
    assert {wire['intent_id'] for wire in _poll(restarted)} == {wire['intent_id'] for wire in wires.values()}
    update_source_config(actor_user_id=admin_id, config_id=first['id'], expected_version=1,
                         priority=1, language_id=1031, asr_profile_id=second['asr_profile_id'])

    for snapshot, content, config, language, model in (
        ('first', audio, first, 'en', 'large-v3'), ('second', other_audio, second, 'de', 'small'),
    ):
        wire = wires[snapshot]
        receipt = _upload(restarted, wire, content)
        assert receipt['state'] == 'STORED'
        assert receipt['permitted_post_step'] == 'none'
        binding = ic.ClinicAutosendIntent.query.filter_by(agent_intent_id=wire['intent_id']).one()
        assert receipt['import']['intent_id'] == binding.import_intent_id
        assert receipt['import']['language_id'] == config['language_id']
        intent = db.session.get(ic.ClinicImportIntent, binding.import_intent_id)
        assert ic.interpret(intent)['priority'] == {'op': 'set', 'value': config['priority']}
        dictation = db.session.get(ClinicDictation, receipt['dictation_id'])
        assert dictation.author_user_id == config['target_author_user_id']
        assert dictation.pool_id == config['target_pool_id']
        assert db.session.get(workflow.ClinicWorkflowDictation, dictation.id).priority == config['priority']
        job = ProcessingJob.query.filter_by(user_id=dictation.author_user_id, job_type='transcribe').one()
        params = json.loads(job.params)
        assert (params['language'], params['transcription_model']) == (language, model)
        assert (params['clinic_asr']['profile'], params['clinic_asr']['profile_version']) == (
            config['asr_profile_id'], 1,
        )
        outcome = _json(restarted, 'post', '/outcomes', {
            'intent_id': wire['intent_id'], 'ingest_id': receipt['ingest_id'], 'outcome': 'stored',
        })
        assert outcome.status_code == 200, outcome.get_json()
        assert outcome.get_json() == {'recorded': True, 'violation': False}

    after_restart = {**env, 'client': env['client'].application.test_client()}
    _report(after_restart, *items)
    assert _poll(after_restart) == []
    assert db.session.get(ic.ClinicImportIntent, manual['id']).status == 'OPEN'
    assert ic.ClinicAutosendIntent.query.count() == ch.ClinicAgentIntent.query.count() == 2
    assert ClinicDictation.query.count() == ClinicOriginal.query.count() == ProcessingJob.query.count() == 2


def test_folder_cannot_apply_another_authors_profile(dropfolders, monkeypatch):
    env, admin_id = dropfolders
    own, own_evidence = _folder(env, admin_id, 'incoming-a', '*.mp3', 128, 1031, 'de', 'large-v3')
    other, other_evidence = _folder(env, admin_id, 'incoming-b', '*.wav', 1, 1031, 'de', 'small')
    monkeypatch.setitem(env['client'].application.extensions, 'clinic_capability_registry',
                        AsrCapabilityRegistry([own_evidence, other_evidence]))
    update_source_config(actor_user_id=admin_id, config_id=own['id'], expected_version=1,
                         asr_profile_id=other['asr_profile_id'])

    audio = _tagged_audio('Take', priority=0, use_asr=True)
    _report(env, {**_item(audio, 'foreign'), 'root_id': 'incoming-a', 'relative_path': 'take.mp3'})
    wire, = _poll(env)
    receipt = _upload(env, wire, audio)
    assert receipt['state'] == 'STORED'
    assert db.session.get(ClinicDictation, receipt['dictation_id']).author_user_id == own['target_author_user_id']
    assert db.session.get(workflow.ClinicWorkflowDictation, receipt['dictation_id']).priority == 128
    profile = json.loads(ProcessingJob.query.one().params)['clinic_asr']
    assert profile['status'] == 'profile_missing'
    assert profile['profile'] is None


def test_existing_import_intents_have_no_folder_profile_after_upgrade(tmp_path):
    engine = _legacy_engine(tmp_path / 'legacy.sqlite')
    try:
        ic.upgrade(engine)
        assert ic.upgrade(engine) == []
        with engine.connect() as connection:
            selected = connection.execute(text(
                'SELECT asr_profile_id FROM clinic_import_intent WHERE id = :id',
            ), {'id': OLD_ID}).one()
        assert selected == (None,)
    finally:
        engine.dispose()

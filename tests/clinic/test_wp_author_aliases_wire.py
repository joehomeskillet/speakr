"""Synthetic DSS/DS2 author evidence through the registered machine routes."""

from datetime import timedelta

import pytest
from flask import Flask

from src.app import app, db, register_clinic_operations
from src.clinic import agent_channel as ch
from src.clinic import import_centre as ic
from src.clinic.author_aliases import add_author_alias
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.import_visibility import ClarificationPolicy
from src.clinic.models import ClinicDictation
from src.models import Recording

from test_wp_v8_agent_routes import FINGERPRINT, PROXY, SECRET, _begin, _headers, _item, _json, _put_chunks, _sha, _user


@pytest.fixture
def machine(clinic_on, monkeypatch, tmp_path):
    monkeypatch.setenv('CLINIC_AGENT_PROXY_SECRET', PROXY)
    monkeypatch.setenv('CLINIC_AGENT_MASTER_SECRET', SECRET.decode('ascii'))
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path))
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=app.config['SQLALCHEMY_DATABASE_URI'],
                        SQLALCHEMY_ENGINE_OPTIONS=app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}))
    flask.extensions['clinic_run_once'] = lambda: None
    db.init_app(flask)
    register_clinic_operations(flask)
    with app.app_context():
        yield {'client': flask.test_client(), 'root': tmp_path}


@pytest.mark.parametrize(('case', 'matched'), [
    ('ds2', True), ('ds2-with-metadata', True), ('dss', True), ('dss3', True), ('unknown', False), ('ambiguous', False),
    ('contradictory', False), ('hint-only', False), ('exclusive', True), ('unknown-on-device', False),
    ('device-conflict', False), ('reused-device', False), ('inactive', False), ('bad-digest', False),
])
def test_finalize_uses_verified_bytes_and_stored_aliases(machine, case, matched):
    admin, author, desktop = (_user() for _ in range(3))
    admin.is_admin = True
    db.session.commit()
    policy = ch.ChannelPolicy(master_secret=SECRET, authorise=lambda *_: True)
    agent = ch.register_agent(policy, actor_user_id=admin.id, display_name='Synthetic recorder',
                              allowed_sources=['usb-1'], credential_fingerprint=FINGERPRINT)

    def alias(kind, code, user):
        add_author_alias(actor_user_id=admin.id, kind=kind, code=code, author_user_id=user.id,
                         provenance='Synthetic approval', may_assign_author=lambda *_: True)

    alias('author', 'QA-A', author)
    alias('author', 'QA-B', desktop)
    if case == 'ambiguous':
        alias('author', 'QA-A', desktop)
    if case in ('exclusive', 'device-conflict', 'unknown-on-device', 'reused-device'):
        alias('device', 'USB-1', desktop if case == 'device-conflict' else author)
    if case == 'reused-device':
        ch.register_agent(policy, actor_user_id=admin.id, display_name='Second synthetic recorder',
                          allowed_sources=['USB-1'], credential_fingerprint='bb' * 20)
    if case == 'inactive':
        db.session.add(ClinicAccountPolicy(user_id=author.id, status='disabled'))
        db.session.commit()

    magic = {'dss': b'\x02dss', 'dss3': b'\x03dss'}.get(case, b'\x03ds2')
    header = bytearray(512 * magic[0])
    header[:4] = magic
    if case not in ('hint-only', 'exclusive', 'reused-device'):
        code = b'NONE' if case in ('unknown', 'unknown-on-device') else b'QA-A'
        header[0x0c:0x10] = code
        if magic == b'\x03ds2':
            header[0x240:0x246] = b'Author'
            header[0x25e:0x262] = b'QA-B' if case == 'contradictory' else code
    data = bytes(header) + bytes(512)
    reported = data[:-1] + b'!' if case == 'bad-digest' else data
    filename = 'dict1.' + magic[1:].decode('ascii')
    item = {**_item(reported), 'relative_path': f'Inbox/{filename}',
            'claimed_author_code': 'QA-B' if matched else 'QA-A', 'desktop_login': desktop.username}
    assert _json(machine, 'put', '/inventory', {'items': [item]}).status_code == 200
    now = ch.utcnow()
    earlier = ic.create_import_intent(actor_user_id=admin.id, target_author_user_id=desktop.id,
                                      may_assign_author=lambda *_: True, source_kind='device', source_ref='usb-1',
                                      expected_files=[filename], now=now - timedelta(minutes=1))
    title = 'Verified DS2 import'
    overrides = {'title': {'op': 'set', 'value': title}} if case == 'ds2-with-metadata' else None
    expected = ic.create_import_intent(actor_user_id=admin.id, target_author_user_id=author.id,
                                       may_assign_author=lambda *_: True, source_kind='device', source_ref='usb-1',
                                       expected_files=[filename], now=now, overrides=overrides)
    intent = ch.create_intent(policy, actor_user_id=admin.id, agent_id=agent['agent_id'],
                              source_id='src-1', snapshot_version='v1')
    begun = _begin(machine, intent, reported)
    assert begun.status_code == 201, begun.get_json()
    ingest_id = begun.get_json()['ingest_id']
    chunks = _put_chunks(machine, ingest_id, data)
    body = {'chunks': chunks, 'total_bytes': len(data), 'sha256': _sha(reported),
            'embedded_author': 'QA-B' if matched else 'QA-A', 'author_user_id': desktop.id,
            'desktop_login': desktop.username}

    def finalize():
        response = _json(machine, 'post', f'/ingests/{ingest_id}/finalize', body,
                         headers=_headers(**{'Idempotency-Key': 'author-finalize'}))
        assert response.status_code == 200, response.get_json()
        return response.get_json()

    receipt = finalize()
    assert finalize() == receipt
    assert db.session.get(ic.ClinicImportIntent, earlier['id']).status == 'OPEN'
    if case == 'bad-digest':
        assert receipt['state'] == 'INVALID'
        assert ic.ClinicImportDelivery.query.filter_by(ingest_id=ingest_id).first() is None
        return
    delivery = receipt['import']
    if matched:
        assert receipt['state'] == 'STORED'
        assert delivery['status'] == 'MATCHED'
        assert delivery['intent_id'] == expected['id']
        dictation = db.session.get(ClinicDictation, receipt['dictation_id'])
        assert dictation.author_user_id == author.id
        if case == 'ds2-with-metadata':
            assert db.session.get(Recording, dictation.recording_id).title == title
    else:
        assert receipt['state'] == 'UNKNOWN'
        assert delivery['status'] == 'UNASSIGNED'
        assert delivery['intent_id'] is None
        assert receipt['dictation_id'] is None
        visible = ic.list_unassigned(actor_user_id=admin.id, policy=ClarificationPolicy(may_access=lambda *_: True))
        assert any(row['id'] == delivery['delivery_id'] and row['original_state'] == 'author_required'
                   for row in visible)
        original = db.session.get(ic.ClinicImportOriginal, delivery['delivery_id'])
        assert (machine['root'] / original.storage_key).read_bytes() == data

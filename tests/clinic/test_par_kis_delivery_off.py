"""Synthetic Q-05 evidence for disabled KIS delivery and readable history."""

import json
from unittest.mock import Mock

from src.clinic import kis
from src.clinic.models import ClinicDelivery
from src.database import db
from src.services import job_queue as jq
from test_wp_v8_clinic_job_handlers import _run, env  # noqa: F401
from test_wp_v8_output_routes import NOW, _base, _body, _key, _release, case, web  # noqa: F401


def test_default_exports_refuse_kis_and_preserve_history(web, monkeypatch):
    monkeypatch.setenv('CLINIC_PROFILE', 'staging')
    monkeypatch.delenv('CLINIC_KIS_DELIVERY_ENABLED', raising=False)
    client, c = web
    release = _release(c)
    c['adapter'] = adapter = kis.MockKisAdapter()
    send = Mock(wraps=adapter.deliver)
    lookup = Mock(wraps=adapter.lookup)
    monkeypatch.setattr(adapter, 'deliver', send)
    monkeypatch.setattr(adapter, 'lookup', lookup)
    receipt = ClinicDelivery(release_id=release.id, target=adapter.name, idempotency_key='historical',
                             payload_hash='d' * 64, state='UNKNOWN', created_at=NOW)
    db.session.add(receipt)
    db.session.commit()
    cl, csrf = client(c['actor'])
    base = _base(c)

    refused = cl.post(base + '/exports', headers=_key() | csrf, json=_body(c, 'kis'))
    send.assert_not_called()
    assert (refused.status_code, refused.json['code']) == (503, 'KIS_UNAVAILABLE')
    targets = cl.get(base + '/output-targets')
    assert [t['id'] for t in targets.json['targets']] == ['txt', 'docx', 'clipboard']
    assert cl.post(base + '/exports', headers=_key() | csrf, json=_body(c)).status_code == 201
    assert kis.deliver(release, c['context'], adapter, 'new-send', NOW).outcome == kis.REFUSED
    assert kis.reconcile(receipt, adapter, NOW).outcome == kis.REFUSED
    send.assert_not_called()
    lookup.assert_not_called()

    status = cl.get(base + f'/exports/{receipt.id}')
    assert (status.status_code, status.json['status'], status.json['release_id']) == (200, 'unknown', release.id)
    assert client(c['other'])[0].get(base + f'/exports/{receipt.id}').status_code == 404
    db.session.refresh(receipt)
    assert (receipt.state, receipt.idempotency_key, receipt.payload_hash, receipt.external_document_id) == (
        'UNKNOWN', 'historical', 'd' * 64, None)
    assert db.session.query(ClinicDelivery).count() == 1


def test_default_finalize_completes_and_legacy_output_job_never_sends(env, monkeypatch):
    monkeypatch.setenv('CLINIC_PROFILE', 'staging')
    monkeypatch.delenv('CLINIC_KIS_DELIVERY_ENABLED', raising=False)
    adapter = env.app.extensions['clinic_kis_adapter'] = kis.MockKisAdapter()
    send = Mock(wraps=adapter.deliver)
    monkeypatch.setattr(adapter, 'deliver', send)
    monkeypatch.setattr('src.clinic.finalize_gates.trusted_context', lambda *_: True)

    finalized = _run(env, 'clinic_finalize', clinic_release_id=env.release, text_hash='h')
    legacy = _run(env, 'clinic_output', clinic_release_id=env.release, target='kis')
    send.assert_not_called()
    assert finalized.status == 'completed'
    assert legacy.status == 'failed' and 'KIS delivery is disabled' in legacy.error_message
    assert jq.CLINIC_DELIVERY_STARTED not in json.loads(legacy.params)
    assert db.session.query(ClinicDelivery).count() == 0

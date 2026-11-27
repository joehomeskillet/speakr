"""Callback regression coverage; these tests do not prove an ASR transport."""

import io

import pytest

from src.clinic.live_asr_adapter import LiveAsrAdapter
from src.services.transcription.base import TranscriptionRequest
from src.services.transcription.exceptions import TranscriptionError


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    return LiveAsrAdapter({'base_url': 'http://speaches:8000/realtime'})


def test_partial_callback_runs_before_next_event_and_preserves_final(adapter, monkeypatch):
    partials = []
    finals = []

    def events(*args, **kwargs):
        yield {'event': 'partial', 'seq': 0, 'text': 'Guten'}
        assert [response.text for response in partials] == ['Guten']
        assert finals == []
        yield {'event': 'final', 'seq': 1, 'text': 'Guten Tag', 'end_ms': 1000}

    monkeypatch.setattr(adapter, '_stream_events', events)
    result = adapter.stream_transcription(
        TranscriptionRequest(io.BytesIO(b'audio'), 'sample.wav', language='de'),
        on_partial=partials.append,
        on_final=finals.append,
    )
    assert partials[0].language == 'de'
    assert partials[0].provider == adapter.PROVIDER_NAME
    assert [response.text for response in finals] == ['Guten Tag']
    assert result.text == 'Guten Tag'


def test_partial_is_not_promoted_to_final(adapter, monkeypatch):
    monkeypatch.setattr(adapter, '_stream_events', lambda *a, **kw: iter([
        {'event': 'partial', 'seq': 0, 'text': 'Vorläufig'},
        {'event': 'response.text.delta', 'seq': 1, 'text': 'Not recognition'},
    ]))
    partials = []
    finals = []
    result = adapter.stream_transcription(
        TranscriptionRequest(io.BytesIO(b'audio'), 'sample.wav'),
        on_partial=partials.append,
        on_final=finals.append,
    )
    assert [response.text for response in partials] == ['Vorläufig']
    assert finals == []
    assert result.text == ''


def test_partial_callback_failure_propagates(adapter, monkeypatch):
    monkeypatch.setattr(adapter, '_stream_events', lambda *a, **kw: iter([
        {'event': 'partial', 'seq': 0, 'text': 'Vorläufig'},
    ]))

    def failed_callback(response):
        raise RuntimeError('client disconnected')

    with pytest.raises(TranscriptionError, match='Live ASR streaming failed'):
        adapter.stream_transcription(
            TranscriptionRequest(io.BytesIO(b'audio'), 'sample.wav'),
            on_partial=failed_callback,
            on_final=lambda response: pytest.fail('No final expected'),
        )


def test_unverified_transport_fails_visibly_without_fallback(adapter, monkeypatch):
    monkeypatch.setattr(
        'src.clinic.live_asr_adapter.httpx.Client',
        lambda *a, **kw: pytest.fail('No unverified transport or batch fallback'),
    )
    request = TranscriptionRequest(io.BytesIO(b'audio'), 'sample.wav')
    with pytest.raises(TranscriptionError, match='CAPABILITY_UNAVAILABLE'):
        adapter.stream_transcription(
            request,
            on_partial=lambda response: pytest.fail('No partial expected'),
            on_final=lambda response: pytest.fail('No final expected'),
        )
    assert request.audio_file.tell() == 0

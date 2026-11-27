"""Live ASR callback order and the local Speaches realtime socket transport.

The in-process fake speaks the WebSocket handshake on 127.0.0.1. It is not a
deployed Speaches service; BT-LIVE-001 stays not_run.
"""

import base64
import io
import json
import socket
import threading
from urllib.parse import unquote

import pytest
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, CloseConnection, Request, TextMessage

from src.clinic.live_asr_adapter import LiveAsrAdapter
from src.clinic.live_asr_transport import iter_speaches_events
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


_MODEL = "Systran/faster-whisper-small"


class _GatedAudio:
    """Returns `first`, then blocks in read() until allow_rest is set."""

    def __init__(self, first: bytes, rest: bytes):
        self._buf = first + rest
        self._gate = len(first)
        self._pos = 0
        self.allow_rest = threading.Event()

    def read(self, _size: int = -1) -> bytes:
        if self._pos >= len(self._buf):
            return b""
        if self._pos >= self._gate:
            if not self.allow_rest.wait(3):
                raise TimeoutError("gated audio was not released")
            take = len(self._buf) - self._pos
        else:
            take = min(_size if _size > 0 else self._gate, self._gate - self._pos)
        blob = self._buf[self._pos:self._pos + take]
        self._pos += take
        return blob

    @property
    def position(self) -> int:
        return self._pos


class _FakeSpeaches:
    """One-shot local peer. mode is ok, batch, respond, generative, or silent."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.audio = bytearray()
        self.target = ""
        self.headers: list[tuple] = []
        self.session = None
        self.release = threading.Event()
        self._listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listen.bind(("127.0.0.1", 0))
        self._listen.listen(1)
        self._listen.settimeout(5)
        self.port = self._listen.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/live/asr"

    def close(self) -> None:
        self.release.set()
        try:
            self._listen.close()
        except OSError:
            pass
        self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            conn, _addr = self._listen.accept()
        except OSError:
            return
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(5)
            if self.mode == "batch":
                self._batch(conn)
            else:
                self._realtime(conn)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _batch(self, conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        self.target = data.decode("latin1", errors="replace")
        body = b"batch-only"
        head = (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n\r\n"
        )
        conn.sendall(head + body)

    def _realtime(self, conn: socket.socket) -> None:
        ws = WSConnection(ConnectionType.SERVER)
        fragments: list[str] = []
        accepted = False
        partial_sent = False
        while True:
            data = conn.recv(65536)
            if not data:
                return
            ws.receive_data(data)
            for event in list(ws.events()):
                if isinstance(event, Request):
                    self.target = event.target
                    self.headers = list(event.extra_headers)
                    conn.sendall(ws.send(AcceptConnection()))
                    accepted = True
                    continue
                if not accepted or not isinstance(event, TextMessage):
                    if isinstance(event, CloseConnection):
                        conn.sendall(ws.send(event.response()))
                        return
                    continue
                fragments.append(event.data)
                if not event.message_finished:
                    continue
                payload = json.loads("".join(fragments))
                fragments.clear()
                kind = payload.get("type")
                if kind == "session.update":
                    self.session = payload
                    conn.sendall(ws.send(TextMessage(data=self._ack())))
                elif kind == "input_audio_buffer.append":
                    self.audio.extend(base64.b64decode(payload.get("audio") or ""))
                    if not partial_sent and self.mode != "silent":
                        partial_sent = True
                        if self.mode == "generative":
                            conn.sendall(ws.send(TextMessage(data=json.dumps({
                                "type": "response.text.delta",
                                "delta": "GENERATED",
                            }))))
                            return
                        conn.sendall(ws.send(TextMessage(data=json.dumps({
                            "type": "conversation.item.input_audio_transcription.delta",
                            "delta": "Guten",
                        }))))
                        if self.mode == "hold":
                            self.release.wait(3)
                elif kind == "input_audio_buffer.commit" and self.mode == "ok":
                    conn.sendall(ws.send(TextMessage(data=json.dumps({
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "Guten Tag",
                    }))))
    def _ack(self) -> str:
        session = (self.session or {}).get("session") or {}
        transcription = dict(session.get("input_audio_transcription") or {})
        turn = dict(session.get("turn_detection") or {})
        if self.mode == "respond":
            turn["create_response"] = True
        body = {
            "type": "session.updated",
            "session": {
                "type": "transcription",
                "modalities": ["text"],
                "input_audio_transcription": transcription,
                "turn_detection": turn,
            },
        }
        return json.dumps(body)


def _adapter(monkeypatch, fake: _FakeSpeaches, model: str = _MODEL) -> LiveAsrAdapter:
    monkeypatch.setenv("CLINIC_MODE", "1")
    return LiveAsrAdapter({
        "base_url": fake.base_url,
        "api_key": "local-test-key",
        "model": model,
        "timeout": 3,
    })


def test_partial_arrives_before_stream_ends_and_audio_is_delivered(monkeypatch):
    fake = _FakeSpeaches("ok")
    first = b"spoken-audio-part"
    rest = b"-and-the-rest-of-the-utterance"
    audio = _GatedAudio(first, rest)
    holder: dict = {}
    snap: dict = {}

    def on_partial(response):
        snap["text"] = response.text
        snap["position"] = audio.position
        snap["server_audio"] = bytes(fake.audio)
        snap["finished"] = holder.get("finished", False)
        audio.allow_rest.set()

    def on_final(response):
        snap["final"] = response.text

    def run():
        try:
            holder["result"] = _adapter(monkeypatch, fake).stream_transcription(
                TranscriptionRequest(audio, "sample.wav", language="de"),
                on_partial=on_partial,
                on_final=on_final,
                timeout=3,
            )
        except Exception as exc:
            holder["error"] = exc
        holder["finished"] = True

    thread = threading.Thread(target=run)
    thread.start()
    try:
        thread.join(5)
        assert holder.get("error") is None
        assert snap["finished"] is False
        assert snap["text"] == "Guten"
        assert snap["position"] == len(first)
        assert snap["server_audio"] == first
        assert bytes(fake.audio) == first + rest
        assert snap["final"] == "Guten Tag"
        assert holder["result"].text == "Guten Tag"
        assert "intent=transcription" in fake.target
        assert unquote(fake.target).find(_MODEL) != -1
        assert "language=de" in fake.target
        assert "local-test-key" not in fake.target
        assert fake.session["session"]["turn_detection"]["create_response"] is False
        assert fake.session["session"]["input_audio_transcription"]["model"] == _MODEL
        assert fake.session["session"]["input_audio_transcription"]["language"] == "de"
        joined = [value for _name, value in fake.headers]
        assert b"Bearer local-test-key" in joined
    finally:
        audio.allow_rest.set()
        fake.close()
        thread.join(2)


def test_batch_only_peer_is_rejected_without_reading_audio(monkeypatch):
    fake = _FakeSpeaches("batch")
    request = TranscriptionRequest(io.BytesIO(b"audio-bytes"), "sample.wav")
    monkeypatch.setattr(
        "src.clinic.live_asr_adapter.httpx.Client",
        lambda *a, **kw: pytest.fail("batch HTTP is not a realtime fallback"),
    )
    try:
        with pytest.raises(TranscriptionError, match="CAPABILITY_UNAVAILABLE"):
            _adapter(monkeypatch, fake).stream_transcription(
                request,
                on_partial=lambda _response: pytest.fail("no partial"),
                on_final=lambda _response: pytest.fail("no final"),
                timeout=2,
            )
        assert request.audio_file.tell() == 0
        assert "Upgrade" in fake.target or "upgrade" in fake.target.lower()
    finally:
        fake.close()


def test_response_generation_is_refused_before_audio(monkeypatch):
    fake = _FakeSpeaches("respond")
    request = TranscriptionRequest(io.BytesIO(b"audio-bytes"), "sample.wav")
    try:
        with pytest.raises(TranscriptionError, match="CAPABILITY_UNAVAILABLE"):
            _adapter(monkeypatch, fake).stream_transcription(
                request,
                on_partial=lambda _response: pytest.fail("no partial"),
                on_final=lambda _response: pytest.fail("no final"),
                timeout=2,
            )
        assert fake.audio == b""
        assert request.audio_file.tell() == 0
    finally:
        fake.close()


def test_generative_delta_is_not_returned_as_transcript(monkeypatch):
    fake = _FakeSpeaches("generative")
    seen = []
    try:
        with pytest.raises(TranscriptionError, match="CAPABILITY_UNAVAILABLE"):
            _adapter(monkeypatch, fake).stream_transcription(
                TranscriptionRequest(io.BytesIO(b"audio-bytes"), "sample.wav"),
                on_partial=lambda response: seen.append(response.text),
                on_final=lambda response: seen.append(response.text),
                timeout=2,
            )
        assert seen == []
        assert b"GENERATED" not in bytes(fake.audio)
    finally:
        fake.close()


def test_silent_peer_times_out(monkeypatch):
    fake = _FakeSpeaches("silent")
    try:
        with pytest.raises(TranscriptionError, match="timed out"):
            _adapter(monkeypatch, fake).stream_transcription(
                TranscriptionRequest(io.BytesIO(b"audio"), "sample.wav"),
                on_partial=lambda _response: None,
                on_final=lambda _response: pytest.fail("no final"),
                timeout=0.4,
            )
    finally:
        fake.close()


def test_cancel_closes_the_socket_after_a_partial(monkeypatch):
    fake = _FakeSpeaches("hold")
    partials = []
    request = TranscriptionRequest(io.BytesIO(b"audio-bytes-for-cancel"), "sample.wav")
    request.should_cancel = lambda: bool(partials)

    def on_partial(response):
        partials.append(response.text)

    try:
        with pytest.raises(TranscriptionError, match="cancelled"):
            _adapter(monkeypatch, fake).stream_transcription(
                request,
                on_partial=on_partial,
                on_final=lambda _response: pytest.fail("no final"),
                timeout=3,
            )
        assert partials == ["Guten"]
    finally:
        fake.close()


def test_public_and_placeholder_models_do_not_open_a_socket(monkeypatch):
    def refused(*_args, **_kwargs):
        pytest.fail("socket opened")

    monkeypatch.setattr(socket, "create_connection", refused)
    audio = io.BytesIO(b"audio")
    with pytest.raises(TranscriptionError, match="CAPABILITY_UNAVAILABLE"):
        list(iter_speaches_events(
            base_url="https://api.openai.com/v1",
            api_key="local-test-key",
            model=_MODEL,
            language="de",
            audio=audio,
            timeout=1,
            hotwords=[],
            should_cancel=None,
        ))
    assert audio.tell() == 0
    with pytest.raises(TranscriptionError, match="CAPABILITY_UNAVAILABLE"):
        list(iter_speaches_events(
            base_url="http://localhost/live/asr",
            api_key="",
            model="Systran/faster-distil-whisper-small.en",
            language="en",
            audio=audio,
            timeout=1,
            hotwords=[],
            should_cancel=None,
        ))
    assert audio.tell() == 0

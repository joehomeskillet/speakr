"""Local Speaches ``/v1/realtime`` client.

wsproto frames the bytes. This module owns the TCP socket. The peer must be a
local host. Response generation stays off (``intent=transcription`` and
``turn_detection.create_response=false``). There is no cloud client and no
batch HTTP fallback.

Cancellation closes this socket. Speaches' pinned realtime API has no truncate
call this connector can rely on; the caller opens a new session for the next
generation.
"""

from __future__ import annotations

import base64
import json
import queue
import socket
import ssl
import threading
import time
from typing import BinaryIO, Callable, Iterator
from urllib.parse import quote, urlencode, urlsplit

from wsproto import ConnectionType, WSConnection
from wsproto.events import (
    AcceptConnection,
    CloseConnection,
    Ping,
    Pong,
    RejectConnection,
    Request,
    TextMessage,
)
from wsproto.utilities import LocalProtocolError, RemoteProtocolError

from src.services.transcription.exceptions import TranscriptionError
from src.services.transcription.local_only import is_local_base_url

_CHUNK_BYTES = 4800
_ENGLISH_API_DEFAULT = "systran/faster-distil-whisper-small.en"
_REJECTED_MODELS = {"", "tts", _ENGLISH_API_DEFAULT}
_GENERATIVE_TYPES = (
    "response.created",
    "response.done",
    "response.text.",
    "response.audio.",
    "response.output_text.",
    "response.output_audio.",
    "response.audio_transcript.",
    "response.content_part.",
)
_PARTIAL_TYPES = {
    "conversation.item.input_audio_transcription.delta",
    "input_audio_transcription.delta",
}
_FINAL_TYPES = {
    "conversation.item.input_audio_transcription.completed",
    "input_audio_transcription.completed",
}


def iter_speaches_events(
    *,
    base_url: str,
    api_key: str,
    model: str,
    language: str,
    audio: BinaryIO | None,
    timeout: float,
    hotwords: list[str] | None,
    should_cancel: Callable[[], bool] | None,
    manual_turn: bool = False,
) -> Iterator[dict]:
    """Yield partial and final events as the peer sends them.

    Audio is read on a side thread so a blocking capture read does not stall
    event delivery. The first partial is yielded before this generator returns.
    """
    chosen = _explicit_model(model)
    chosen_language = language or "de"
    scheme, host, port, target, header_host = _endpoint(base_url, chosen, chosen_language)
    deadline = time.monotonic() + max(timeout, 0.1)
    try:
        sock = _connect(scheme, host, port, deadline)
    except OSError as exc:
        raise TranscriptionError(f"Live ASR streaming failed: {exc}") from exc
    stop = threading.Event()
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ws = WSConnection(ConnectionType.CLIENT)
        headers = []
        if api_key:
            token = api_key.encode("ascii", errors="strict")
            headers.append((b"authorization", b"Bearer " + token))
        _send_all(
            sock,
            ws.send(Request(host=header_host, target=target, extra_headers=headers)),
            deadline,
        )
        _await_accept(sock, ws, deadline)
        _send_all(
            sock,
            ws.send(TextMessage(
                data=_session_update(chosen, chosen_language, hotwords or [], manual_turn),
            )),
            deadline,
        )
        _await_session(sock, ws, deadline, chosen, chosen_language, manual_turn)
        yield from _exchange(
            sock, ws, audio, deadline, should_cancel, stop, manual_turn,
        )
    except TranscriptionError:
        raise
    except socket.timeout as exc:
        raise TranscriptionError("Live ASR streaming timed out") from exc
    except OSError as exc:
        raise TranscriptionError(f"Live ASR streaming failed: {exc}") from exc
    except (LocalProtocolError, RemoteProtocolError) as exc:
        raise TranscriptionError(f"Live ASR streaming failed: {exc}") from exc
    finally:
        stop.set()
        sock.close()


def _explicit_model(model: str) -> str:
    name = (model or "").strip()
    if name.lower() in _REJECTED_MODELS:
        raise TranscriptionError(
            "CAPABILITY_UNAVAILABLE: explicit local recognition model is required"
        )
    return name


def _endpoint(
    base_url: str, model: str, language: str,
) -> tuple[str, str, int, str, str]:
    parts = urlsplit((base_url or "").strip())
    scheme = parts.scheme.lower()
    if scheme in ("ws", "wss"):
        scheme = "http" if scheme == "ws" else "https"
    if scheme not in ("http", "https"):
        raise TranscriptionError(
            "CAPABILITY_UNAVAILABLE: live ASR URL scheme is not local http(s)"
        )
    host = parts.hostname
    if not host:
        raise TranscriptionError(
            "CAPABILITY_UNAVAILABLE: live ASR URL has no host"
        )
    port = parts.port or (443 if scheme == "https" else 80)
    literal = f"[{host}]" if ":" in host else host
    check = f"{scheme}://{literal}:{port}/v1/realtime"
    if not is_local_base_url(check):
        raise TranscriptionError(
            "CAPABILITY_UNAVAILABLE: live ASR refuses a non-local endpoint"
        )
    query = urlencode(
        {"intent": "transcription", "model": model, "language": language},
        quote_via=quote,
    )
    default_port = 443 if scheme == "https" else 80
    header_host = literal if port == default_port else f"{literal}:{port}"
    return scheme, host, port, f"/v1/realtime?{query}", header_host


def _connect(scheme: str, host: str, port: int, deadline: float) -> socket.socket:
    remaining = max(0.1, deadline - time.monotonic())
    raw = socket.create_connection((host, port), remaining)
    if scheme != "https":
        return raw
    context = ssl.create_default_context()
    try:
        return context.wrap_socket(raw, server_hostname=host)
    except OSError:
        raw.close()
        raise


def _session_update(model: str, language: str, hotwords: list[str], manual_turn: bool) -> str:
    transcription: dict = {"model": model, "language": language}
    if hotwords:
        transcription["prompt"] = "Keywords: " + ", ".join(hotwords)
    body = {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "modalities": ["text"],
            "input_audio_format": "pcm16",
            "input_audio_transcription": transcription,
            "turn_detection": None if manual_turn else {
                "type": "server_vad",
                "create_response": False,
            },
        },
    }
    return json.dumps(body, separators=(",", ":"))


def _send_all(sock: socket.socket, data: bytes, deadline: float) -> None:
    view = memoryview(data)
    while view:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TranscriptionError("Live ASR streaming timed out")
        sock.settimeout(remaining)
        try:
            sent = sock.send(view)
        except socket.timeout as exc:
            raise TranscriptionError("Live ASR streaming timed out") from exc
        if sent <= 0:
            raise TranscriptionError("Live ASR streaming failed: send closed")
        view = view[sent:]


def _await_accept(sock: socket.socket, ws: WSConnection, deadline: float) -> None:
    while time.monotonic() < deadline:
        data = _recv_blocking(sock, deadline)
        try:
            ws.receive_data(data)
            events = list(ws.events())
        except (LocalProtocolError, RemoteProtocolError) as exc:
            raise TranscriptionError(
                "CAPABILITY_UNAVAILABLE: peer did not accept a realtime upgrade"
            ) from exc
        for event in events:
            if isinstance(event, AcceptConnection):
                return
            if isinstance(event, RejectConnection):
                raise TranscriptionError(
                    "CAPABILITY_UNAVAILABLE: realtime upgrade rejected "
                    f"({getattr(event, 'status_code', 'unknown')})"
                )
    raise TranscriptionError(
        "CAPABILITY_UNAVAILABLE: realtime handshake timed out"
    )


def _await_session(
    sock: socket.socket,
    ws: WSConnection,
    deadline: float,
    model: str,
    language: str,
    manual_turn: bool,
) -> None:
    fragments: list[str] = []
    while time.monotonic() < deadline:
        data = _recv_blocking(sock, deadline)
        ws.receive_data(data)
        for event in ws.events():
            if isinstance(event, Ping):
                _send_all(sock, ws.send(Pong(payload=event.payload)), deadline)
                continue
            if not isinstance(event, TextMessage):
                continue
            fragments.append(event.data)
            if not event.message_finished:
                continue
            raw = "".join(fragments)
            fragments.clear()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise TranscriptionError(
                    f"Live ASR streaming failed: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "error":
                raise TranscriptionError(
                    "CAPABILITY_UNAVAILABLE: " + _error_text(payload)
                )
            verdict = _ack_ok(payload, model, language, manual_turn)
            if verdict is True:
                return
            if verdict is False:
                raise TranscriptionError(
                    "CAPABILITY_UNAVAILABLE: realtime session is not transcription-only"
                )
    raise TranscriptionError(
        "CAPABILITY_UNAVAILABLE: realtime session was not acknowledged"
    )


def _ack_ok(payload: dict, model: str, language: str, manual_turn: bool) -> bool | None:
    if payload.get("type") != "session.updated":
        return None
    session = payload.get("session") or {}
    if not isinstance(session, dict):
        return False
    transcription = session.get("input_audio_transcription") or {}
    if not isinstance(transcription, dict) or transcription.get("model") != model:
        return False
    echoed = transcription.get("language")
    if echoed not in (None, language):
        return False
    turn = session.get("turn_detection")
    if manual_turn and ("turn_detection" not in session or turn is not None):
        return False
    if isinstance(turn, dict) and turn.get("create_response") is True:
        return False
    modalities = session.get("modalities")
    if session.get("type") == "transcription":
        return True
    if isinstance(turn, dict) and turn.get("create_response") is False:
        return True
    if modalities == ["text"]:
        return True
    return False


def _exchange(
    sock: socket.socket,
    ws: WSConnection,
    audio: BinaryIO | None,
    deadline: float,
    should_cancel: Callable[[], bool] | None,
    stop: threading.Event,
    manual_turn: bool,
) -> Iterator[dict]:
    sock.setblocking(False)
    pending = bytearray()
    incoming: queue.Queue = queue.Queue(maxsize=4)
    producer = threading.Thread(
        target=_produce_audio,
        args=(audio, incoming, stop),
        daemon=True,
        name="live-asr-audio",
    )
    producer.start()
    fragments: list[str] = []
    seq = -1
    commit_sent = False
    try:
        while True:
            if should_cancel is not None and should_cancel():
                _queue_close(ws, pending, 1000, "cancelled")
                _flush(sock, pending, deadline)
                raise TranscriptionError("Live ASR streaming cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TranscriptionError("Live ASR streaming timed out")
            if not commit_sent and not pending:
                item = _next_audio(incoming)
                if isinstance(item, BaseException):
                    raise TranscriptionError(
                        f"Live ASR streaming failed: {item}"
                    ) from item
                if item is None:
                    pending.extend(ws.send(TextMessage(
                        data='{"type":"input_audio_buffer.commit"}',
                    )))
                    commit_sent = True
                elif item is not _WAITING:
                    pending.extend(ws.send(TextMessage(data=_append(item))))
            _flush(sock, pending, deadline)
            wait = remaining if commit_sent and not pending and should_cancel is None else min(0.05, remaining)
            readable, _, _ = _select_read(sock, wait)
            if not readable:
                continue
            data = _recv_nonblocking(sock)
            if data is None:
                continue
            if data == b"":
                raise TranscriptionError(
                    "Live ASR streaming failed: connection closed"
                )
            ws.receive_data(data)
            for kind, text in _messages(ws, fragments, pending):
                if kind == "generative":
                    _queue_close(ws, pending, 1000, "refused")
                    _flush(sock, pending, deadline)
                    raise TranscriptionError(
                        "CAPABILITY_UNAVAILABLE: generative realtime response refused"
                    )
                if kind == "error":
                    raise TranscriptionError("CAPABILITY_UNAVAILABLE: " + text)
                if manual_turn and kind == "final" and not commit_sent:
                    raise TranscriptionError("CAPABILITY_UNAVAILABLE: realtime final preceded the audio commit")
                seq += 1
                yield {"event": kind, "seq": seq, "text": text}
                if kind == "final":
                    _queue_close(ws, pending, 1000, "done")
                    _flush(sock, pending, deadline)
                    return
    finally:
        stop.set()
        _unblock_producer(incoming)
        producer.join(timeout=0.2)


_WAITING = object()


def _produce_audio(
    audio: BinaryIO | None,
    incoming: queue.Queue,
    stop: threading.Event,
) -> None:
    try:
        if audio is not None:
            while not stop.is_set():
                blob = audio.read(_CHUNK_BYTES)
                if not blob:
                    break
                if isinstance(blob, str):
                    blob = blob.encode("utf-8")
                incoming.put(blob)
        if not stop.is_set():
            incoming.put(None)
    except Exception as exc:
        incoming.put(exc)


def _next_audio(incoming: queue.Queue):
    try:
        return incoming.get_nowait()
    except queue.Empty:
        return _WAITING


def _unblock_producer(incoming: queue.Queue) -> None:
    try:
        incoming.get_nowait()
    except queue.Empty:
        pass


def _append(blob: bytes) -> str:
    encoded = base64.b64encode(blob).decode("ascii")
    return json.dumps(
        {"type": "input_audio_buffer.append", "audio": encoded},
        separators=(",", ":"),
    )


def _messages(ws, fragments, pending):
    for event in ws.events():
        if isinstance(event, Ping):
            pending.extend(ws.send(Pong(payload=event.payload)))
            continue
        if isinstance(event, CloseConnection):
            pending.extend(ws.send(event.response()))
            raise TranscriptionError("Live ASR streaming failed: peer closed the session")
        if not isinstance(event, TextMessage):
            continue
        fragments.append(event.data)
        if not event.message_finished:
            continue
        raw = "".join(fragments)
        fragments.clear()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TranscriptionError(f"Live ASR streaming failed: {exc}") from exc
        if not isinstance(payload, dict):
            continue
        event_type = payload.get("type")
        kind = event_type if isinstance(event_type, str) else ""
        if kind in _PARTIAL_TYPES:
            text = payload.get("delta") or ""
            if isinstance(text, str) and text:
                yield "partial", text
            continue
        if kind in _FINAL_TYPES:
            text = payload.get("transcript")
            if not isinstance(text, str):
                text = payload.get("text") if isinstance(payload.get("text"), str) else ""
            yield "final", text
            continue
        if kind == "error":
            yield "error", _error_text(payload)
            continue
        if kind.startswith(_GENERATIVE_TYPES) or kind in _GENERATIVE_TYPES:
            yield "generative", ""


def _error_text(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        message = err.get("message") or err.get("code") or "realtime error"
        return str(message)
    if err:
        return str(err)
    return "realtime error"


def _queue_close(ws: WSConnection, pending: bytearray, code: int, reason: str) -> None:
    pending.extend(ws.send(CloseConnection(code=code, reason=reason)))


def _flush(sock: socket.socket, pending: bytearray, deadline: float) -> None:
    while pending and time.monotonic() < deadline:
        try:
            sent = sock.send(pending)
        except BlockingIOError:
            return
        except InterruptedError:
            continue
        if sent <= 0:
            raise TranscriptionError("Live ASR streaming failed: send closed")
        del pending[:sent]


def _select_read(sock: socket.socket, wait: float) -> tuple[list, list, list]:
    import select
    try:
        return select.select([sock], [], [], max(wait, 0.0))
    except (InterruptedError, ValueError):
        return [], [], []


def _recv_blocking(sock: socket.socket, deadline: float) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TranscriptionError("Live ASR streaming timed out")
    sock.settimeout(remaining)
    try:
        data = sock.recv(65536)
    except socket.timeout as exc:
        raise TranscriptionError("Live ASR streaming timed out") from exc
    if not data:
        raise TranscriptionError(
            "CAPABILITY_UNAVAILABLE: realtime endpoint closed the connection"
        )
    return data


def _recv_nonblocking(sock: socket.socket) -> bytes | None:
    try:
        return sock.recv(65536)
    except BlockingIOError:
        return None
    except InterruptedError:
        return None

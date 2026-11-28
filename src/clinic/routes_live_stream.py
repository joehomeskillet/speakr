"""One WebSocket per browser ASR turn, on the clinic Gunicorn WSGI path.

Offer ``clinic-live-v1`` and the Flask CSRF token as subprotocols. After the
upgrade, send {client_id, generation, after_sequence}, wait for ``ready``,
send mono PCM16LE frames at 24 kHz and end with the text message ``finish``.
Only ``server_saved`` advances the checkpoint. After a lost reply, use resume
and its stored events before opening another turn.
"""

from __future__ import annotations

import binascii
import json
import socket
import wave
from base64 import b64decode
from collections import deque
from contextlib import suppress
from io import BytesIO
from select import select
from threading import Event, Lock
from time import monotonic

from flask import Response, current_app, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.wsgi import ClosingIterator
from wsproto import Connection, ConnectionType
from wsproto.events import BytesMessage, CloseConnection, Ping, TextMessage
from wsproto.utilities import LocalProtocolError, generate_accept_token

from src.clinic import audio_revisions, live_phrases, live_session, revisions
from src.clinic.live_commit import commit_asr_phrase
from src.clinic.models import ClinicDictation
from src.clinic.parity_dispatch import CommandProblem, _problem
from src.clinic.routes_live import _code, _valid_fence
from src.database import db
from src.models import User
from src.services.transcription.base import TranscriptionRequest
from src.services.transcription.exceptions import ConfigurationError, TranscriptionError

PROTOCOL = "clinic-live-v1"
RATE = 24_000
FRAME_BYTES = RATE * 2 // 10
MAX_AUDIO_SECONDS = 30
TURN_TIMEOUT = 45


def stream_csrf() -> str | None:
    protocols = request.headers.get("Sec-WebSocket-Protocol", "").split(",")
    if len(protocols) == 2 and protocols[0].strip() == PROTOCOL:
        return protocols[1].strip()
    return None


class PeerClosed(Exception):
    """The browser has disconnected or its socket can no longer be used."""


class UpgradeResponse(Response):
    def get_app_iter(self, environ):
        # Werkzeug normally suppresses 1xx iterators. This one runs the upgraded socket.
        return ClosingIterator(self.response, self.close)


class BrowserTurn:
    """Bounded audio source read by the adapter's producer thread."""

    def __init__(self, sock):
        self.sock = sock
        self.ws = Connection(ConnectionType.SERVER)
        self.lock = Lock()
        self.pending = deque()
        self.deadline = monotonic() + 5
        self.closed = Event()
        self.audio = bytearray()
        self.finished = False
        self.failure = None

    def send(self, event):
        try:
            with self.lock:
                self.sock.sendall(self.ws.send(event))
        except (LocalProtocolError, OSError) as exc:
            raise PeerClosed from exc

    def emit(self, status, **data):
        self.send(TextMessage(data=json.dumps(dict(status=status, **data))))

    def receive(self, wait):
        try:
            if not select([self.sock], [], [], wait)[0]:
                return
            data = self.sock.recv(FRAME_BYTES)
        except OSError as exc:
            raise PeerClosed from exc
        if not data:
            raise PeerClosed
        with self.lock:
            self.ws.receive_data(data)
            self.pending.extend(self.ws.events())

    def next_message(self):
        parts = []
        size = 0
        while not self.closed.is_set():
            remaining = self.deadline - monotonic()
            if remaining <= 0:
                raise CommandProblem("DEPENDENCY_UNAVAILABLE")
            if not self.pending:
                self.receive(min(0.1, remaining))
                continue
            event = self.pending.popleft()
            if isinstance(event, CloseConnection):
                raise PeerClosed
            if isinstance(event, Ping):
                self.send(event.response())
            if isinstance(event, (BytesMessage, TextMessage)):
                size += len(event.data)
                if size > FRAME_BYTES:
                    raise CommandProblem("INVALID_PAYLOAD")
                parts.append(event.data)
                if event.message_finished:
                    return (b"" if isinstance(event, BytesMessage) else "").join(parts)
        raise PeerClosed

    def start_capture(self, check, position):
        self.deadline = monotonic() + TURN_TIMEOUT
        self.check = check
        self.position = position

    def read(self, _size):
        try:
            if not self.audio:
                self.check()
                self.emit("ready", **self.position, sample_rate=RATE, max_frame_bytes=FRAME_BYTES,
                          max_audio_seconds=MAX_AUDIO_SECONDS)
            chunk = self.next_message()
            if chunk == "finish" and len(self.audio) >= FRAME_BYTES:
                self.finished = True
                return b""
            if (not isinstance(chunk, bytes) or not chunk or len(chunk) % 2
                    or len(self.audio) + len(chunk) > MAX_AUDIO_SECONDS * RATE * 2):
                raise CommandProblem("INVALID_PAYLOAD")
            self.check()
            self.audio.extend(chunk)
            return chunk
        except (CommandProblem, PeerClosed, live_session.LiveSessionError, SQLAlchemyError) as exc:
            self.failure = exc
            raise

    def cancelled(self):
        # After EOF the adapter no longer reads the browser. Check for disconnects while it finalizes.
        if self.failure is not None:
            return True
        if self.finished:
            try:
                self.receive(0)
                while self.pending:
                    event = self.pending.popleft()
                    if isinstance(event, CloseConnection):
                        raise PeerClosed
                    if isinstance(event, Ping):
                        self.send(event.response())
                    if isinstance(event, (BytesMessage, TextMessage)):
                        raise CommandProblem("INVALID_PAYLOAD")
            except (PeerClosed, CommandProblem) as exc:
                self.failure = exc
                return True
        return False

    def close(self):
        self.closed.set()
        with suppress(PeerClosed):
            self.send(CloseConnection(code=1000))
        with suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)


def register_stream_route(blueprint, authorize_dictation, store, adapter_for, now):
    @blueprint.get("/sessions/<session_id>/stream", websocket=True)
    def stream_audio(session_id):
        if request.headers.get("Origin") != request.host_url.rstrip("/"):
            return _problem("FORBIDDEN")
        if (request.method != "GET" or request.query_string or request.content_length not in (None, 0)
                or request.headers.get("Transfer-Encoding") or request.headers.get("Sec-WebSocket-Version") != "13"):
            return _problem("INVALID_PAYLOAD")
        try:
            key = request.headers.get("Sec-WebSocket-Key", "").encode("ascii")
            if len(b64decode(key, validate=True)) != 16:
                return _problem("INVALID_PAYLOAD")
        except (ValueError, binascii.Error):
            return _problem("INVALID_PAYLOAD")

        actor, login_id = current_user.id, current_user.get_id()
        row = db.session.get(live_session.ClinicLiveSession, session_id)
        if row is None or row.author_user_id != actor or not authorize_dictation(actor, row.dictation_id):
            return _problem("POLICY_DENIED")
        sock = request.environ.get("gunicorn.socket")
        if sock is None:
            return _problem("CAPABILITY_UNAVAILABLE")
        app = current_app._get_current_object()

        def run():
            # Gunicorn sends the 101 headers on the first WSGI yield and owns closing the fd.
            yield b""
            sock.settimeout(3)
            turn = BrowserTurn(sock)
            position = {"session_id": session_id}
            try:
                begin = turn.next_message()
                data = json.loads(begin) if isinstance(begin, str) else None
                if (not isinstance(data, dict) or data.keys() != {"client_id", "generation", "after_sequence"}
                        or not _valid_fence(data) or type(data["after_sequence"]) is not int
                        or data["after_sequence"] < 0):
                    raise CommandProblem("INVALID_PAYLOAD")
                client_id, generation = data["client_id"], data["generation"]
                after = data["after_sequence"]
                position.update(generation=generation, sequence=after + 1)

                def active():
                    row = live_session._holder_session(session_id, actor, now(), client_id, generation)
                    user = db.session.get(User, actor)
                    if (row.holder_generation is None or not row.client_id or user is None
                            or not user.is_active or user.get_id() != login_id
                            or not authorize_dictation(actor, row.dictation_id)):
                        raise CommandProblem("POLICY_DENIED")
                    if row.state != live_session.RECORDING or row.ack_seq != after:
                        raise CommandProblem("REVISION_CONFLICT")
                    return row

                with app.app_context():
                    row = active()
                    activity = row.last_activity_at
                    dictation = db.session.get(ClinicDictation, row.dictation_id)
                    audio_head = audio_revisions.current_audio_revision(dictation)
                    text_head = revisions.current_revision(dictation)
                    expected_audio = audio_head.id if audio_head else None
                    expected_text = text_head.id if text_head else None

                def unchanged():
                    row = active()
                    if row.last_activity_at != activity:
                        raise CommandProblem("REVISION_CONFLICT")
                    return row

                def check():
                    with app.app_context():
                        unchanged()

                def partial(result):
                    try:
                        check()
                        turn.emit("partial", **position, text=result.text)
                    except (CommandProblem, PeerClosed, live_session.LiveSessionError, SQLAlchemyError) as exc:
                        turn.failure = exc
                        raise

                adapter = adapter_for()
                turn.start_capture(check, position)
                transcription = TranscriptionRequest(
                    audio_file=turn, filename="phrase.pcm", extra_options={"manual_turn": True},
                    should_cancel=turn.cancelled)
                final = adapter.stream_transcription(transcription, on_partial=partial,
                                                     on_final=lambda _result: None, timeout=TURN_TIMEOUT)
                if turn.cancelled():
                    raise turn.failure
                if not turn.finished or not final.text.strip():
                    raise CommandProblem("DEPENDENCY_UNAVAILABLE")
                pcm = BytesIO()
                with wave.open(pcm, "wb") as wav:
                    wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                    wav.writeframes(turn.audio)

                with app.app_context():
                    row = unchanged()
                    if turn.cancelled():
                        raise turn.failure
                    start = row.audio_offset_ms
                    end = start + len(turn.audio) * 1000 // (2 * RATE)
                    receipt = commit_asr_phrase(
                        store, dictation_id=row.dictation_id, session_id=session_id, user_id=actor,
                        client_id=client_id, now=now(), generation=generation, sequence=after + 1,
                        event_id=f"asr:{generation}:{after + 1}", audio_start_ms=start, audio_end_ms=end,
                        audio=pcm.getvalue(), expected_audio_revision_id=expected_audio,
                        expected_text_revision_id=expected_text, text=final.text, model_ref=final.model)
                turn.emit(receipt.status, session_id=receipt.session_id, generation=receipt.generation,
                          sequence=receipt.sequence, event_id=receipt.event_id, phrase_id=receipt.phrase_id,
                          audio_start_ms=start, audio_end_ms=end, text=revisions.normalise_text(final.text),
                          audio_revision_id=receipt.audio_revision_id,
                          transcript_revision_id=receipt.transcript_revision_id)
            except (CommandProblem, live_session.LiveSessionError, live_phrases.PhraseError,
                    TranscriptionError, SQLAlchemyError, OSError, ValueError, RecursionError, PeerClosed) as exc:
                error = turn.failure or exc
                if not isinstance(error, PeerClosed):
                    with suppress(PeerClosed):
                        turn.emit("error", **position, code=_stream_error(error))
            finally:
                turn.close()

        return UpgradeResponse(run(), status=101, headers={
            "Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Protocol": PROTOCOL,
            "Sec-WebSocket-Accept": generate_accept_token(key).decode("ascii"),
        })


def _stream_error(error):
    if isinstance(error, CommandProblem):
        return error.args[0]
    if isinstance(error, ConfigurationError) or (
        isinstance(error, TranscriptionError) and str(error).startswith("CAPABILITY_UNAVAILABLE:")
    ):
        return "CAPABILITY_UNAVAILABLE"
    if isinstance(error, (TranscriptionError, SQLAlchemyError)):
        return "DEPENDENCY_UNAVAILABLE"
    return _code(error)

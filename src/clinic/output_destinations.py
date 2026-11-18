"""Approved export destinations: alias, rights, contained name, collision policy.

A destination is a server-approved alias. The client never supplies a filesystem
path. The file name comes from ``expand_filename`` (positional ``%A`` / ``%T``
and the existing sanitiser). Writes use ``O_CREAT|O_EXCL|O_NOFOLLOW``, so two
same-name exports cannot clobber each other. The caller picks one policy:

* ``fail`` — the second export fails and is recorded
* ``version`` — the next free ``name_vN`` is created
* ``unique_name`` — a short unique suffix is added when the plain name is taken

Every attempt, including a refusal, is an auditable receipt. A receipt never
claims the file was saved on the client workstation. Browser pickers and the
original behaviour test BT-EXP-006 stay outside this module.

Wiring into routes and the output-receipt table is a later package. This module
does not create the alias directory and does not follow a file-name symlink.
"""

from __future__ import annotations

import errno
import os
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath

from src.clinic.output import OutputNameContext, OutputNameError, expand_filename

POLICY_FAIL = "fail"
POLICY_VERSION = "version"
POLICY_UNIQUE = "unique_name"
POLICIES = frozenset({POLICY_FAIL, POLICY_VERSION, POLICY_UNIQUE})

KIND_WORKFLOW = "workflow"
KIND_NAMED = "named"
KINDS = frozenset({KIND_WORKFLOW, KIND_NAMED})

OUTCOME_CREATED = "created"
OUTCOME_COLLISION = "collision"
OUTCOME_WRITE_ERROR = "write_error"
OUTCOME_REJECTED = "rejected"

_MAX_VERSION = 20
_UNIQUE_TRIES = 5
_TOKEN = re.compile(r"[A-Za-z0-9]{4,32}")


class DestinationError(ValueError):
    """The destination was refused before or during placement."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class ApprovedAlias:
    """One server-configured destination. ``root`` is an absolute directory."""

    name: str
    root: str
    actions: frozenset[str]
    profiles: frozenset[str]
    version: int
    kind: str
    label: str


@dataclass(frozen=True)
class PlaceRequest:
    """A placement. ``client_path`` must stay ``None``; any value is refused."""

    alias: str
    alias_version: int
    action: str
    profile: str
    context: OutputNameContext
    policy: str
    content: bytes
    actor_user_id: int
    pattern: str | None = None
    manual_name: str | None = None
    extension: str | None = None
    client_path: str | None = None


@dataclass(frozen=True)
class ResolvedDestination:
    """The name that would be used. Not a write and not a client save."""

    alias: str
    kind: str
    label: str
    version: int
    filename: str
    relative_path: str
    local_save_proven: bool


@dataclass(frozen=True)
class DestinationReceipt:
    """One attempt. ``local_save_proven`` is always false."""

    alias: str | None
    alias_version: int | None
    kind: str | None
    label: str | None
    action: str | None
    profile: str | None
    actor_user_id: int | None
    policy: str | None
    requested_filename: str | None
    filename: str | None
    relative_path: str | None
    outcome: str
    reason: str
    sha256: str | None
    size: int
    local_save_proven: bool
    at: datetime | None


def resolve_destination(
    catalog: Mapping[str, ApprovedAlias], request: PlaceRequest
) -> ResolvedDestination:
    """Check rights and return the contained file name. Does not create a file."""
    alias = _authorize(catalog, request)
    filename = _filename(request)
    _contained(Path(alias.root).resolve(strict=False), filename)
    return ResolvedDestination(
        alias=alias.name,
        kind=alias.kind,
        label=alias.label,
        version=alias.version,
        filename=filename,
        relative_path=filename,
        local_save_proven=False,
    )


def place_export(
    catalog: Mapping[str, ApprovedAlias],
    request: PlaceRequest,
    *,
    audit: list[DestinationReceipt] | None = None,
    now: datetime | None = None,
    token_factory: Callable[[], str] | None = None,
) -> DestinationReceipt:
    """Create the file under the alias, or record why it was not created."""
    if audit is not None and not isinstance(audit, list):
        raise DestinationError("bad_audit")
    try:
        when = _clock(now)
        receipt = _attempt(catalog, request, when, token_factory or _default_token)
    except DestinationError as exc:
        receipt = _rejected(request, exc.code, None if exc.code == "naive_time" else _safe_clock(now))
    if audit is not None:
        audit.append(receipt)
    return receipt


def _attempt(catalog, request, when, token_factory) -> DestinationReceipt:
    alias = _authorize(catalog, request)
    filename = _filename(request)
    try:
        root = Path(alias.root).resolve(strict=True)
    except FileNotFoundError:
        return _done(request, alias, filename, None, OUTCOME_WRITE_ERROR, "not_a_directory", when)
    if not root.is_dir():
        return _done(request, alias, filename, None, OUTCOME_WRITE_ERROR, "not_a_directory", when)
    stored, outcome, reason = _store(
        root, filename, request.extension, request.policy, request.content, token_factory
    )
    return _done(request, alias, filename, stored, outcome, reason, when, request.content)


def _store(root, filename, extension, policy, content, token_factory):
    saw_exists = False
    for name in _names(filename, extension, policy, token_factory):
        target = _contained(root, name)
        try:
            _exclusive_write(target, content)
        except FileExistsError:
            saw_exists = True
            continue
        except OSError:
            return None, OUTCOME_WRITE_ERROR, "write_failed"
        reason = "created" if name == filename else policy
        return name, OUTCOME_CREATED, reason
    if saw_exists:
        reason = "name_taken" if policy == POLICY_FAIL else "names_exhausted"
        return None, OUTCOME_COLLISION, reason
    return None, OUTCOME_WRITE_ERROR, "write_failed"


def _names(filename, extension, policy, token_factory):
    yield filename
    if policy == POLICY_VERSION:
        for number in range(2, _MAX_VERSION + 1):
            yield _with_suffix(filename, extension, f"_v{number}")
    elif policy == POLICY_UNIQUE:
        for _ in range(_UNIQUE_TRIES):
            token = token_factory()
            if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
                raise DestinationError("bad_token")
            yield _with_suffix(filename, extension, "_" + token)


def _with_suffix(filename: str, extension: str | None, suffix: str) -> str:
    if extension is None:
        return filename + suffix
    ending = "." + extension
    if filename.endswith(ending):
        return filename[: -len(ending)] + suffix + ending
    return filename + suffix


def _exclusive_write(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise FileExistsError(path) from exc
        raise
    try:
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            offset += written
        os.fsync(fd)
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    try:
        os.close(fd)
    except OSError:
        return


def _authorize(catalog: Mapping[str, ApprovedAlias], request: PlaceRequest) -> ApprovedAlias:
    if not isinstance(request, PlaceRequest):
        raise DestinationError("bad_request")
    if not isinstance(request.content, bytes):
        raise DestinationError("bad_content")
    if type(request.actor_user_id) is not int or request.actor_user_id < 1:
        raise DestinationError("bad_actor")
    if request.client_path is not None:
        raise DestinationError("client_path_rejected")
    if not isinstance(catalog, Mapping):
        raise DestinationError("bad_catalog")
    alias = catalog.get(request.alias)
    if not isinstance(alias, ApprovedAlias) or alias.name != request.alias:
        raise DestinationError("unknown_alias")
    if type(alias.version) is not int or alias.version < 1:
        raise DestinationError("bad_catalog")
    if alias.kind not in KINDS or not isinstance(alias.label, str) or alias.label.strip() == "":
        raise DestinationError("bad_catalog")
    if not isinstance(alias.root, str) or not Path(alias.root).is_absolute():
        raise DestinationError("alias_root_invalid")
    if type(request.alias_version) is not int or request.alias_version != alias.version:
        raise DestinationError("stale_version")
    if request.action not in alias.actions:
        raise DestinationError("action_denied")
    if request.profile not in alias.profiles:
        raise DestinationError("profile_denied")
    if request.policy not in POLICIES:
        raise DestinationError("unknown_policy")
    if not isinstance(request.context, OutputNameContext):
        raise DestinationError("bad_filename")
    return alias


def _filename(request: PlaceRequest) -> str:
    if (request.pattern is None) == (request.manual_name is None):
        raise DestinationError("name_required")
    source = request.pattern if request.pattern is not None else request.manual_name
    if not isinstance(source, str) or source == "":
        raise DestinationError("bad_filename")
    if _is_traversal(source):
        raise DestinationError("traversal_rejected")
    if request.pattern is None:
        source = source.replace("%", "%%")
    try:
        name = expand_filename(source, request.context, extension=request.extension)
    except OutputNameError as exc:
        raise DestinationError("bad_filename") from exc
    _single_segment(name)
    return name


def _is_traversal(value: str) -> bool:
    if "\x00" in value or "/" in value or "\\" in value:
        return True
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute():
        return True
    return any(part == ".." for part in (*posix.parts, *windows.parts))


def _single_segment(filename: str) -> None:
    if filename in {"", ".", ".."} or "/" in filename or "\\" in filename or "\x00" in filename:
        raise DestinationError("traversal_rejected")
    if Path(filename).name != filename:
        raise DestinationError("traversal_rejected")


def _contained(root: Path, filename: str) -> Path:
    _single_segment(filename)
    target = root / filename
    if target.parent != root:
        raise DestinationError("traversal_rejected")
    return target


def _done(request, alias, requested, stored, outcome, reason, when, content=b""):
    created = outcome == OUTCOME_CREATED and stored is not None
    return DestinationReceipt(
        alias=alias.name,
        alias_version=alias.version,
        kind=alias.kind,
        label=alias.label,
        action=request.action,
        profile=request.profile,
        actor_user_id=request.actor_user_id,
        policy=request.policy,
        requested_filename=requested,
        filename=stored if created else None,
        relative_path=stored if created else None,
        outcome=outcome,
        reason=reason,
        sha256=sha256(content).hexdigest() if created else None,
        size=len(content) if created else 0,
        local_save_proven=False,
        at=when,
    )


def _rejected(request, code, when) -> DestinationReceipt:
    known = isinstance(request, PlaceRequest)
    return DestinationReceipt(
        alias=request.alias if known else None,
        alias_version=request.alias_version if known and type(request.alias_version) is int else None,
        kind=None,
        label=None,
        action=request.action if known else None,
        profile=request.profile if known else None,
        actor_user_id=request.actor_user_id if known and type(request.actor_user_id) is int else None,
        policy=request.policy if known else None,
        requested_filename=None,
        filename=None,
        relative_path=None,
        outcome=OUTCOME_REJECTED,
        reason=code,
        sha256=None,
        size=0,
        local_save_proven=False,
        at=when,
    )


def _clock(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise DestinationError("naive_time")
    return now.astimezone(timezone.utc)


def _safe_clock(now: datetime | None) -> datetime | None:
    try:
        return _clock(now)
    except DestinationError:
        return None


def _default_token() -> str:
    return secrets.token_hex(4)

"""HTTP surface for directory preview and confirmed application (PAR-ADM-009).

A thin layer over ``directory_diff``. The actor is always ``current_user`` and
must be an active identity administrator with a matching clinic session; a body
never names an actor, user or role. ``preview`` reads the configured directory
snapshot and returns the changes plus a version and hash. ``apply`` rebuilds the
preview from the current snapshot and local state and refuses unless both match,
then persists only the selected changes through the existing pool and identity
services. Local account management never depends on the provider: without one,
the routes answer ``ldap_capability_unavailable``. Reading local Windows accounts
stays open (WG-ADM-009).

The application selects its provider at startup. The separate ``link-new``
confirmation creates one pending account for a selected new entry.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import directory_diff as dd
from src.clinic import identity, ldap_profile, pools
from src.clinic.config import clinic_mode_enabled
from src.clinic.directory_provider import PROVIDER_EXTENSION, SnapshotProvider
from src.clinic.directory_reconcile import Action, DirectoryReconcileError, LocalUser, normalise_directory_email
from src.clinic.entra import ClinicExternalIdentity
from src.database import db
from src.models import Group, GroupMembership, User

PREFIX = "/api/clinic/v1/directory"
# Identity and authority come from the session, never from the request.
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "role", "roles", "is_admin", "is_identity_admin",
})
_STATUS = {
    "unauthenticated": 401, "unauthorized": 403, "invalid_payload": 400,
    "stale_version": 409, "stale_state": 409, "invalid_selection": 400,
    "explicit_link_required": 422, "ldap_capability_unavailable": 503, "apply_failed": 409,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           409: "Versionskonflikt", 422: "Verknüpfung erforderlich",
           503: "Verzeichnis nicht verfügbar"}

_provider: SnapshotProvider | None = None


def set_provider(provider: SnapshotProvider | None) -> None:
    """Set the fallback for standalone blueprint consumers without app wiring."""
    global _provider
    _provider = provider


def _read_snapshot() -> dd.DirectorySnapshot | None:
    provider = current_app.extensions.get(PROVIDER_EXTENSION, _provider)
    try:
        return provider() if provider is not None else None
    except ldap_profile.LdapCapabilityError:
        return None


def _local_state(source: str) -> tuple[list[LocalUser], list[dd.LocalMembership]]:
    links = {}
    for row in ClinicExternalIdentity.query.filter_by(status="active"):
        if row.issuer == identity.DIRECTORY_ISSUER:
            if row.tenant_id == source == identity.DIRECTORY_SOURCE:
                links[row.user_id] = row.subject
        else:
            links[row.user_id] = row.object_id
    users = []
    for user, policy in (db.session.query(User, identity.ClinicAccountPolicy)
                         .join(identity.ClinicAccountPolicy, identity.ClinicAccountPolicy.user_id == User.id)
                         .order_by(User.id)):
        users.append(LocalUser(user.id, user.username, links.get(user.id),
                               user.name or user.username, policy.status == "active", email=user.email))
    members = [dd.LocalMembership(r.user_id, "author", r.pool_id)
               for r in pools.ClinicAuthorPoolMember.query.all()]
    members += [dd.LocalMembership(r.user_id, "staff", sp.id)
                for r, sp in (db.session.query(GroupMembership, pools.ClinicStaffPool)
                              .join(pools.ClinicStaffPool, pools.ClinicStaffPool.group_id == GroupMembership.group_id))]
    return users, members


def _managed(body: dict) -> list[tuple[str, str]]:
    raw = body.get("managed_pools", [])
    if not isinstance(raw, list) or not all(
            isinstance(p, dict) and isinstance(p.get("kind"), str) and isinstance(p.get("id"), str) for p in raw):
        raise dd.DirectoryDiffError("invalid_payload", "managed_pools")
    return [(p["kind"], p["id"]) for p in raw]


def _action_json(a: Action) -> dict[str, Any]:
    return {"kind": a.kind, "object_id": a.object_id, "local_user_id": a.local_user_id, "detail": a.detail}


def _change_json(c: dd.MembershipChange) -> dict[str, Any]:
    return {"kind": c.kind, "subject_id": c.subject_id, "user_id": c.user_id,
            "pool_kind": c.pool_kind, "pool_id": c.pool_id}


def preview_hash(preview: dd.DirectoryPreview) -> str:
    """Digest over everything the application depends on, source and local state alike."""
    canonical = json.dumps({
        "source": preview.snapshot.source, "version": preview.source_version,
        "actions": [_action_json(a) for a in preview.actions],
        "new_entries": [[e.object_id, e.account, e.display_name, e.email, e.enabled]
                        for e in preview.differences.new],
        "changes": [_change_json(c) for c in preview.membership_changes],
        "users": [[u.id, u.username, u.external_id, u.display_name, u.active, u.email] for u in preview.local_users],
        "members": sorted([m.user_id, m.pool_kind, m.pool_id] for m in preview.local_memberships),
        "managed": sorted(preview.managed_pools),
    }, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _preview_json(preview: dd.DirectoryPreview) -> dict[str, Any]:
    users = {user.id: user for user in preview.local_users}
    entries = {entry.object_id: entry for entry in preview.snapshot.entries}
    membership_counts = Counter((row.user_id, row.pool_kind) for row in preview.local_memberships)
    actions = []
    for action in preview.actions:
        row = _action_json(action)
        entry = entries[action.object_id]
        user = users.get(action.local_user_id)
        row["account_label"] = user.username if user else entry.account
        if action.kind == "create_pending_user":
            row["after"] = entry.display_name
        elif action.kind == "update_display_name":
            row.update(before=user.display_name, after=entry.display_name)
        elif action.kind == "update_email":
            row.update(before=user.email, after=normalise_directory_email(entry.email))
        actions.append(row)
    return {
        "source": preview.snapshot.source, "version": preview.source_version, "hash": preview_hash(preview),
        "link_new_available": preview.snapshot.source == identity.DIRECTORY_SOURCE,
        "changed_fields": [{"user_id": local.id, "object_id": entry.object_id, "fields": list(fields)}
                           for local, entry, fields in preview.differences.changed],
        "actions": actions,
        "membership_changes": [dict(_change_json(c), account_label=users[c.user_id].username,
                                    current_count=membership_counts[c.user_id, c.pool_kind])
                               for c in preview.membership_changes],
        "ambiguous": len(preview.differences.ambiguous),
        "unmatchable": len(preview.differences.unmatchable),
    }


def _build(body: dict) -> tuple[dd.DirectorySnapshot, list, list, dd.DirectoryPreview]:
    snapshot = _read_snapshot()
    if snapshot is None:
        raise dd.DirectoryDiffError("ldap_capability_unavailable", "Directory unavailable")
    users, members = _local_state(snapshot.source)
    preview = dd.build_preview(snapshot, users, members, _managed(body), enabled=True)
    return snapshot, users, members, preview


def _confirmed_preview(body: dict) -> tuple[dd.DirectorySnapshot, list, list, dd.DirectoryPreview]:
    if not isinstance(body.get("version"), str) or not isinstance(body.get("hash"), str):
        raise dd.DirectoryDiffError("invalid_payload", "version and hash are required")
    snapshot, users, members, built = _build(body)
    if snapshot.version != body["version"]:
        raise dd.DirectoryDiffError("stale_version", "Directory version changed")
    if preview_hash(built) != body["hash"]:
        raise dd.DirectoryDiffError("stale_state", "Preview no longer matches current state")
    return snapshot, users, members, built


def _actor() -> identity.ClinicAccountPolicy:
    from src.api.auth import clinic_session_matches

    policy = db.session.get(identity.ClinicAccountPolicy, current_user.id)
    if (not current_user.is_admin or policy is None or not policy.is_identity_admin
            or policy.status != "active" or policy.must_change_password
            or not clinic_session_matches(policy)):
        raise dd.DirectoryDiffError("unauthorized", "Identity administration required")
    return policy


def _authorize(actor_id: int, change: Action | dd.MembershipChange) -> bool:
    """Per-object policy: administrators never deactivate or re-pool their own account."""
    if isinstance(change, Action):
        return change.kind in {"update_display_name", "update_email"} or (
            change.kind == "deactivate" and change.local_user_id != actor_id)
    return change.user_id != actor_id


def _select(rows: Any, keys: tuple[str, ...], universe: tuple) -> list:
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise dd.DirectoryDiffError("invalid_selection", "Selection must be a list of objects")
    index = {tuple(getattr(item, k) for k in keys): item for item in universe}
    picked = []
    for row in rows:
        item = index.get(tuple(row.get(k) for k in keys))
        if item is None:
            raise dd.DirectoryDiffError("invalid_selection", "Selected change is absent from preview")
        picked.append(item)
    return picked


def _persist(applied: dd.AppliedDirectoryDiff, actor: identity.ClinicAccountPolicy) -> None:
    users = {user.id: user for user in applied.local_users}
    profiles: dict[int, dict[str, str | None]] = {}
    for action in applied.actions:
        if action.kind == "update_display_name":
            profiles.setdefault(action.local_user_id, {})["name"] = users[action.local_user_id].display_name
        elif action.kind == "update_email":
            profiles.setdefault(action.local_user_id, {})["email"] = users[action.local_user_id].email
        elif action.kind == "deactivate":
            policy = db.session.get(identity.ClinicAccountPolicy, action.local_user_id, populate_existing=True)
            identity.set_status(action.local_user_id, "disabled", actor_id=actor.user_id,
                                expected_version=policy.version)
    for user_id, profile in profiles.items():
        policy = db.session.get(identity.ClinicAccountPolicy, user_id, populate_existing=True)
        identity.update_local_account(
            user_id, actor_id=actor.user_id, expected_version=policy.version, changes={"profile": profile},
            actor_security_version=actor.security_version)
    for change in applied.membership_changes:
        add, remove = ((pools.add_author_member, pools.remove_author_member) if change.pool_kind == "author"
                       else (pools.add_staff_member, pools.remove_staff_member))
        (add if change.kind == "add" else remove)(change.pool_id, change.user_id, actor_id=actor.user_id)


def _problem(code: str, status: int | None = None, **extra: Any):
    status = status or _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code, **extra)
    response.mimetype = "application/problem+json"
    return response, status


blueprint = Blueprint("clinic_directory", __name__)


@blueprint.before_request
def _guard():
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active):
        return _problem("unauthenticated")
    try:
        _actor()
    except dd.DirectoryDiffError:
        return _problem("unauthorized")
    if request.method != "GET":
        try:
            validate_csrf(request.headers.get("X-CSRF-Token"))
        except ValidationError:
            return _problem("unauthorized")


blueprint.register_error_handler(CSRFError, lambda _e: _problem("unauthorized"))
blueprint.register_error_handler(
    DirectoryReconcileError,
    lambda e: _problem(e.code if e.code in _STATUS else "invalid_payload"))


def _body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys():
        raise dd.DirectoryDiffError("invalid_payload", "Invalid body")
    return body


@blueprint.get(PREFIX + "/capability")
def capability():
    available = _read_snapshot() is not None
    options = []
    if available:
        author = pools.ClinicAuthorPool
        staff = pools.ClinicStaffPool
        options = [{"kind": "author", "id": pool_id, "name": name}
                   for pool_id, name in db.session.query(author.id, author.name).order_by(author.name)]
        options += [{"kind": "staff", "id": pool_id, "name": name}
                    for pool_id, name in db.session.query(staff.id, Group.name).join(Group).order_by(Group.name)]
    return jsonify(directory_available=available, local_accounts=True, pool_options=options,
                   local_windows_accounts="unavailable", gap="WG-ADM-009")


@blueprint.post(PREFIX + "/preview")
def preview():
    return jsonify(_preview_json(_build(_body())[3]))


@blueprint.post(PREFIX + "/apply")
def apply():
    body = _body()
    snapshot, users, members, built = _confirmed_preview(body)
    actions = _select(body.get("actions", []), ("kind", "object_id"), built.actions)
    changes = _select(body.get("memberships", []), ("kind", "subject_id", "pool_kind", "pool_id"),
                      built.membership_changes)
    if any(a.kind == "create_pending_user" for a in actions):
        raise dd.DirectoryDiffError("explicit_link_required", "Linked accounts are created explicitly")
    actor = _actor()
    applied = dd.apply_preview(built, snapshot, users, members, selected_actions=actions,
                               selected_memberships=changes, actor_id=actor.user_id,
                               authorize=_authorize, enabled=True)
    try:
        _persist(applied, actor)
    except (identity.IdentityError, pools.PoolError) as exc:
        db.session.rollback()
        raise dd.DirectoryDiffError("apply_failed", str(exc)) from exc
    return jsonify(ok=True, applied_actions=len(applied.actions), applied_memberships=len(applied.membership_changes))


@blueprint.post(PREFIX + "/link-new")
def link_new():
    body = _body()
    subject = body.get("object_id")
    if body.keys() - {"version", "hash", "object_id", "managed_pools"} or not isinstance(subject, str):
        raise dd.DirectoryDiffError("invalid_payload", "A single preview object_id is required")
    snapshot, _, _, built = _confirmed_preview(body)
    if snapshot.source != identity.DIRECTORY_SOURCE:
        raise dd.DirectoryDiffError("ldap_capability_unavailable", "Directory linking unavailable")
    entry = next((entry for entry in built.differences.new if entry.object_id == subject), None)
    if entry is None:
        raise dd.DirectoryDiffError("invalid_selection", "Selected new entry is absent from preview")

    actor = _actor()
    try:
        user = identity.create_directory_user(subject, entry.display_name, actor_id=actor.user_id,
                                              actor_security_version=actor.security_version)
    except identity.IdentityError as exc:
        db.session.rollback()
        code = "unauthorized" if isinstance(exc, identity.IdentityDenied) else "apply_failed"
        raise dd.DirectoryDiffError(code, str(exc)) from exc
    return jsonify(ok=True, user_id=user.id), 201

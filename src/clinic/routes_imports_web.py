"""Session-authenticated import centre and clarification routes."""

import json
from hashlib import sha256
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy import and_, or_
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from wtforms.validators import ValidationError

from src.clinic import (
    agent_channel, author_suggestion, decoder, firmware_catalog, import_centre, import_source_config,
    import_visibility, pools, recipient_defaults, speaker_client, speaker_pipeline, worklist_entries,
)
from src.clinic import routes_import_sources_web as source_routing
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.device_inventory_view import get_firmware_action_status
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.ingest import IngestError
from src.clinic.migration_import_intent_metadata import interpret
from src.clinic.parity_dispatch import MAX_BODY_BYTES, CommandProblem, _problem, _unique_object
from src.clinic.parity_receipts import ClinicParityReceipt, run_once, scope_key
from src.clinic.pool_policy import author_pool_ids
from src.database import db
from src.models import VoiceEmbeddingSpace
from src.services import audit
from src.services.storage.locator import local_path_from_key


blueprint = Blueprint("clinic_imports_web", __name__, url_prefix="/api/clinic/v1/imports")


blueprint.add_url_rule("/sources", view_func=source_routing.create_source, methods=["POST"])
blueprint.add_url_rule("/sources", view_func=source_routing.delete_source, methods=["DELETE"])
blueprint.add_url_rule("/sources/<config_id>", view_func=source_routing.delete_source, methods=["DELETE"])
blueprint.add_url_rule("/sources", view_func=source_routing.list_sources, methods=["GET"])
blueprint.add_url_rule("/sources/<config_id>", view_func=source_routing.get_source, methods=["GET"])
blueprint.add_url_rule("/sources/<config_id>", view_func=source_routing.update_source, methods=["PUT"])
blueprint.add_url_rule("/sources/<config_id>/retention-preview", view_func=source_routing.retention_preview,
                       methods=["POST"])


@blueprint.before_request
def _require_session():
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active
            and session.get("_user_id") == current_user.get_id()):
        return _problem("UNAUTHORIZED")
    account = db.session.get(ClinicAccountPolicy, current_user.id)
    if account is None or account.status != "active":
        return _problem("FORBIDDEN")
    return None


@blueprint.after_request
def _response(response):
    # The app-level clinic guard can refuse the request before the blueprint runs.
    code = {401: "UNAUTHORIZED", 403: "FORBIDDEN"}.get(response.status_code)
    if code and response.mimetype != "application/problem+json":
        problem, _ = _problem(code)
        response.set_data(problem.get_data())
        response.mimetype = problem.mimetype
    response.cache_control.private = True
    response.cache_control.no_store = True
    return response


def _can_import(actor):
    try:
        import_centre._may_import(actor)
    except agent_channel.AgentChannelError as error:
        if error.code != "forbidden":
            raise
        return False
    return True


def _source_config(scope, relative_path, owners):
    if scope.root_id is None or relative_path is None or owners.get(scope.root_id) != scope.agent_id:
        return None
    try:
        return import_source_config.delivery_config(scope.root_id, relative_path)
    except agent_channel.AgentChannelError:
        return None


@blueprint.get("/intents")
def intents():
    actor = current_user.id
    if not _can_import(actor):
        return _problem("FORBIDDEN")
    items = import_centre.list_import_intents(actor_user_id=actor)
    channel = agent_channel.ClinicAgentIntent
    failed = {row.id for row in channel.query.filter_by(created_by_user_id=actor, status="DONE")}
    for item in items:
        if item["status"] == "OPEN" and item["id"] in failed:
            item["status"] = "FAILED"
    return jsonify(items=items, count=len(items))


def _import_write(target, operation):
    actor = current_user.id
    if not _can_import(actor):
        return _problem("FORBIDDEN")
    try:
        validate_csrf(request.headers.get("X-CSRF-Token") or request.headers.get("X-CSRFToken"))
    except ValidationError:
        return _problem("FORBIDDEN")
    key = request.headers.get("Idempotency-Key", "")
    if not 8 <= len(key) <= 128 or any(not 33 <= ord(char) <= 126 for char in key):
        return _problem("INVALID_PAYLOAD")
    request.max_content_length = min(MAX_BODY_BYTES, request.max_content_length or MAX_BODY_BYTES)
    try:
        body = json.loads(request.get_data().decode("utf-8"), object_pairs_hook=_unique_object) if request.is_json else None
    except (BadRequest, RequestEntityTooLarge, ValueError, RecursionError):
        return _problem("INVALID_PAYLOAD")
    if not isinstance(body, dict):
        return _problem("INVALID_PAYLOAD")
    digest = sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    scope = (actor, request.endpoint, target, key)

    connection = db.session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    def apply():
        return operation(actor, body)

    try:
        try:
            return jsonify(run_once(scope, digest, apply))
        except CommandProblem as error:
            if str(error) != "REVISION_CONFLICT" or db.session.get(ClinicParityReceipt, scope_key(scope)) is None:
                raise
            return jsonify(run_once(scope, digest, apply))
    except CommandProblem as error:
        return _problem(str(error))
    except agent_channel.AgentChannelError as error:
        return _assignment_error(error)


def _route_snapshot(item, actor, owners, now):
    scope = import_visibility.ImportScope(item.agent_id, item.root_id, item.source_id, item.snapshot_version)
    config = _source_config(scope, item.relative_path, owners)
    if config is None:
        raise agent_channel.AgentChannelError("not_found", "snapshot")
    author = config["target_author_user_id"]
    if author != actor and not _is_active_site_admin(db.session, current_user):
        raise agent_channel.AgentChannelError("not_found", "snapshot")
    account = db.session.get(ClinicAccountPolicy, author)
    if account is None or account.status != "active":
        raise agent_channel.AgentChannelError("forbidden", "inactive author")
    eligible_pools = {
        pool_id for pool_id in author_pool_ids(author)
        if (pool := db.session.get(pools.ClinicAuthorPool, pool_id)) is not None and pools.is_routable(pool, now)
    }
    pool_id = config["target_pool_id"]
    if pool_id is not None and pool_id not in eligible_pools:
        raise agent_channel.AgentChannelError("forbidden", "inactive pool")
    return config, eligible_pools


def _metadata(overrides, pool_id, eligible_pools):
    pool_override = overrides.get("pool_id")
    if isinstance(pool_override, dict) and pool_override.get("op") == "set":
        pool_id = pool_override.get("value")
        if not isinstance(pool_id, str) or pool_id not in eligible_pools:
            raise agent_channel.AgentChannelError("forbidden", "ineligible pool")
    pool_id = pool_id or (next(iter(eligible_pools)) if len(eligible_pools) == 1 else None)
    approved = recipient_defaults.eligible_user_ids(pool_id) if pool_id else ()
    return import_centre._stored_metadata(overrides, approved)


def _intent_audit(intent, action):
    if audit.record_in_tx(
        "recording.edited", actor_user_id=intent.created_by_user_id, target_user_id=intent.target_author_user_id,
        force=True, details={"fields": {"import_intent_id": intent.id, "import_action": action}},
    ) is None:
        raise CommandProblem("DEPENDENCY_UNAVAILABLE")


def _queue_snapshot(item, actor, owners, author_id, overrides, now, device_only):
    config, eligible_pools = _route_snapshot(item, actor, owners, now)
    author, pool_id, kind = (config[key] for key in ("target_author_user_id", "target_pool_id", "source_kind"))
    if author_id not in (None, author) or (device_only and kind != "device"):
        raise agent_channel.AgentChannelError("forbidden", "source route")
    channel = agent_channel.ClinicAgentIntent
    if channel.query.filter_by(agent_id=item.agent_id, source_id=item.source_id,
                               snapshot_version=item.snapshot_version).filter(or_(
        agent_channel.ClinicAgentIngest.query.filter_by(intent_id=channel.id).exists(),
        and_(channel.status != "CANCELLED", or_(
            channel.expires_at > now, import_centre.ClinicImportIntent.query.filter_by(id=channel.id).exists(),
        )),
    )).first() is not None:
        raise agent_channel.AgentChannelError("conflict", "snapshot already queued")
    version, metadata = _metadata(overrides, pool_id, eligible_pools)
    if item.size <= 0:
        raise agent_channel.AgentChannelError("invalid", "empty snapshot")
    filename = import_centre._file_name(item.relative_path)
    intent = import_centre.ClinicImportIntent(
        id=str(uuid4()), created_by_user_id=actor, source_kind=kind, source_ref=item.source_id,
        expected_files=json.dumps(import_centre._clean_files([filename])), target_author_user_id=author,
        target_pool_id=pool_id, language_id=config["language_id"], metadata_version=version, metadata_json=metadata,
        created_at=now, expires_at=now + import_centre.INTENT_TTL,
    )
    db.session.add_all([intent, agent_channel.ClinicAgentIntent(
        id=intent.id, created_by_user_id=actor, agent_id=item.agent_id, source_id=item.source_id,
        snapshot_version=item.snapshot_version, created_at=now, expires_at=intent.expires_at.replace(microsecond=0),
    )])
    _intent_audit(intent, "queue")
    return intent.id


def _queue(actor, body, *, device_only):
    if (not {"agent_id", "source_snapshot_ids"} <= body.keys()
            or body.keys() - {"agent_id", "source_snapshot_ids", "author_user_id", "overrides"}
            or not isinstance(body["source_snapshot_ids"], list) or not 1 <= len(body["source_snapshot_ids"]) <= 1000
            or ("author_user_id" in body and (type(body["author_user_id"]) is not int or body["author_user_id"] < 1))
            or not isinstance(body.get("overrides", {}), dict)):
        raise CommandProblem("INVALID_PAYLOAD")
    try:
        agent_id = str(UUID(body["agent_id"]))
        ids = [str(UUID(value)) for value in body["source_snapshot_ids"]]
    except (ValueError, TypeError, AttributeError):
        raise CommandProblem("INVALID_PAYLOAD") from None
    if len(set(ids)) != len(ids):
        raise CommandProblem("INVALID_PAYLOAD")
    inventory_model = agent_channel.ClinicAgentInventory
    selection = inventory_model.query.filter(inventory_model.agent_id == agent_id, inventory_model.id.in_(ids))
    snapshots = {row.id: row for row in selection.order_by(inventory_model.id).with_for_update()}
    owners = agent_channel.unique_root_owners(agent_channel.registered_agent_roots())
    now = agent_channel.utcnow()
    items = []
    for snapshot_id in ids:
        outcome = {"snapshot_id": snapshot_id}
        try:
            item = snapshots.get(snapshot_id)
            if item is None:
                raise agent_channel.AgentChannelError("not_found", "snapshot")
            outcome["intent_id"] = _queue_snapshot(item, actor, owners, body.get("author_user_id"),
                                                    body.get("overrides", {}), now, device_only)
        except agent_channel.AgentChannelError as error:
            outcome.update(status="REJECTED", code=error.code.upper())
        else:
            outcome["status"] = "OPEN"
        items.append(outcome)
    return {"items": items, "count": len(items)}


@blueprint.post("/device-send")
def device_send() -> Response | tuple[Response, int]:
    return _import_write("batch", lambda actor, body: _queue(actor, body, device_only=True))


@blueprint.post("/intents")
def create_intents() -> Response | tuple[Response, int]:
    return _import_write("batch", lambda actor, body: _queue(actor, body, device_only=False))


@blueprint.post("/intents/<uuid:intent_id>/retry")
def retry_intent(intent_id: UUID) -> Response | tuple[Response, int]:
    def retry(actor, body):
        if body:
            raise CommandProblem("INVALID_PAYLOAD")
        channel = agent_channel.ClinicAgentIntent
        owned = channel.query.filter_by(id=str(intent_id), created_by_user_id=actor)
        agent_intent = owned.with_for_update().first()
        if agent_intent is None:
            raise agent_channel.AgentChannelError("not_found", "paired import intent")
        intent = db.session.get(import_centre.ClinicImportIntent, agent_intent.id, with_for_update=True)
        if intent is None or intent.created_by_user_id != actor:
            raise agent_channel.AgentChannelError("not_found", "paired import intent")
        item = agent_channel.inventory_row(agent_intent.agent_id, agent_intent.source_id, agent_intent.snapshot_version)
        if item is None:
            raise agent_channel.AgentChannelError("not_found", "snapshot")
        now = agent_channel.utcnow()
        owners = agent_channel.unique_root_owners(agent_channel.registered_agent_roots())
        config, eligible_pools = _route_snapshot(item, actor, owners, now)
        if ((intent.source_kind, intent.target_author_user_id, intent.target_pool_id) !=
                (config["source_kind"], config["target_author_user_id"], config["target_pool_id"])):
            raise CommandProblem("REVISION_CONFLICT")
        overrides = interpret(intent)
        _metadata({name: value for name, value in overrides.items() if name != "schema_version"},
                  intent.target_pool_id, eligible_pools)
        sessions = agent_channel.ClinicAgentIngest.query.filter_by(intent_id=intent.id).with_for_update().all()
        if (intent.status not in ("OPEN", "EXPIRED") or agent_intent.status in ("CANCELLED", "STORED")
                or (agent_channel.aware(agent_intent.expires_at) > now and agent_intent.status != "DONE")
                or any(row.state != "RECEIVING" for row in sessions)):
            raise CommandProblem("REVISION_CONFLICT")
        intent.status, intent.expires_at = "OPEN", now + import_centre.INTENT_TTL
        agent_intent.status = "IN_PROGRESS" if sessions else "OPEN"
        agent_intent.expires_at = intent.expires_at.replace(microsecond=0)
        for row in sessions:
            row.expires_at = intent.expires_at
        _intent_audit(intent, "retry")
        return {"intent_id": intent.id, "status": intent.status}

    return _import_write(str(intent_id), retry)


def _visible_inventory() -> tuple[list[dict], bool] | None:
    actor = current_user.id
    admin = _is_active_site_admin(db.session(), current_user)
    led_pools = () if admin else worklist_entries._led_author_pools(actor)
    can_import = admin or _can_import(actor)
    if not (can_import or led_pools):
        return None

    registry = agent_channel.registered_agent_roots()
    owners = agent_channel.unique_root_owners(registry)
    roots_by_agent = {agent: roots for agent, (enabled, roots) in registry.items() if enabled}

    def may_read(uid, action, scope):
        if uid != actor or action != "read_inventory" or scope.root_id not in roots_by_agent.get(scope.agent_id, ()):
            return False
        if admin:
            return True
        item = agent_channel.inventory_row(scope.agent_id, scope.source_id, scope.snapshot_version)
        config = _source_config(scope, item.relative_path, owners) if item else None
        return config is not None and (
            config["target_pool_id"] in led_pools or (can_import and config["target_author_user_id"] == uid)
        )

    # This policy can only read inventory. The signing secret stays on the machine channel.
    channel_policy = agent_channel.ChannelPolicy(
        master_secret=b"", authorise=lambda uid, action: uid == actor and action == "read_inventory",
    )
    visibility = import_visibility.ClarificationPolicy(may_access=may_read)
    items: list[dict] = []
    for agent_id in roots_by_agent:
        body = import_visibility.list_inventory(visibility, channel_policy, actor_user_id=actor, agent_id=agent_id)
        snapshots = {
            (row.source_id, row.snapshot_version): row.id
            for row in agent_channel.ClinicAgentInventory.query.filter_by(agent_id=agent_id)
        }
        items.extend(row | {"agent_id": agent_id, "snapshot_id": snapshots[row["source_id"], row["snapshot_version"]]}
                     for row in body["items"] if (row["source_id"], row["snapshot_version"]) in snapshots)
    return items, bool(admin or led_pools)


@blueprint.get("/inventory")
def inventory():
    visible = _visible_inventory()
    if visible is None:
        return _problem("FORBIDDEN")
    items, can_clarify = visible
    return jsonify(items=items, count=len(items), can_clarify=can_clarify)


@blueprint.get("/firmware")
def firmware() -> Response | tuple[Response, int]:
    visible = _visible_inventory()
    if visible is None:
        return _problem("FORBIDDEN")
    targets = firmware_catalog.list_targets()
    items = [firmware_catalog.compare(row, targets) for row in visible[0]]
    return jsonify(catalog=[targets[key] for key in sorted(targets)], items=items, count=len(items),
                   firmware_action=get_firmware_action_status())


@blueprint.put("/firmware/<device_type>")
def update_firmware(device_type: str) -> Response | tuple[Response, int]:
    if not _is_active_site_admin(db.session(), current_user):
        return _problem("FORBIDDEN")
    try:
        validate_csrf(request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token"))
    except ValidationError:
        return _problem("FORBIDDEN")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.keys() - {"target_version", "expected_version"}:
        return _problem("INVALID_PAYLOAD")
    try:
        entry = firmware_catalog.set_target(device_type, body.get("target_version"), body.get("expected_version", 0))
    except ValueError:
        return _problem("INVALID_PAYLOAD")
    except firmware_catalog.RevisionConflict:
        db.session.rollback()
        return _problem("REVISION_CONFLICT")
    event = audit.record_in_tx(
        "admin.setting.changed", actor_kind="session", actor_user_id=current_user.id, force=True,
        details={"key": f"firmware:{entry['device_type']}", "new": entry},
    )
    if event is None:
        db.session.rollback()
        return _problem("DEPENDENCY_UNAVAILABLE")
    db.session.commit()
    return jsonify(entry)


def _clarification_policy():
    actor = current_user.id
    admin = _is_active_site_admin(db.session, current_user)
    led_pools = () if admin else worklist_entries._led_author_pools(actor)
    if not (admin or led_pools):
        return None

    owners = {} if admin else agent_channel.unique_root_owners(agent_channel.registered_agent_roots())

    def may_access(uid, action, scope):
        if uid != actor or action not in ("list_unassigned", "assign_unassigned", "suggest_author"):
            return False
        if admin:
            return True
        delivery = db.session.get(import_centre.ClinicImportDelivery, scope.delivery_id)
        config = _source_config(scope, delivery.relative_path, owners) if delivery else None
        return config is not None and config["target_pool_id"] in led_pools

    return import_visibility.ClarificationPolicy(may_access=may_access)


@blueprint.get("/unassigned")
def unassigned():
    visibility = _clarification_policy()
    if visibility is None:
        return _problem("FORBIDDEN")
    actor = current_user.id
    items = import_centre.list_unassigned(actor_user_id=actor, policy=visibility)
    return jsonify(items=items, count=len(items))


def _assignment_error(error):
    if error.code == "not_found":
        response = jsonify(type="about:blank", title="Nicht gefunden", status=404, code="NOT_FOUND")
        response.mimetype = "application/problem+json"
        return response, 404
    return _problem({"invalid": "INVALID_PAYLOAD", "forbidden": "FORBIDDEN", "conflict": "REVISION_CONFLICT"}
                    .get(error.code, "DEPENDENCY_UNAVAILABLE"))


@blueprint.get("/unassigned/<uuid:delivery_id>/candidates")
def assignment_candidates(delivery_id):
    visibility = _clarification_policy()
    if visibility is None:
        return _problem("FORBIDDEN")
    try:
        items = import_visibility.assignment_candidates(visibility, actor_user_id=current_user.id,
                                                        delivery_id=str(delivery_id))
    except agent_channel.AgentChannelError as error:
        return _assignment_error(error)
    return jsonify(items=items, count=len(items))


def _require_unassigned_for_suggestion(actor, delivery_id):
    visibility = _clarification_policy()
    if visibility is None:
        raise agent_channel.AgentChannelError("forbidden")
    delivery, _ = import_visibility._require_delivery(visibility, actor, "suggest_author", delivery_id)
    if delivery.status != "UNASSIGNED" or delivery.intent_id is not None:
        raise agent_channel.AgentChannelError("not_found", "delivery")


def _speech_seconds(segments, duration):
    # The client sorts by start time. Count overlapping speech only once.
    seconds = end = 0.0
    for segment in segments:
        stop = min(segment["end_time"], duration)
        seconds += max(0.0, stop - max(end, segment["start_time"]))
        end = max(end, stop)
    return seconds


def _suggestion_evidence(delivery_id):
    original = db.session.get(import_centre.ClinicImportOriginal, delivery_id)
    if original is None:
        raise agent_channel.AgentChannelError("not_found", "delivery original")
    try:
        path = local_path_from_key(original.storage_root, original.storage_key)
    except ValueError as error:
        raise IngestError("original_read_failed") from error

    with TemporaryDirectory(prefix="clinic-import-speaker-") as tmp:
        audio = decoder.decode_to_wav(path, tmp)
        if audio.status != decoder.OK or audio.input_sha256 != original.content_hash:
            raise IngestError("original_read_failed")
        if audio.duration_s < author_suggestion.MIN_SPEECH_SECONDS:
            return audio.duration_s, None
        seconds = _speech_seconds(speaker_client.diarize(audio.output_path), audio.duration_s)
        if seconds < author_suggestion.MIN_SPEECH_SECONDS:
            return seconds, None

        model = speaker_client.health()
        # The normal embed helper registers or touches a space. Here we only read it.
        space = (db.session.query(VoiceEmbeddingSpace.id, VoiceEmbeddingSpace.dimension)
                 .filter_by(backend_fingerprint=speaker_client.model_fingerprint(model))
                 .order_by(VoiceEmbeddingSpace.id).first())
        if space is None:
            return seconds, None
        vector = speaker_client.embed(audio.output_path)
        if space.dimension is not None and space.dimension != len(vector):
            raise speaker_client.SpeakerServiceBadResponse("embedding dimension does not match the model space")
        return seconds, speaker_client.EmbeddingResult(space.id, model, vector)


@blueprint.get("/unassigned/<uuid:delivery_id>/suggestion")
def author_suggestion_view(delivery_id):
    actor, delivery_id = current_user.id, str(delivery_id)
    try:
        _require_unassigned_for_suggestion(actor, delivery_id)
        seconds, embedding = _suggestion_evidence(delivery_id)
        _require_unassigned_for_suggestion(actor, delivery_id)
        if embedding is None:
            reason = (author_suggestion.REASON_SHORT_SPEECH if seconds < author_suggestion.MIN_SPEECH_SECONDS
                      else author_suggestion.REASON_NO_CANDIDATES)
            return jsonify(candidates=[], band=author_suggestion.BAND_NONE, reasons=[reason])

        targets = import_visibility._targets()
        if not _is_active_site_admin(db.session, current_user):
            targets = targets.filter(pools.ClinicAuthorPool.id.in_(worklist_entries._led_author_pools(actor)))
        now = agent_channel.utcnow()
        candidate_ids = {uid for uid, _, _, pool in targets if pools.is_routable(pool, now)}
        result = speaker_pipeline.suggest_for_embedding(embedding, seconds, candidate_user_ids=candidate_ids)
    except agent_channel.AgentChannelError as error:
        return _assignment_error(error)
    except (IngestError, OSError, speaker_client.SpeakerServiceError, author_suggestion.AuthorSuggestionError):
        return _problem("DEPENDENCY_UNAVAILABLE")
    return jsonify(candidates=[candidate.user_id for candidate in result.suggestions],
                   band=result.band, reasons=[result.reason])


@blueprint.post("/unassigned/<uuid:delivery_id>/assign")
def assign(delivery_id):
    visibility = _clarification_policy()
    if visibility is None:
        return _problem("FORBIDDEN")
    delivery_id = str(delivery_id)
    body = request.get_json(silent=True, cache=False)
    if not isinstance(body, dict):
        return _problem("INVALID_PAYLOAD")
    try:
        version = agent_channel.clean_id(body.pop("expected_snapshot_version", None), "expected_snapshot_version")
        if body.keys() == {"intent_id"}:
            agent_channel.clean_id(body["intent_id"], "intent_id")
        elif (body.keys() != {"target_author_user_id", "target_pool_id"}
                or type(body["target_author_user_id"]) is not int
                or not 1 <= body["target_author_user_id"] < 2**31
                or len(agent_channel.clean_id(body["target_pool_id"], "target_pool_id")) > 36):
            return _problem("INVALID_PAYLOAD")
        dictation_id = import_visibility.assign_target(
            visibility, actor_user_id=current_user.id, delivery_id=delivery_id, expected_snapshot_version=version, **body,
        )
    except (agent_channel.AgentChannelError, IngestError) as error:
        return _assignment_error(error)
    return jsonify(delivery_id=delivery_id, dictation_id=dictation_id)

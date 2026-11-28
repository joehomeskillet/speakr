"""Session-authenticated import centre and clarification routes."""

from tempfile import TemporaryDirectory

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user

from src.clinic import (
    agent_channel, author_suggestion, decoder, import_centre, import_source_config, import_visibility,
    pools, speaker_client, speaker_pipeline, worklist_entries,
)
from src.clinic import routes_import_sources_web as source_routing
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.ingest import IngestError
from src.clinic.parity_dispatch import _problem
from src.database import db
from src.models import VoiceEmbeddingSpace
from src.services.storage.locator import local_path_from_key


blueprint = Blueprint("clinic_imports_web", __name__, url_prefix="/api/clinic/v1/imports")


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
    return jsonify(items=items, count=len(items))


@blueprint.get("/inventory")
def inventory():
    actor = current_user.id
    admin = _is_active_site_admin(db.session, current_user)
    led_pools = () if admin else worklist_entries._led_author_pools(actor)
    can_import = admin or _can_import(actor)
    if not (can_import or led_pools):
        return _problem("FORBIDDEN")

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
    items = []
    for agent_id in roots_by_agent:
        body = import_visibility.list_inventory(visibility, channel_policy, actor_user_id=actor, agent_id=agent_id)
        items.extend(body["items"])
    return jsonify(items=items, count=len(items), can_clarify=bool(admin or led_pools))


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

"""Session-authenticated reads for the clinic import centre."""

import json

from flask import Blueprint, abort, jsonify, session
from flask_login import current_user

from src.clinic import agent_channel, import_centre, import_source_config, import_visibility, worklist_entries
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import _problem
from src.database import db


blueprint = Blueprint("clinic_imports_web", __name__, url_prefix="/api/clinic/v1/imports")


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


def _source_config(scope, relative_path):
    if scope.root_id is None or relative_path is None:
        return None
    try:
        matches = [
            config for kind in import_centre.SOURCE_KINDS
            if (config := import_source_config.resolve_source_config(kind, scope.root_id, relative_path)) is not None
        ]
    except agent_channel.AgentChannelError:
        return None  # Revoked or invalid roots confer no authority.
    return matches[0] if len(matches) == 1 else None


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

    agents = agent_channel.ClinicAgent.query.filter_by(enabled=True).order_by(agent_channel.ClinicAgent.id)
    roots_by_agent = {agent.id: set(json.loads(agent.allowed_sources)) for agent in agents}

    def may_read(uid, action, scope):
        if uid != actor or action != "read_inventory" or scope.root_id not in roots_by_agent.get(scope.agent_id, ()):
            return False
        if admin:
            return True
        item = agent_channel.inventory_row(scope.agent_id, scope.source_id, scope.snapshot_version)
        config = _source_config(scope, item.relative_path) if item else None
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
    return jsonify(items=items, count=len(items))


@blueprint.get("/unassigned")
def unassigned():
    actor = current_user.id
    admin = _is_active_site_admin(db.session, current_user)
    led_pools = () if admin else worklist_entries._led_author_pools(actor)
    if not (admin or led_pools):
        return _problem("FORBIDDEN")

    def may_read(uid, action, scope):
        if uid != actor or action != "list_unassigned":
            return False
        if admin:
            return True
        delivery = db.session.get(import_centre.ClinicImportDelivery, scope.delivery_id)
        config = _source_config(scope, delivery.relative_path) if delivery else None
        return config is not None and config["target_pool_id"] in led_pools

    visibility = import_visibility.ClarificationPolicy(may_access=may_read)
    items = import_centre.list_unassigned(actor_user_id=actor, policy=visibility)
    return jsonify(items=items, count=len(items))

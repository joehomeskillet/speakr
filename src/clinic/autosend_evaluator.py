"""Apply the stored autosend rule to an authenticated agent's stable snapshot."""

from src.database import db

from . import agent_channel, import_centre, import_source_config
from .batch_import_plan import AUDIO_EXTENSIONS
from .eol_filter import IMPORT, FileFacts, Policy, decide
from .migration_inventory_hints import PHILIPS_EOL, UNVERIFIED


def evaluate_inventory(channel_policy, snapshots, *, now, author_lookup):
    """Stage eligible intent pairs in the inventory writer's transaction."""
    owners = agent_channel.unique_root_owners({
        agent_id: (enabled, {root.upper() for root in roots})
        for agent_id, (enabled, roots) in agent_channel.registered_agent_roots().items()
    })
    for item, raw, changed in snapshots:
        _evaluate_snapshot(channel_policy, item, raw, changed, owners, now, author_lookup)


def _evaluate_snapshot(channel_policy, item, raw, changed, owners, now, author_lookup):
    if db.session.get(import_centre.ClinicAutosendIntent, item.id) is not None:
        if changed:
            raise agent_channel.AgentChannelError("conflict", "autosend snapshot identity changed")
        return

    # The import agent only reports snapshots after its stability samples and
    # post-read check. An explicit negative overrides that wire contract.
    if (not item.size or not item.relative_path.lower().endswith(AUDIO_EXTENSIONS)
            or raw.get("file_stable", True) is not True
            or owners.get(item.root_id.upper()) != item.agent_id):
        return
    try:
        config = import_source_config.delivery_config(item.root_id, item.relative_path)
    except agent_channel.AgentChannelError:
        return  # An unapproved path or ambiguous rule can stay visible in inventory.
    if config is None or not config["autosend_enabled"]:
        return
    author = author_lookup(device_id=item.root_id).author_user_id
    eol = (item.completion_source == PHILIPS_EOL and item.evidence_level != UNVERIFIED
           and raw.get("observed_completion") in (None, "completed"))
    filter_enabled = config["eol_filter_enabled"]
    # The authorized, persisted filter-off setting opts this source into non-EOL imports.
    policy = Policy(eol_filter_enabled=filter_enabled, auto_import_non_eol=filter_enabled is False)
    facts = FileFacts(item.relative_path, item.size, eol, stable=True,
                      author_known=author == config["target_author_user_id"])
    if decide(facts, policy).action != IMPORT or agent_channel.live_intent_exists(
        item.agent_id, item.source_id, item.snapshot_version, now,
    ):
        return
    source = db.session.get(import_source_config.ClinicImportSourceConfig, config["id"])
    if source is None:
        return

    transport = agent_channel.stage_snapshot_intent(
        channel_policy, actor_user_id=source.created_by_user_id, agent_id=item.agent_id,
        source_id=item.source_id, snapshot_version=item.snapshot_version, post_step="none", now=now,
    )
    intent = import_centre._stage_import_intent(
        actor_user_id=source.created_by_user_id, source_kind=config["source_kind"], source_ref=config["source_ref"],
        expected_files=(), author=author, target_pool_id=config["target_pool_id"], language_id=config["language_id"],
        metadata=import_centre._stored_metadata({}, ()), now=now,
    )
    db.session.flush()
    db.session.add(import_centre.ClinicAutosendIntent(
        inventory_id=item.id, agent_intent_id=transport.id, import_intent_id=intent.id,
    ))

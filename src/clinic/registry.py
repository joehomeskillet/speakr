"""Registers every clinic model module on ``db.metadata`` and builds the tables.

``init_db`` imports this module before ``create_all()``. Without the imports a
module whose models nobody imports yet would have no table in production. The
central migration entry point creates missing tables with ``checkfirst``, then
runs the registered modules' ``create_tables`` hooks for additive upgrades.
"""
from __future__ import annotations

import importlib

from src.database import db

# Every module under src/clinic that declares a db.Model subclass.
MODEL_MODULES = (
    "models",
    "identity",
    "pools",
    "entra",
    "release",
    "workflow",
    "presets",
    "vocabulary",
    "correction",
    "correction_recognition_marks",
    "live_session",
    "asr_profiles",
    "audio_revisions",
    "voice_consent",
    "import_centre",
    "output",
    "agent_channel",
    "revisions",
    "richtext",
    "entra_link_intent",
    "recipient_defaults",
    "recipient_assignment",
    "live_phrases",
    "import_source_config",
    "author_aliases",
    "context_grants",
    "case_handoff",
    "audio_invalidation",
    "parity_receipts",
    "acceptance_policy",
    "archive_policy",
    "worklist_layout",
    "worklist_filters",
    "workstation_preferences",
    "correction_modes",
    "key_bindings",
    "app_wiring",
    "alignment_tokens", "alignment",
    "output_preferences",
    "statistics_grants",
    "notification_preferences",
    "draft_store",
)


def load_model_modules() -> list:
    """Import all model modules and return them."""
    return [importlib.import_module("src.clinic." + name) for name in MODEL_MODULES]


def create_clinic_tables(engine=None) -> None:
    """Create all missing clinic tables, then run the per-module entry points."""
    modules = load_model_modules()
    bind = engine or db.engine
    db.metadata.create_all(
        bind=bind,
        tables=[t for t in db.metadata.tables.values() if t.name.startswith("clinic_")],
        checkfirst=True,
    )
    for module in modules:
        create = getattr(module, "create_tables", None)
        if callable(create):
            create(bind=bind)


# Registers the models at import time as well.
load_model_modules()

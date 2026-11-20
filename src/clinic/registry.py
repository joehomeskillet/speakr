"""Registers every clinic model module on ``db.metadata`` and builds the tables.

``init_db`` imports this module before ``create_all()``. Without the imports a
module whose models nobody imports yet would have no table in production. The
``create_tables`` entry points use ``checkfirst``, so a table is created exactly
once and an existing one is left alone.
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
    "live_phrases",
    "import_source_config",
    "context_grants",
    "audio_invalidation",
    "parity_receipts",
    "acceptance_policy",
    "archive_policy",
    "worklist_layout",
    "worklist_filters",
    "correction_modes",
    "app_wiring",
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

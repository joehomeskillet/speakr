"""Data-only clinic route catalog: identity, pools, grants, metadata, jobs, stats, training and voice consent."""

# Provider sign-in endpoints run before a session exists (disabled providers answer 422;
# handlers check CSRF, state and browser binding themselves).
PUBLIC: tuple[str, ...] = (
    # Identity, directory, pool, grant and preference handlers, mounted by
    # register_clinic_identity in src/app.py only in clinic mode. The provider sign-in
    # endpoints run before a session exists, so they are PUBLIC (disabled providers answer
    # an inert 422; the handlers check CSRF, state and browser binding themselves). Pool
    # writes are AUTH: the handler admits an active site admin or a manage_pools holder,
    # who need not be a stock admin. The pool-admin handlers (v8-29) are AUTH too: each admits a
    # site admin or manage_pools holder, and the usage preview also a pool lead (not a site admin). Directory and grant changes need an active site admin.
    'clinic_identity_providers.discovery',
    'clinic_identity_providers.entra_login',
    'clinic_identity_providers.entra_callback',
    'clinic_identity_providers.iwa_login',
)

AUTH: tuple[str, ...] = (
    'clinic_identity_providers.entra_link_start',
    'clinic_identity_providers.link_request',
    'clinic_identity_providers.unlink',
    'clinic_pools.list_pools',
    'clinic_pools.put_pool',
    'clinic_pool_admin.list_pools',
    'clinic_pool_admin.create_pool',
    'clinic_pool_admin.rename',
    'clinic_pool_admin.set_active',
    'clinic_pool_admin.set_validity',
    'clinic_pool_admin.link',
    'clinic_pool_admin.usage',
    'clinic_pool_admin.set_member_role',
    'clinic_grants.read',
    # Versioned command export/import (PAR-VOC-008). Handlers check scope ownership and CSRF.
    'clinic_commands.export_commands',
    'clinic_commands.import_commands',
    'clinic_preferences.recipient_default',
    'clinic_preferences.recipient_resolve',
    'clinic_preferences.last_selection',
    'clinic_preferences.workstation_issue',
    'clinic_preferences.workstation',
    'clinic_preferences.correction_mode',
    'clinic_preferences.notifications',
    # Metadata field definitions (x28), mounted by register_clinic_identity in clinic mode only.
    # Reading is for any signed-in user; the handler lists inactive fields to admins only.
    'clinic_metadata_fields.list_fields',
    # Recordings list, processing overview, statistics, ASR training and import-agent handlers,
    # mounted by register_clinic_operations in src/app.py only in clinic mode. Recordings, jobs
    # and statistics are AUTH: each handler authenticates the browser session itself and decides
    # every row or role through the central policy (jobs retry and statistics scope refuse with 403
    # inside the handler). Training is ADMIN (the handler additionally needs an active site admin).
    'clinic_jobs.health',
    'clinic_jobs.retry',
    'clinic_statistics.aggregates',
    # --- v8-75: own-user voice consent and voice enrollment (mounted by register_clinic_voice_consent) ---
    # Each handler authenticates the browser session itself (active user, session id match, CSRF for
    # writes) and acts only on current_user: no body or URL names a subject.
    'clinic_voice_consent.get_consent',
    'clinic_voice_consent.grant',
    'clinic_voice_consent.revoke',
    'voice_enrollment.get_own_voice_enrollment',
    'voice_enrollment.enroll_own_voice_sample',
    'voice_enrollment.grant_own_voice_consent',
    'voice_enrollment.revoke_own_voice_consent',
)

ADMIN: tuple[str, ...] = (
    'clinic_directory.capability',
    'clinic_directory.preview',
    'clinic_directory.apply',
    'clinic_grants.change',
    'clinic_statistics.list_grants',
    'clinic_statistics.change_grant',
    'clinic_task_models.settings',
    # Admin dictation transcript and audio (w308): own blueprint, mounted by
    # register_clinic_identity in clinic mode only.
    'clinic_admin_dictations.transcript',
    'clinic_admin_dictations.audio',
    'clinic_metadata_fields.save_field',
    # Category set writes (PAR-CFG-003). The handler also requires an active site admin.
    'clinic_categories.create_category_set',
    'clinic_categories.update_category_set',
    'clinic_training.manifest',
    'clinic_training.schedule',
    'clinic_training.start',
    'clinic_training.status',
    'clinic_training.cancel',
    'clinic_statistics.collection_policy',
    'clinic_statistics.collection_period',
    'clinic_statistics.collection_retention',
)

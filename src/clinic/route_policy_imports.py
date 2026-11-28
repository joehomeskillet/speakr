"""Data-only clinic route catalog: import agent machine endpoints, web imports and recordings list."""

# Machine principal endpoints: no browser session exists, so the guard lets them through;
# every agent route verifies proxy secret plus certificate fingerprint itself (401 otherwise).
PUBLIC: tuple[str, ...] = (
    # The import agent is a machine principal: no browser session exists, so the guard must let the
    # request through. Every agent route authenticates itself (proxy secret plus certificate
    # fingerprint, 401 otherwise) and the blueprint is CSRF-exempt in the app.
    'clinic_agent.inventory',
    'clinic_agent.intents',
    'clinic_agent.begin',
    'clinic_agent.chunk',
    'clinic_agent.finalize',
    'clinic_agent.receipt',
    'clinic_agent.abort',
    'clinic_agent.outcomes',
)

AUTH: tuple[str, ...] = (
    'clinic_recordings.list_recordings',
    'clinic_offline.capability',
    'clinic_imports_web.intents',
    'clinic_imports_web.create_intents',
    'clinic_imports_web.device_send',
    'clinic_imports_web.retry_intent',
    'clinic_imports_web.inventory',
    'clinic_imports_web.unassigned',
    'clinic_imports_web.assignment_candidates',
    'clinic_imports_web.author_suggestion_view',
    'clinic_imports_web.assign',
    'clinic_imports_web.list_sources',
    'clinic_imports_web.get_source',
    'clinic_imports_web.update_source',
    'clinic_imports_web.create_source',
    'clinic_imports_web.delete_source',
)

ADMIN: tuple[str, ...] = ('clinic_imports_web.retention_preview',)

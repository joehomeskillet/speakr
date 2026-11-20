"""Route-policy table: the classification of every URL rule of the app.

The key is the Flask endpoint. An endpoint that is not listed here is denied in
clinic mode (fail closed), and tests/clinic/test_s01_policy.py fails when the
app has an endpoint that is not listed, so a new route cannot appear unnoticed.

Kinds (names follow clinic-spec/tests/route-policy.json):

PUBLIC     reachable without a session (static files, login, OIDC)
AUTH       needs a signed-in user; the handler works on that user's own data
ADMIN      needs a signed-in admin; identity/system administration, no content
RECORDING  addresses a recording; the central policy decides before the handler
DISABLED   switched off by a clinic feature lock (deny by default)

A DISABLED route behaves like a RECORDING route once its feature is explicitly
enabled in the defaults, so enabling a feature never opens an ACL bypass.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.clinic.config import clinic_mode_enabled

PUBLIC = 'public'
AUTH = 'auth'
ADMIN = 'admin'
RECORDING = 'recording'
DISABLED = 'disabled'

DEFAULT_BODY_KEYS = ('recording_ids',)

# Action a DISABLED route needs once its feature is enabled (default: edit).
_FEATURE_ACTIONS = {
    'stock_unversioned_export': 'export',
    'clinical_user_hard_delete': 'delete',
}


@dataclass(frozen=True)
class RouteRule:
    kind: str
    action: str | None = None
    feature: str | None = None
    arg: str = 'recording_id'          # URL argument that identifies the recording
    # Keys that carry recording ids besides the generic ones (any key whose name
    # has "recording" and "id", see src/clinic/request_ids.py), in any channel.
    body_keys: tuple[str, ...] = ()
    # Flags that turn the call into a hard delete of the recordings it names.
    delete_flags: tuple[str, ...] = ()
    # The call works on every recording of the caller without naming them.
    batch_all: bool = False
    # (query argument, value) pairs the route refuses in clinic mode.
    forbid_args: tuple[tuple[str, str], ...] = ()


def _split(text: str) -> tuple[str, ...]:
    return tuple(text.split())


_PUBLIC = _split("""
    static robots_txt service_worker auth.login auth.sso_login auth.sso_callback
    api_v1.get_sso_status system.get_csrf_token
""")

_AUTH = _split("""
    auth.account auth.account_storage auth.change_password auth.logout auth.record_timezone
    auth.sso_link auth.sso_unlink
    auth.update_auto_speaker_labelling auth.update_auto_summarization
    api_v1.create_folder api_v1.create_tag api_v1.delete_folder api_v1.delete_tag
    api_v1.get_capabilities api_v1.get_current_user api_v1.get_docs api_v1.get_folder
    api_v1.get_openapi_spec api_v1.get_stats api_v1.get_transcription_info api_v1.list_folders
    api_v1.list_recording_changes api_v1.list_recordings api_v1.list_tags
    api_v1.search_recordings_v1 api_v1.update_auto_summarization api_v1.update_folder
    api_v1.update_tag api_v1.upload_recording
    export_templates.apply_filename_template export_templates.create_default_export_templates
    export_templates.create_export_template export_templates.delete_export_template
    export_templates.get_export_templates export_templates.update_export_template
    folders.create_folder folders.delete_folder folders.get_folders folders.update_folder
    initial_prompt_templates.create_default_initial_prompt_templates
    initial_prompt_templates.create_initial_prompt_template
    initial_prompt_templates.delete_initial_prompt_template
    initial_prompt_templates.get_initial_prompt_templates
    initial_prompt_templates.update_initial_prompt_template
    naming_templates.create_default_naming_templates naming_templates.create_naming_template
    naming_templates.delete_naming_template naming_templates.get_default_naming_template
    naming_templates.get_naming_template naming_templates.get_naming_templates
    naming_templates.set_default_naming_template naming_templates.test_naming_template
    naming_templates.update_naming_template
    notifications.dismiss_notification notifications.list_notifications
    notifications.mark_notifications_read notifications.notification_count
    recording_sessions.abort_session recording_sessions.create_session
    recording_sessions.finalize_session recording_sessions.finalize_sliced_upload
    recording_sessions.get_session recording_sessions.upload_chunk
    recordings.cancel_upload_route recordings.clear_completed_jobs recordings.clear_failed_jobs
    recordings.discard_upload_join recordings.get_archived_recordings
    recordings.get_inbox_recordings recordings.get_job_queue_status recordings.get_recordings
    recordings.get_recordings_paginated recordings.share_target
    recordings.upload_file
    system.check_deletion_permission system.get_config system.get_system_info
    system.get_user_token_budget system.save_user_preferences
    tags.create_tag tags.delete_tag tags.get_tags tags.update_tag
    templates.create_default_templates templates.create_transcript_template
    templates.delete_transcript_template templates.get_transcript_templates
    templates.update_transcript_template
""")

# Registered by src/app.py only in clinic mode (register_clinic_resources), so they
# are classified only then; the table never lists a route the app does not have.
# Each handler acts on the caller's own or pool-scoped data; a body or query that
# names a recording is decided by the guard like a view before the handler runs.
_CLINIC_RESOURCES = _split("""
    clinic_parity.execute
    clinic_vocabulary.search clinic_vocabulary.export clinic_vocabulary.import_text
    clinic_vocabulary.additions clinic_vocabulary.suppress clinic_vocabulary.reactivate
    clinic_vocabulary_review.list_resource_vocabulary_reviews
    clinic_vocabulary_review.undo_resource_vocabulary_review
    clinic_commands.list_commands clinic_commands.list_pronunciations
    clinic_commands.save_pronunciation_hint_endpoint
    clinic_commands.get_pronunciation_capabilities clinic_commands.execute_command_endpoint
""")

# Worklist, workflow, history, template, category, regex and column handlers, mounted
# by register_clinic_worklist in src/app.py only in clinic mode. Their URLs carry a
# clinic dictation id (not a recording id), so the guard cannot resolve an object:
# each handler authenticates the session, checks CSRF on writes and then decides the
# object through the central policy (pool_policy.decide), failing closed.
_CLINIC_WORKLIST = _split("""
    clinic_worklist.rows clinic_worklist.own_filters clinic_worklist.own_filter
    clinic_worklist.create_filter clinic_worklist.update_filter clinic_worklist.delete_filter
    clinic_worklist.own_layout clinic_columns.own_layout
    clinic_workflow.metadata clinic_workflow.release clinic_workflow.archive
    clinic_history.get_history clinic_history.review_lease clinic_dictation_detail.get_detail
    clinic_templates.list_templates clinic_templates.get_template
    clinic_templates.get_template_content clinic_templates.save_template
    clinic_templates.assign_template_route clinic_templates.delete_template
    clinic_categories.list_categories clinic_categories.check_categories
    clinic_regex.preview clinic_regex.apply
""")

# ASR profile handlers (w104s), mounted by register_clinic_resources in clinic mode only.
# The blueprint guard authenticates the session and checks CSRF on writes; each handler
# acts on the caller's own profile only.
_CLINIC_ASR = _split("""
    clinic_asr_profiles.show clinic_asr_profiles.select
""")

# Identity, directory, pool, grant and preference handlers, mounted by
# register_clinic_identity in src/app.py only in clinic mode. The provider sign-in
# endpoints run before a session exists, so they are PUBLIC (disabled providers answer
# an inert 422; the handlers check CSRF, state and browser binding themselves). Pool
# writes are AUTH: the handler admits an active site admin or a manage_pools holder,
# who need not be a stock admin. The pool-admin handlers (v8-29) are AUTH too: each admits a
# site admin or manage_pools holder, and the usage preview also a pool lead (not a site admin). Directory and grant changes need an active site admin.
_CLINIC_IDENTITY_PUBLIC = _split("""
    clinic_identity_providers.discovery clinic_identity_providers.entra_login
    clinic_identity_providers.entra_callback clinic_identity_providers.iwa_login
""")

_CLINIC_IDENTITY = _split("""
    clinic_identity_providers.entra_link_start clinic_identity_providers.link_request
    clinic_identity_providers.unlink
    clinic_pools.list_pools clinic_pools.put_pool
    clinic_pool_admin.list_pools clinic_pool_admin.create_pool clinic_pool_admin.rename
    clinic_pool_admin.set_active clinic_pool_admin.set_validity clinic_pool_admin.link
    clinic_pool_admin.usage
    clinic_grants.read
    clinic_preferences.recipient_default clinic_preferences.recipient_resolve
    clinic_preferences.last_selection clinic_preferences.workstation_issue
    clinic_preferences.workstation clinic_preferences.correction_mode
""")

_CLINIC_IDENTITY_ADMIN = _split("""
    clinic_directory.capability clinic_directory.preview clinic_directory.apply
    clinic_grants.change
""")

_ADMIN = _split("""
    admin.admin admin.admin_add_user admin.admin_audit_log admin.admin_audit_log_export
    admin.admin_get_auto_process_status admin.admin_get_daily_token_stats
    admin.admin_get_daily_transcription_stats admin.admin_get_llm_temperatures
    admin.admin_get_monthly_token_stats admin.admin_get_monthly_transcription_stats
    admin.admin_get_settings admin.admin_get_stats admin.admin_get_stats_overview
    admin.admin_get_token_stats admin.admin_get_transcription_stats
    admin.admin_get_user_token_stats admin.admin_get_user_transcription_stats
    admin.admin_get_users admin.admin_get_visible_models admin.admin_set_llm_temperatures
    admin.admin_toggle_admin admin.admin_update_setting admin.admin_update_user
    admin.admin_voice_embedding_check admin.admin_voice_embedding_rebaseline
    admin.admin_voice_embedding_status admin.get_auto_deletion_stats admin.run_auto_deletion
    admin.admin_clinic_dictations
""")

# Admin dictation transcript and audio (w308): own blueprint, mounted by
# register_clinic_identity in clinic mode only.
_CLINIC_ADMIN_DICTATIONS = _split("""
    clinic_admin_dictations.transcript clinic_admin_dictations.audio
""")

# Metadata field definitions (x28), mounted by register_clinic_identity in clinic mode only.
# Reading is for any signed-in user; the handler lists inactive fields to admins only.
_CLINIC_METADATA_FIELDS_AUTH = _split("""
    clinic_metadata_fields.list_fields
""")
_CLINIC_METADATA_FIELDS_ADMIN = _split("""
    clinic_metadata_fields.save_field
""")

# Audio, dictation-property, bundle, live, correction and TTS handlers, mounted by
# register_clinic_audio in src/app.py only in clinic mode (start/stop live session are
# commands on clinic_parity.execute). Their URLs carry a clinic dictation id, so each
# handler authenticates the session, checks CSRF on writes and decides the object through
# the central policy (pool_policy.decide) before it reads or changes anything.
_CLINIC_AUDIO = _split("""
    clinic_audio.get_current clinic_audio.edit clinic_audio.derived clinic_audio.peaks
    clinic_dictation_properties.validate clinic_dictation_properties.protection_state
    clinic_dictation_properties.set_protection clinic_dictation_properties.unlock
    clinic_bundle.export clinic_bundle.import_session
    clinic_live.pause clinic_live.resume_recording clinic_live.append_phrase
    clinic_live.resume clinic_live.switch_draft
    clinic_correction.get_correction clinic_correction.save clinic_correction.add_marker
    clinic_correction.change_marker
    clinic_tts.capability clinic_tts.speak
    clinic_clipboard.prepare clinic_clipboard.outcome
    clinic_output.targets clinic_output.request_export clinic_output.export_status
""")

# Recordings list, processing overview, statistics, ASR training and import-agent handlers,
# mounted by register_clinic_operations in src/app.py only in clinic mode. Recordings, jobs
# and statistics are AUTH: each handler authenticates the browser session itself and decides
# every row or role through the central policy (jobs retry and statistics scope refuse with 403
# inside the handler). Training is ADMIN (the handler additionally needs an active site admin).
_CLINIC_OPERATIONS = _split("""
    clinic_recordings.list_recordings
    clinic_jobs.health clinic_jobs.retry
    clinic_statistics.aggregates
""")
_CLINIC_OPERATIONS_ADMIN = _split("""
    clinic_training.manifest clinic_training.schedule clinic_training.start
    clinic_training.status clinic_training.cancel
""")
# The import agent is a machine principal: no browser session exists, so the guard must let the
# request through. Every agent route authenticates itself (proxy secret plus certificate
# fingerprint, 401 otherwise) and the blueprint is CSRF-exempt in the app.
_CLINIC_AGENT_MACHINE = _split("""
    clinic_agent.inventory clinic_agent.intents clinic_agent.begin clinic_agent.chunk
    clinic_agent.finalize clinic_agent.receipt clinic_agent.abort clinic_agent.outcomes
""")

# --- v8-75: own-user voice consent and voice enrollment (mounted by register_clinic_voice_consent) ---
# Each handler authenticates the browser session itself (active user, session id match, CSRF for
# writes) and acts only on current_user: no body or URL names a subject.
_CLINIC_VOICE_CONSENT_V8_75 = _split("""
    clinic_voice_consent.get_consent clinic_voice_consent.grant clinic_voice_consent.revoke
    voice_enrollment.get_own_voice_enrollment voice_enrollment.enroll_own_voice_sample
    voice_enrollment.grant_own_voice_consent voice_enrollment.revoke_own_voice_consent
""")
# --- end v8-75 ---

_DISABLED = {
    'internal_sharing': """
        groups.add_team_member groups.create_team
        groups.delete_team groups.get_team groups.get_teams groups.remove_team_member
        groups.update_team groups.update_team_member
        tags.create_group_tag tags.get_group_tags folders.create_group_folder
        folders.get_group_folders admin.group_management""",
    'chat': """
        recordings.chat_with_transcription recordings.download_chat_word
        api_v1.chat_with_recording""",
    'inquire': """
        inquire.create_inquire_session inquire.download_inquire_conversation
        inquire.get_available_filters inquire.get_inquire_sessions inquire.inquire
        inquire.inquire_chat inquire.inquire_search api_v1.inquire_v1
        admin.admin_inquire_status admin.admin_process_recordings_for_inquire""",
    'user_webhooks': """
        webhooks.create_webhook webhooks.delete_webhook webhooks.get_delivery
        webhooks.get_webhook webhooks.list_deliveries webhooks.list_webhooks
        webhooks.replay_delivery webhooks.rotate_secret webhooks.test_fire
        webhooks.update_webhook""",
    'personal_api_tokens': """
        tokens.create_token tokens.list_tokens tokens.revoke_token tokens.update_token
        api_v1.exchange_sso_token api_v1.get_current_token api_v1.revoke_current_token""",
    'speaker_identification': """
        speakers.clear_speaker_embeddings speakers.create_speaker
        speakers.delete_all_speakers speakers.delete_speaker speakers.delete_voice_sample
        speakers.get_snippet_audio speakers.get_snippets speakers.get_speaker_recordings
        speakers.get_speaker_suggestions speakers.get_speakers speakers.list_voice_samples
        speakers.merge_speaker_profiles speakers.search_speakers speakers.update_speaker
        recordings.auto_identify_speakers api_v1.identify_speakers api_v1.create_speaker
        api_v1.delete_speaker api_v1.list_speakers api_v1.update_speaker""",
    'generative_summary': """
        recordings.generate_summary_endpoint recordings.reprocess_summary
        recordings.regenerate_title recordings.translate_transcription
        api_v1.api_regenerate_title api_v1.start_summarization""",
    'stock_unversioned_export': """
        recordings.download_notes_word recordings.download_summary_word
        recordings.download_transcript_with_template recordings.export_existing_recordings""",
    'clinical_user_hard_delete': """
        recordings.delete_recording recordings.delete_recording_audio
        recordings.bulk_delete_recordings api_v1.delete_recording
        api_v1.delete_recording_audio api_v1.batch_delete_recordings admin.admin_delete_user""",
    'auto_process': """
        admin.admin_start_auto_process admin.admin_stop_auto_process
        admin.admin_trigger_auto_process admin.admin_update_auto_process_config""",
    'cloud_provider_selection': """
        admin.admin_discover_transcription_models admin.admin_save_visible_models""",
    'generic_admin_content_access': """
        admin.migrate_existing_recordings_api""",
    'external_ingest': """
        api_v1.upload_from_asr_voice_recorder""",
    'push_notifications': """
        push.get_push_config push.subscribe push.unsubscribe""",
}

# Recording routes by the action they need. The recording id is in the URL.
_RECORDING = {
    'view': """
        api_v1.get_recording api_v1.get_notes
        api_v1.get_recording_speakers api_v1.get_recording_status api_v1.get_summary
        api_v1.get_transcript api_v1.list_external_refs
        recordings.get_recording_detail recordings.get_recording_status_only
        recordings.get_status recordings.get_summary_prompt recordings.index""",
    'play': """
        recordings.get_audio recordings.get_audio_peaks""",
    'download': """
        api_v1.download_audio""",
    'edit': """
        api_v1.add_external_ref api_v1.add_tags_to_recording api_v1.assign_speakers
        api_v1.delete_external_ref api_v1.remove_tag_from_recording
        api_v1.replace_external_refs api_v1.replace_notes api_v1.replace_recording_tags
        api_v1.replace_summary api_v1.start_transcription api_v1.update_recording
        folders.assign_recording_folder folders.remove_recording_folder
        recordings.add_tag_to_recording recordings.remove_tag_from_recording
        recordings.reorder_recording_tags recordings.process_recording_chunks_endpoint
        recordings.reprocess_transcription recordings.reset_status
        recordings.save_summary_prompt_route recordings.toggle_archive
        recordings.toggle_deletion_exempt recordings.toggle_highlight
        recordings.toggle_inbox recordings.update_speakers recordings.update_transcript
        recordings.update_transcription""",
}

# Routes whose URL carries a job id; the job resolves to its recording.
_JOB_ROUTES = {'recordings.delete_job': 'edit', 'recordings.retry_failed_job': 'edit'}

# Routes that name recordings in the request body: endpoint -> (action, keys).
_BODY_ROUTES = {
    'recordings.save_metadata': ('edit', ('id',)),
    'recordings.bulk_reprocess': ('edit', ('recording_ids',)),
    'recordings.bulk_toggle': ('edit', ('recording_ids',)),
    'recordings.bulk_update_tags': ('edit', ('recording_ids',)),
    'recordings.get_batch_recording_status': ('view', ('recording_ids',)),
    'recordings.merge_recordings_endpoint': ('edit', ('recording_ids', 'notes_source_id')),
    'folders.bulk_assign_folder': ('edit', ('recording_ids',)),
    'api_v1.batch_update_recordings': ('edit', ('recording_ids',)),
    'api_v1.batch_transcribe_recordings': ('edit', ('recording_ids',)),
}


# Per-endpoint additions on top of the kind: endpoint -> RouteRule fields.
_EXTRAS: dict[str, dict] = {
    # merge can hard-delete its sources (delete_originals) or their audio.
    'recordings.merge_recordings_endpoint': {'delete_flags': ('delete_originals', 'remove_original_audio')},
    # exports every recording of the caller: each one needs the export grant.
    'recordings.export_existing_recordings': {'batch_all': True},
    # scope=all gives an admin the global counts.
    'api_v1.get_stats': {'forbid_args': (('scope', 'all'),)},
}


def _build() -> dict[str, RouteRule]:
    table: dict[str, RouteRule] = {}

    def add(endpoint: str, rule: RouteRule) -> None:
        if endpoint in table:
            raise ValueError(f'route-policy: {endpoint} is classified twice')
        table[endpoint] = rule

    clinic = clinic_mode_enabled()
    for endpoint in _PUBLIC + ((_CLINIC_IDENTITY_PUBLIC + _CLINIC_AGENT_MACHINE) if clinic else ()):
        add(endpoint, RouteRule(PUBLIC))
    for endpoint in _AUTH + ((_CLINIC_RESOURCES + _CLINIC_ASR + _CLINIC_WORKLIST + _CLINIC_IDENTITY
                                       + _CLINIC_METADATA_FIELDS_AUTH + _CLINIC_AUDIO
                                       + _CLINIC_OPERATIONS) if clinic else ()):
        add(endpoint, RouteRule(AUTH))
    # v8-75
    for endpoint in (_CLINIC_VOICE_CONSENT_V8_75 if clinic else ()):
        add(endpoint, RouteRule(AUTH))
    for endpoint in _ADMIN + ((_CLINIC_IDENTITY_ADMIN + _CLINIC_ADMIN_DICTATIONS
                                    + _CLINIC_METADATA_FIELDS_ADMIN + _CLINIC_OPERATIONS_ADMIN) if clinic else ()):
        add(endpoint, RouteRule(ADMIN))
    for feature, endpoints in _DISABLED.items():
        for endpoint in _split(endpoints):
            add(endpoint, RouteRule(DISABLED, action=_FEATURE_ACTIONS.get(feature, 'edit'),
                                    feature=feature, body_keys=DEFAULT_BODY_KEYS))
    for action, endpoints in _RECORDING.items():
        for endpoint in _split(endpoints):
            add(endpoint, RouteRule(RECORDING, action=action))
    for endpoint, action in _JOB_ROUTES.items():
        add(endpoint, RouteRule(RECORDING, action=action, arg='job_id'))
    for endpoint, (action, keys) in _BODY_ROUTES.items():
        add(endpoint, RouteRule(RECORDING, action=action, body_keys=keys))
    for endpoint, extra in _EXTRAS.items():
        table[endpoint] = replace(table[endpoint], **extra)
    return table


ROUTE_POLICY: dict[str, RouteRule] = _build()

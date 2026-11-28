"""Data-only clinic route catalog: editor, audio, live, correction, resources and ASR profiles."""

PUBLIC: tuple[str, ...] = ()

# Registered by register_clinic_resources and register_clinic_audio only in clinic mode.
# Handlers authenticate the session, check CSRF on writes and decide object access via
# pool_policy.decide (or act on the caller's own / pool-scoped resources).
AUTH: tuple[str, ...] = (
    # Registered by src/app.py only in clinic mode (register_clinic_resources), so they
    # are classified only then; the table never lists a route the app does not have.
    # Each handler acts on the caller's own or pool-scoped data; a body or query that
    # names a recording is decided by the guard like a view before the handler runs.
    'clinic_parity.execute',
    'clinic_vocabulary.search',
    'clinic_vocabulary.export',
    'clinic_vocabulary.import_text',
    'clinic_vocabulary.additions',
    'clinic_vocabulary.suppress',
    'clinic_vocabulary.reactivate',
    'clinic_vocabulary.bulk',
    'clinic_vocabulary_review.list_resource_vocabulary_reviews',
    'clinic_vocabulary_review.undo_resource_vocabulary_review',
    'clinic_commands.list_commands',
    'clinic_commands.list_pronunciations',
    'clinic_commands.save_pronunciation_hint_endpoint',
    'clinic_commands.get_pronunciation_capabilities',
    'clinic_commands.execute_command_endpoint',
    # ASR profile handlers (w104s), mounted by register_clinic_resources in clinic mode only.
    # The blueprint guard authenticates the session and checks CSRF on writes; each handler
    # acts on the caller's own profile only.
    'clinic_asr_profiles.show',
    'clinic_asr_profiles.select',
    'clinic_workflow.skip_asr',
    # Audio, dictation-property, bundle, live, correction and TTS handlers, mounted by
    # register_clinic_audio in src/app.py only in clinic mode (start/stop live session are
    # commands on clinic_parity.execute). Their URLs carry a clinic dictation id, so each
    # handler authenticates the session, checks CSRF on writes and decides the object through
    # the central policy (pool_policy.decide) before it reads or changes anything.
    'clinic_audio.get_current',
    'clinic_audio.edit',
    'clinic_audio.derived',
    'clinic_audio.peaks',
    'clinic_audio.upload_segment',
    'clinic_dictation_properties.validate',
    'clinic_dictation_properties.protection_state',
    'clinic_dictation_properties.set_protection',
    'clinic_dictation_properties.unlock',
    'clinic_bundle.export',
    'clinic_bundle.import_session',
    'clinic_live.pause',
    'clinic_live.resume_recording',
    'clinic_live.append_phrase',
    'clinic_live.resume',
    'clinic_live.switch_draft',
    'clinic_live.stream_audio',
    # Durable live drafts additionally require ownership of the underlying dictation.
    'clinic_live_drafts.list_drafts',
    'clinic_live_drafts.create_draft',
    'clinic_live_drafts.get_draft',
    'clinic_live_drafts.save_draft',
    'clinic_live_drafts.discard_draft',
    'clinic_correction.get_correction',
    'clinic_correction.save',
    'clinic_correction.add_marker',
    'clinic_correction.change_marker',
    'clinic_correction.get_review_markers',
    'clinic_correction.confirm_review_marker',
    'clinic_correction.propose_vocabulary',
    # Read-only document and template preview. The handler checks correction_read
    # and never stores the upload or replaces the saved revision.
    'clinic_correction_documents.preview_document',
    'clinic_correction_documents.preview_template',
    'clinic_alignment.get_document',
    'clinic_alignment.get_alignment',
    'clinic_alignment.post_alignment',
    'clinic_alignment.get_operation',
    'clinic_tts.capability',
    'clinic_tts.speak',
)

ADMIN: tuple[str, ...] = ()

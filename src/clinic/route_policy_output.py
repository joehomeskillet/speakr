"""Data-only clinic route catalog: worklist, workflow, templates, context, acceptance and output."""

PUBLIC: tuple[str, ...] = ()

# Registered by register_clinic_worklist and register_clinic_audio only in clinic mode.
# Handlers authenticate the session, check CSRF on writes and enforce object access
# through the central policy (pool_policy.decide).
AUTH: tuple[str, ...] = (
    # These clinic-only endpoints authenticate the session and check CSRF on writes.
    # Handlers enforce object access. W13 assignment checks site admin or routed pool
    # lead status and pool grants directly, including explicit reassign denies.
    'clinic_worklist.rows',
    'clinic_worklist.own_filters',
    'clinic_worklist.own_filter',
    'clinic_worklist.create_filter',
    'clinic_worklist.update_filter',
    'clinic_worklist.delete_filter',
    'clinic_worklist.own_layout',
    'clinic_columns.own_layout',
    'clinic_workflow.metadata',
    'clinic_workflow.release',
    'clinic_workflow.archive',
    'clinic_workflow.send',
    'clinic_history.get_history',
    'clinic_history.review_lease',
    'clinic_dictation_detail.get_detail',
    'clinic_dictation_detail.get_review',
    'clinic_dictation_detail.summary',
    'clinic_context.get_context',
    'clinic_context.post_context',
    'clinic_templates.list_templates',
    'clinic_templates.get_template',
    'clinic_templates.get_template_content',
    'clinic_templates.save_template',
    'clinic_templates.assign_template_route',
    'clinic_templates.delete_template',
    'clinic_categories.list_categories',
    'clinic_categories.check_categories',
    'clinic_regex.preview',
    'clinic_regex.apply',
    'clinic_assignment.assign',
    'clinic_assignment.candidates',
    # v8-74: acceptance policy read/publish and the acceptance-state read (P07), mounted by
    # register_clinic_worklist in clinic mode only. All three are AUTH: the handlers authenticate the session,
    # check CSRF on writes and decide the object themselves (policy action 'view'; publishing needs an active
    # site admin and a refusal is audited), so the table does not stand in for them.
    'clinic_acceptance_policy.read',
    'clinic_acceptance_policy.save',
    'clinic_workflow.read_acceptance_state',
    'clinic_clipboard.prepare',
    'clinic_clipboard.outcome',
    'clinic_output.targets',
    'clinic_output.request_export',
    'clinic_output.export_status',
)

ADMIN: tuple[str, ...] = ()

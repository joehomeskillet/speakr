#!/usr/bin/env python3
"""Print a read-only JSON snapshot from the clinic container's configured database.

Use CLINIC_MODE=true and SQLALCHEMY_DATABASE_URI from the deployment. This
command does not boot the web app or run its migrations and background tasks.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import case, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.clinic import pools, revisions, workflow
from src.clinic.acceptance_policy import ClinicAcceptancePolicyVersion, SCOPE_DICTATION
from src.clinic.audio_revisions import ClinicAudioSessionState
from src.clinic.config import clinic_mode_enabled
from src.clinic.context_grants import ClinicContextGrantUse
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.legacy_backfill import ORIGIN as BACKFILL_ORIGIN, ORIGIN_REF_PREFIXES
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal,
    ClinicReviewLease, ClinicTranscriptRevision,
)
from src.clinic.worklist_entries import LEAD_ROLE, stamp
from src.models import AuditEvent, GroupMembership, ProcessingJob, User


def _rows(connection, query):
    return [dict(row) for row in connection.execute(query).mappings()]


def _roles(connection):
    account, author, staff = ClinicAccountPolicy, pools.ClinicAuthorPool, pools.ClinicStaffPool
    member, route = pools.ClinicAuthorPoolMember, pools.ClinicPoolRoute
    grant, grant_author = pools.ClinicVisibilityGrant, pools.ClinicGrantAuthor
    membership = GroupMembership
    now = datetime.now(timezone.utc)

    def pool_rows(model):
        rows = connection.execute(
            select(model.id, model.active, model.valid_from, model.valid_to).order_by(model.id))
        return [dict(pool_id=row.id, active=row.active is not False,
                     valid_from=stamp(row.valid_from), valid_to=stamp(row.valid_to),
                     routable=pools.is_routable(row, now)) for row in rows]

    grants = _rows(connection, select(grant.id.label("grant_id"), grant.user_id, grant.effect,
                                     grant.scope, grant.pool_id, grant.actions).order_by(grant.id))
    for row in grants:
        if row["actions"] is not None:
            stored = row["actions"].split(",")
            row["actions"] = [action for action in stored if action in pools.GRANTABLE_ACTIONS]

    is_lead = membership.role == LEAD_ROLE
    return dict(
        accounts=_rows(connection, select(User.id.label("user_id"), User.is_admin, account.status,
                                          account.is_identity_admin)
                       .outerjoin(account, account.user_id == User.id).order_by(User.id)),
        author_pools=pool_rows(author),
        staff_pools=pool_rows(staff),
        author_memberships=_rows(connection, select(member.pool_id, member.user_id)
                                 .order_by(member.pool_id, member.user_id)),
        staff_memberships=_rows(connection, select(
            staff.id.label("pool_id"), membership.user_id,
            case((membership.role.in_((LEAD_ROLE, "member")), membership.role),
                 else_="unknown").label("role"),
            case((is_lead, True), else_=False).label("is_lead"),
        ).join(membership, membership.group_id == staff.group_id).order_by(staff.id, membership.user_id)),
        pool_routes=_rows(connection, select(route.author_pool_id, route.staff_pool_id)
                          .order_by(route.author_pool_id, route.staff_pool_id)),
        grants=grants,
        grant_authors=_rows(connection, select(grant_author.grant_id, grant_author.author_user_id)
                            .order_by(grant_author.grant_id, grant_author.author_user_id)),
    )


def _dictations(connection, acceptance_versions):
    acceptance = {row["subject_id"]: row for row in acceptance_versions if row["scope"] == SCOPE_DICTATION}
    transcript = ClinicTranscriptRevision
    asr_ids, human_counts = defaultdict(list), Counter()
    for dictation_id, revision_id, kind in connection.execute(
        select(transcript.dictation_id, transcript.id, transcript.kind)
        .order_by(transcript.dictation_id, transcript.created_at.desc().nulls_last(), transcript.id)
    ):
        if kind == revisions.KIND_ASR:
            asr_ids[dictation_id].append(revision_id)
        elif kind == revisions.KIND_HUMAN:
            human_counts[dictation_id] += 1

    context, proof = ClinicContextRevision, ClinicContextGrantUse
    child = context.__table__.alias()
    superseded = select(child.c.id).where(child.c.dictation_id == context.dictation_id,
                                         child.c.parent_revision_id == context.id).exists()
    heads = defaultdict(list)
    for dictation_id, revision_id, grant_bound in connection.execute(
        select(context.dictation_id, context.id, proof.revision_id.is_not(None))
        .outerjoin(proof, proof.revision_id == context.id).where(~superseded)
    ):
        heads[dictation_id].append((revision_id, grant_bound))

    d, flow = ClinicDictation, workflow.ClinicWorkflowDictation
    audio, lease = ClinicAudioSessionState, ClinicReviewLease
    has_audio = select(ClinicAudioRevision.id).where(ClinicAudioRevision.dictation_id == d.id).exists()
    rows = _rows(connection, select(
        d.id.label("dictation_id"), d.recording_id, d.author_user_id, d.pool_id, flow.status,
        flow.assignee_user_id, flow.acceptance_required, has_audio.label("has_audio_revision"),
        audio.current_revision_id.label("audio_revision_id"),
        lease.holder_user_id.label("lease_holder_user_id"), lease.generation.label("lease_generation"),
        lease.expires_at.label("lease_expires_at"),
    ).outerjoin(flow, flow.id == d.id).outerjoin(audio, audio.dictation_id == d.id)
        .outerjoin(lease, lease.dictation_id == d.id).order_by(d.id))
    for row in rows:
        dictation_id = row["dictation_id"]
        candidates = heads.get(dictation_id, [])
        revision_id, bound = candidates[0] if len(candidates) == 1 else (None, False)
        context_state = "missing"
        if len(candidates) > 1:
            context_state = "conflict"
        elif revision_id is not None:
            context_state = "grant_bound" if bound else "unbound"
        version = acceptance.get(dictation_id, {})
        row.update(
            asr_revision_ids=asr_ids.get(dictation_id, []), human_revision_count=human_counts[dictation_id],
            context_revision_id=revision_id, context_state=context_state,
            acceptance_version=version.get("version"), policy_requires_acceptance=version.get("required"),
            lease_expires_at=stamp(row["lease_expires_at"]),
        )
    return rows


def _source_recording_id(receipt, reference):
    # File imports can store a filename in origin_ref. Only decode internal recording links.
    try:
        origin = json.loads(receipt).get("origin")
    except (ValueError, AttributeError):
        return None
    if origin in ("web-upload", "web-recorder", BACKFILL_ORIGIN):
        for prefix in ORIGIN_REF_PREFIXES:
            if reference.startswith(prefix):
                value, separator, _ = reference[len(prefix):].partition(":")
                if separator and value.isascii() and value.isdecimal():
                    return int(value)
    return None


def _recent_originals(connection):
    original = ClinicOriginal
    rows = connection.execute(select(original.dictation_id, original.id, original.receipt, original.origin_ref)
                              .order_by(original.created_at.desc(), original.id).limit(10))
    return [dict(dictation_id=did, original_version_id=oid, source_recording_id=_source_recording_id(receipt, ref))
            for did, oid, receipt, ref in rows]


def _snapshot(connection):
    policy, audit, job = ClinicAcceptancePolicyVersion, AuditEvent, ProcessingJob
    versions = _rows(connection, select(policy.scope, policy.subject_id, policy.version, policy.required)
                     .order_by(policy.scope, policy.subject_id, policy.version))
    count = func.count().label("count")
    return dict(
        **_roles(connection),
        dictations=_dictations(connection, versions),
        acceptance_versions=versions,
        jobs=_rows(connection, select(job.job_type, job.status, count)
                   .group_by(job.job_type, job.status).order_by(job.job_type, job.status)),
        content_reads=_rows(connection, select(audit.recording_id, audit.actor_user_id, count)
                            .where(audit.event == "recording.content.read")
                            .group_by(audit.recording_id, audit.actor_user_id)
                            .order_by(count.desc(), audit.recording_id, audit.actor_user_id).limit(20)),
        recent_originals=_recent_originals(connection),
    )


def main() -> int:
    if not clinic_mode_enabled() or not os.environ.get("SQLALCHEMY_DATABASE_URI"):
        print("Set CLINIC_MODE and SQLALCHEMY_DATABASE_URI for the clinic database.", file=sys.stderr)
        return 2

    try:
        url = make_url(os.environ["SQLALCHEMY_DATABASE_URI"])
        backend = url.get_backend_name()
        if backend == "sqlite":
            path = Path(url.database or "")
            if not path.is_absolute():
                print("Configure an absolute SQLite database path.", file=sys.stderr)
                return 2
            url = url.set(database=path.as_uri()).update_query_dict({"mode": "ro", "uri": "true"})
        elif backend != "postgresql":
            print("The report requires PostgreSQL or a SQLite database file.", file=sys.stderr)
            return 2
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                if backend == "postgresql":
                    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                else:
                    connection.exec_driver_sql("BEGIN")
                report = _snapshot(connection)
        finally:
            engine.dispose()
    except (SQLAlchemyError, ImportError, ValueError):
        print("Could not read the clinic database. Check the database configuration and schema.", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

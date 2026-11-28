# Author pools and writing teams

[Deutsch](../de/admin-guide/group-management.md)

The clinic fork uses separate **Autorenpools** (author pools) and **Schreibkräfte-Pools** (writing teams). Public and internal shares, folders and the upstream sharing-oriented group interface are not offered. An existing upstream group does not automatically become a clinic pool.

## Pool types and routing

| Pool | Members | Purpose |
| --- | --- | --- |
| **Autorenpool** | Dictating authors | Shared author resources and the author-pool scope attached to a dictation |
| **Schreibkräfte-Pool** | Writing staff | Responsibility for processing dictations from linked author pools |

The two kinds remain separate even when their names match. A staff pool reuses an upstream group and its memberships; an author pool has its own membership table. A link routes an author pool to a staff pool. Current members of linked, active staff pools become assignment candidates.

An author needs an active author pool within its validity window to create a clinic dictation through recording or upload. Otherwise the upload is refused with `clinic_no_author_pool`. Keep each author in exactly one author pool. The UI states that rule, but the membership service does not reject multiple memberships; there is no author-pool picker when creating a dictation.

Staff-pool membership plus a routing link grants **Ansehen** (view) on that author pool's dictations. It does not grant playback, editing or release. A right to view an object also does not imply that every such object appears in the worklist: ordinary staff see their own and assigned dictations; leads additionally see unassigned, sent dictations of the author pools routed to their teams.

## Administration pages

An active site administrator manages pools under **Verwaltung → Konten und Pools**. Identity-administration rights alone and pool leadership do not grant pool administration.

### Schreibkräfte-Pools

Open `/admin#/schreibkraefte-pools`.

1. Choose **Pool hinzufügen** to create a writing team with a unique name.
2. Open **Schreibkräfte-Pool bearbeiten**. Add active accounts through **Benutzer nach Name suchen** and **Hinzufügen**.
3. Set **Leitung** next to a member who should coordinate assignments. Removing a membership requires confirmation.
4. Use **Umbenennen**, **Gültigkeit** and **Gültigkeit speichern** for the team's name and validity window.
5. Review **Verwendung** before **Deaktivieren**: open dictations, members and linked author pools. **Reaktivieren** makes the team active again.

A team with no members is shown as unstaffed. A pool outside its validity window is shown as **Aktiv, aktuell ausserhalb der Gültigkeit**.

### Diktierende und Autorenpools

Open `/admin#/diktierende`. The table lists authors, their author pools, their own writing-team memberships, dictation role and account status. The writing-team column describes personal memberships, not routing links.

Expand **Autorenpools und Schreibkräfte-Pools** to use **Pools verwalten**. Add author-pool members by user ID or remove a membership after confirmation. Each save sends the complete member list and its last-read revision. Reload after a conflict. Membership changes are committed one at a time; if a save fails midway, inspect the current membership list before repeating it.

Author-pool creation, renaming, deactivation, validity and routing links currently have API endpoints but no corresponding author-pool controls here. **Benutzer und Rollen → Poolbezüge** is read-only.

## API-only pool operations

These are clinic browser-session endpoints, not the removed general API v1. All writes require an authorized session and its CSRF token in `X-CSRF-Token`. Do not supply an actor or role in the request body; the server derives authority from the session.

| Method and path, after `/api/clinic/v1` | Body or purpose |
| --- | --- |
| `POST /pool-admin/pools` | `{"kind":"author","name":"<name>"}`; `staff` creates a writing team |
| `PUT /pool-admin/pools/<kind>/<pool_id>/name` | `{"name":"<name>"}` |
| `PUT /pool-admin/pools/<kind>/<pool_id>/active` | `{"active":false}` to deactivate, `true` to reactivate |
| `PUT /pool-admin/pools/<kind>/<pool_id>/validity` | `valid_from` and `valid_to`: ISO 8601 timestamps with offsets or `null` |
| `POST /pool-admin/pools/author/<pool_id>/links` | `{"staff_pool_id":"<id>"}` |
| `GET /pool-admin/pools/<kind>/<pool_id>/usage` | Counts of members, open dictations and links |
| `GET /pools` | Membership lists and their current revision |
| `PUT /pools/<kind>/<pool_id>` | `expected_revision`, `name`, complete `member_user_ids` list |

`<kind>` is `author` or `staff`. Pool names contain 1–100 characters, must have no leading or trailing spaces and must be unique in their namespace; staff names must also be free among upstream groups. The validity start must precede the end. A future pool cannot be linked before it becomes routable. There is no link-removal route.

## Deactivation and leadership

Pools are deactivated, never deleted. Deactivation and expired validity prevent new routing through the pool. They retain dictations and history, do not redistribute open work and do not change the author. Existing read access from a staff-pool link remains; deactivation is not a substitute for revoking access or membership.

A lead can assign or reassign sent dictations of linked author pools while the writing team is routable. The lead needs view access and must have no matching **Neu zuordnen** deny. An active site administrator may assign without the lead role or a view grant, but the same reassignment deny still applies. Recipients must belong to linked, routable writing teams. Assignment grants no additional content permissions.

The lead role does not allow pool creation, renaming, membership changes, role changes, clinical-permission administration or access to `/admin`. Usage counts for the led pool are available through the pool API. Assignment changes require a mandatory audit record; an audit-storage failure refuses the change.

## Clinical permissions

Manage **Fachrechte** under **Benutzer und Rollen**. Choose a user, effect (**Erlauben** or **Verweigern**), scope (**Eigene Diktate**, an author **Pool**, or **Alle Diktate**) and explicit actions. Only an active site administrator can change these entries. **Entziehen** revokes an entry. A stale revision returns a conflict; reload before repeating the change.

| Account or relationship | Default dictation access |
| --- | --- |
| Author on own dictation | View, play and edit |
| Member of a writing team linked to the author pool | View only |
| Pool lead | Same content access as a member, plus assignment authority |
| Active site administrator | View, play and read corrections for clinic dictations |
| Identity-administration flag alone | No additional dictation access |

Matching denies override ownership, staff links and explicit allows. **Exception:** the active site administrator's view, play and correction-read access is checked before deny entries. That exception does not grant editing, export, release or original-audio download.

The preview **Wirksames Ergebnis nach dem Speichern** compares action entries without object scope, ownership, staff links or administrator access. It is not a complete permission decision for a particular dictation. Test the intended action and an excluded scope with synthetic material.

Pool-resource permissions also differ from dictation permissions. An edit or release allow for an author pool can authorize decisions on that pool's vocabulary proposals; editing its word list additionally requires author-pool membership. Pool commands and templates use membership checks. Do not treat a dictation grant as a complete inventory of resource authority.

## Release, acceptance and original audio

**Freigeben** is an explicit permission, including for authors and site administrators. Submission for acceptance needs editing and release rights; acceptance, rejection and reopening need release rights. Assignment through **Zuweisen** follows the separate lead/administrator rules above.

The **Abnahme** page sets an acceptance policy for a selected dictation: whether acceptance is required and which active users may accept. Only an active site administrator can publish the policy. Without a published policy, acceptance is not required. A listed approver still needs the release permission. Final release is distinct from acceptance and requires the current revisions. The KIS integration is read-only: the KIS opens Speakr with a case (Fall) ID as URL context, and Speakr shows only dictations linked to that case that the user may already see. Nothing is delivered or written back to the KIS (no document delivery, no receipts, no reissue or addendum). A simulation is not evidence of a working clinical integration.

**Originalaudio exportieren** (`audio_export`) is separate from document **Exportieren**. Neither own defaults nor administrator playback access include original-audio download. No mail handoff or mail attachment is offered in this fork. See [Privacy](privacy.md) and [Retention](retention.md) for data handling.

## History and common problems

Pool, membership, routing, lead and grant changes are recorded in `clinic_pool_history`, separately from the system audit log. The application treats this history as append-only. The administration has no history viewer for it. Assignment and workflow actions use audit events; see [Audit log](audit-log.md).

- **Recording cannot become a dictation:** check the author's active, currently valid author-pool membership.
- **Assigned dictation cannot be played or edited:** grant the required action within the appropriate author-pool scope; assignment alone is insufficient.
- **Assignment offers nobody:** check routing links, writing-team validity and active members.
- **Lead cannot assign:** check leadership, view access and reassignment denies.
- **Administration page missing:** inspect **Für dieses Konto nicht freigegeben** and the account's role.

See [User management](user-management.md) for accounts and [System statistics](statistics.md) for the separate statistics-grant limitation.

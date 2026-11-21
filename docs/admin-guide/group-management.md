---
layout: default
title: Group Management
parent: Admin Guide
nav_order: 6
---

# Group Management: Author Pools and Staff Pools
<small>[Deutsch](../de/admin-guide/group-management.md)</small>

In upstream Speakr, this page covers groups (teams), group tags, group folders and group admins. In the clinic build with `CLINIC_MODE` on, that group administration is switched off. Two separate kinds of pools and explicit per-user rights (**Fachrechte**, professional rights) take its place. This page explains pools, pool leads and rights, where they are managed, who may change them and what is recorded.

`CLINIC_MODE` counts as on for `1`, `true`, `yes` or `on` and is read on every request. With it off, none of the rules on this page apply and upstream behaviour is unchanged.

## Status overview

| Function | Status |
|---|---|
| Upstream groups, teams, group tags, group folders, the **Groups** tab and `/group-management` | Upstream only |
| **Autorenpools** (author pools) and **Schreibkräfte-Pools** (staff pools) | Available. Creating, renaming, deactivating and linking author pools works only through the API (no UI control). |
| **Leitung** (pool lead) | Available |
| Fachrechte with allow and deny entries | Available |
| **Abnahme** (acceptance policy) | Available. Note: no acceptance is required until an active site administrator publishes a policy for a dictation. |
| **Endgültig freigeben** (final release; in **Prüfen und Freigeben**: **Fertigstellen**) | Available; needs a trusted KIS context. KIS: Simulated (only with `CLINIC_PROFILE=staging`, synthetic MockKIS, no KIS evidence); otherwise the adapter is disabled and final release is refused. |
| Right **Originalaudio exportieren** (export original audio) | Available as a right. A local mail draft with the audio attached is an Open gap (WG-EXP-009). |

## What remains of upstream group administration

- **Admin UI.** In clinic mode, `/admin` shows the clinic **Verwaltung** (administration) navigation instead of the upstream tab strip. The upstream tabs User management, Default prompts, Vector store and Groups are not rendered.
- **Routes.** The upstream team endpoints (`groups.*`), group tags (`tags.create_group_tag`, `tags.get_group_tags`), group folders (`folders.create_group_folder`, `folders.get_group_folders`) and the page `/group-management` belong to the clinic feature `internal_sharing`. The contract file `clinic-spec/config/dev.defaults.json` does not name this feature, and a feature it does not set to `true` is locked. These routes answer `403` with `clinic_feature_disabled`. Even with the lock lifted, the route guard of clinic mode refuses them (`clinic_feature_unsupported`), because they name no recording it could check.
- **Group creation rights.** The checkbox **Gruppen erstellen** (create groups; upstream label "Can create groups") in the account editor appears only while `internal_sharing` is unlocked and never for an account with **Administrationsrolle**, so it is hidden. The upstream setting `permission_create_groups` has no effect on group functions, because no group route is reachable. In the mode `selected` or `everyone` it only shows the menu entry **Groups** (see below). Its default is `admins`; keep it there.
- **Upstream environment variables.** `ENABLE_INTERNAL_SHARING`, `SHOW_USERNAMES_IN_UI` and `USER_SEARCH_SCOPE` are upstream only: the code of this build does not read them.
- **Group admins.** A user with the upstream group-admin role who is not a site administrator gets `403` on `/admin`.
- **Menu entry Groups.** Pool leads without **Administrationsrolle** see the upstream entry **Groups** (German UI: **Gruppen**) in the account menu, because they hold the upstream group-admin role. It leads to `/group-management`, which answers `403` `clinic_feature_disabled`. The same applies to users whom `permission_create_groups` in the mode `selected` or `everyone` allows to create groups.
- **Reused data model.** Each staff pool is stored as exactly one upstream group row. Its typists are that group's membership rows, and a pool lead holds the upstream group-admin role in it. A group without a staff pool row is not a pool. A new staff pool cannot take the name of any existing group.

Upstream reference: [Group Management, upstream Speakr documentation](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/group-management.md). It describes upstream behaviour only and is not an instruction for the clinic build.

## Two kinds of pools

The clinic spec requires author resource pools and writing pools to stay separate. They live in two tables, so a pool cannot change its kind.

| | **Autorenpool** (author pool) | **Schreibkräfte-Pool** (staff pool) |
|---|---|---|
| Table | `clinic_author_pool` | `clinic_staff_pool`, one upstream group each |
| Members | Authors (**Diktierende**, dictating persons) | Typists (Schreibkräfte) |
| Purpose | Groups authors and their shared resources (vocabulary and template references); a dictation carries its author pool | Responsibility for transcription and correction work |
| Rights from membership alone | None beyond the author's own defaults | **Ansehen** (view) on dictations of linked author pools |

A **link** connects an author pool to a staff pool. Links decide:

- which typists hold **Ansehen** (view) on the dictations of the author pool. This is a right, not a list entry: **Arbeitsliste** (worklist) and **Aufnahmen** (recordings) show a typist only own and assigned dictations; only a pool lead also sees the unassigned, sent dictations of linked author pools (see Pool leads below);
- who is offered as recipient when a dictation is assigned (current members of the linked, active staff pools);
- which pool leads see the unassigned, sent dictations of the author pool.

Links can be created only through the API, and no route removes a link. To stop routing to a staff pool, deactivate it or limit its validity window (see below).

### Authors need an active author pool

A web recording or upload becomes a dictation only if the author belongs to an active author pool. Otherwise the server refuses it with `403` `clinic_no_author_pool` and the message "Ihr Benutzerkonto ist keinem Autorenpool zugeordnet. Die Aufnahme wurde nicht als Diktat angelegt." (Your account is not assigned to an author pool. The recording was not created as a dictation.)

The page **Diktierende und Autorenpools** states the rule "Ein Benutzer gehört höchstens einem Autorenpool an." (a user belongs to at most one author pool). The server does not enforce it: a second membership is accepted. The code then picks one active pool automatically, and not every code path picks the same one. Keep each author in exactly one author pool.

### Names

A pool name has 1 to 100 characters without leading or trailing spaces. Author pool names are unique among author pools. Staff pool names are unique among all groups. A taken name is refused with `409` `name_taken` ("Dieser Name ist bereits vergeben."). Names are compared exactly (case-sensitive); unlike the upstream group rule, `Sales` and `sales` count as different names.

## Who may manage pools and rights

These roles are separate. None of them implies another.

| Role | Pools, memberships, links, leads | Fachrechte |
|---|---|---|
| Site administrator: **Administrationsrolle** (`User.is_admin`) with an active clinic account | All changes | Read and change |
| Local identity administration: flag **Identitätsadministration** (an additional flag for account administration) | Adds nothing | Adds read access only, through the API (`GET /api/clinic/v1/identity/users/<user_id>/grants`); the flag does not open `/admin` |
| **Leitung** (pool lead) | Assign dictations, read usage counts of the pools they lead | Own list, read only |
| Every other user | Sees only the pools they belong to, and only themselves as member | Own list, read only |

The pool service also admits a holder of a `manage_pools` grant. `manage_pools` is not a grantable action, and neither the UI nor the API can create it. In practice only active site administrators manage pools. `/admin` itself requires the site administrator flag; everyone else gets `403`.

For account administration (**Konten**, the Administrationsrolle and Identitätsadministration flags), see [User Management](user-management.md).

## Where in the UI

All pages are under **Verwaltung › Konten und Pools** (administration, accounts and pools) at `/admin`. The labels are German only. A page whose API refuses the account is withheld and named in the notice "Für dieses Konto nicht freigegeben: …" (not enabled for this account).

### Schreibkräfte-Pools (staff pools)

Deep link `/admin#/schreibkraefte-pools`. The page loads only for an active site administrator. It manages staff pools only.

- **Pool hinzufügen** (add pool) opens **Schreibkräfte-Pool anlegen** (create staff pool).
- Per pool, after **Schreibkräfte-Pool bearbeiten** (edit): **Umbenennen** (rename); **Gültigkeit** (validity) with **Gültig ab**, **Gültig bis** and **Gültigkeit speichern**; **Verwendung** (usage) with **Offene Diktate**, **Mitglieder** and **Verknüpfte Autorenpools** (open dictations, members, linked author pools); **Deaktivieren** or **Reaktivieren**.
- **Mitglieder** (members): add active accounts with **Benutzer nach Name suchen** (search user by name) and **Hinzufügen**. **Mitglied entfernen** asks "Mitgliedschaft wirklich entfernen?" first. The **Leitung** checkbox next to a member sets or removes the pool lead.
- Status labels: **Aktiv**, **Deaktiviert**, **Aktiv, aktuell ausserhalb der Gültigkeit** (active, currently outside its validity).
- A pool without members shows "Keine Mitglieder. Der Pool ist nicht besetzt; neue Diktate an diesen Pool können nicht bearbeitet werden."

### Diktierende und Autorenpools (authors and author pools)

Deep link `/admin#/diktierende`. The table lists every user who belongs to at least one author pool, with the columns **Anzeigename**, **Autorenpools**, **Schreibkräfte-Pools**, **Diktierrolle** and **Kontostatus**. **Diktierrolle** is **Diktierend** or **Diktierend und Schreibkraft**. The column **Schreibkräfte-Pools** lists the staff pools the person is a member of. It does not show links; "Kein Pool zugewiesen" means the person is in no staff pool.

The collapsible section **Autorenpools und Schreibkräfte-Pools** holds the editor **Pools verwalten** (manage pools). It lists **Autorenpools** and **Schreibpools** separately and is the only UI for author pool membership:

- Members appear as `ID <number>`. Add one with **Benutzer-ID des neuen Mitglieds** (user ID of the new member) and **Mitglied hinzufügen**.
- Removal asks "Mitgliedschaft wirklich entfernen?" first.
- Each save sends the complete member list with the revision last read (**Stand**). If any pool or rights change was recorded in between, the save is refused and the editor shows "Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen." (The state is outdated. Reload and repeat the change.)
- A save applies its additions and removals one at a time. If it fails midway, the changes before the failure stay.

### Benutzer und Rollen (users and roles)

Deep link `/admin#/benutzer`. Choose a user under **Benutzer**.

- **Poolbezüge** (pool memberships) lists the user's author pools and staff pools read-only, with the hint *Mitgliedschaften werden unter «Pools verwalten» geändert.*
- **Fachrechte** opens the rights editor described below.

### Not available in the UI

These functions exist only in the API (see the API reference below): creating, renaming, deactivating and setting validity for author pools, creating links, and usage counts of author pools.

## Deactivation, validity and usage

Pools are never deleted. No route deletes a pool.

- A pool that is deactivated, or outside its validity window, is not routable. New links to or from it are refused with `409` `pool_inactive`. Such an author pool is not chosen for new web dictations. Such a staff pool offers no recipients for assignment, and its leads cannot assign through it.
- Existing dictations, the history and existing read access stay. Open dictations are not redistributed, and **Diktiert von** (dictated by) does not change. Members of a deactivated staff pool keep **Ansehen** on dictations of linked author pools.
- The validity window has an optional start and end. The API expects ISO 8601 timestamps with an offset; `null` leaves a side open. The start must lie before the end. A pool whose start lies in the future cannot be linked before that date.
- The usage preview returns counts only: open dictations (of the pool, or for a staff pool of its linked author pools), members and links. The page shows it before a deactivation together with "Neue Zuweisungen an diesen Pool werden abgelehnt. Vorhandene Diktate und der Verlauf bleiben erhalten, offene Diktate werden nicht automatisch umverteilt. Diktiert von bleibt unverändert."
- The columns `active`, `valid_from` and `valid_to` are added to existing pool tables automatically at application start. Existing pools count as active.

## Pool leads (Leitung)

A pool lead is a staff pool member with the upstream group-admin role (stored as `GroupMembership.role` `admin`). Only an active site administrator sets or removes it, with the **Leitung** checkbox. The person must already be a member of the pool.

A pool lead may:

- see in the worklist the unassigned, sent (not draft) dictations of the author pools linked to a staff pool they lead, in addition to their own and their assigned dictations;
- assign or reassign a sent dictation of such an author pool with **Zuweisen** (dialog **Diktat zuweisen**, field **Schreibkraft**). This requires that the staff pool is active and inside its validity, that the lead holds **Ansehen** on the dictation, and that no deny entry for **Neu zuordnen** (reassign) applies. Recipients are the current members of the linked, active staff pools. **Für mich übernehmen** (take over myself) appears when the lead is one of them;
- read the usage counts of a staff pool they lead, or of an author pool linked to it (API only).

A pool lead may not create, rename or deactivate pools, change memberships, set leads, change Fachrechte or open `/admin`. The lead role gives no content rights: view comes from the pool membership, and **Abspielen**, **Bearbeiten**, **Exportieren** and **Freigeben** need Fachrechte.

Each changed assignment writes the audit event `recording.edited` with the field `assignee`. If that row cannot be written, the assignment is refused with `503` `AUDIT_UNAVAILABLE` ("Auditprotokoll nicht verfügbar").

## Fachrechte (professional rights)

### Safe defaults

| Who | Default on a dictation |
|---|---|
| Author, on their own dictations | **Ansehen**, **Abspielen**, **Bearbeiten** (view, play, edit). Export, original-audio export, release, archive and delete need a grant even on own dictations. |
| Member of a staff pool linked to the dictation's author pool | **Ansehen** only |
| Pool lead | Same as a member, plus assignment |
| Active site administrator | **Ansehen** and **Abspielen** of any clinic dictation, and reading its corrections. These are decided before grants and deny entries. Nothing else without a grant. |
| Identity administration only | Nothing |

A typist who should transcribe needs explicit grants, for example **Abspielen** and **Bearbeiten** with scope **Pool** on the author pool. Being the assignee of a dictation grants nothing by itself.

The site administrator's view and play access is described in [User Management](user-management.md), and how it is logged in [Audit Log](audit-log.md): its rows are written only while `audit_log_enabled` is on, and playback rows also need `audit_log_record_views`.

### Editing rights

Rights are edited under **Benutzer und Rollen › Fachrechte** (users and roles, professional rights) in the panel **Berechtigungen** (permissions). The fields **Geltungsbereich** (scope), **Wirkung** (effect) and **Aktionen** (actions), the buttons **Speichern** (save) and **Entziehen** (revoke) and the revision handling are described in [User Management](user-management.md#fachrechte-professional-rights). For pools:

- Scope **Pool** matches the dictations whose author pool is the chosen **Autorenpool**. A staff pool cannot be chosen as scope.
- **Wirksames Ergebnis nach dem Speichern** (effective result after saving) shows, per action, whether the stored entries plus the draft allow or deny it. It ignores scope and pool and does not include own defaults, staff-pool links or administrator access, so it is not the effective right on a given dictation.

Rules enforced by the server:

- Only an active site administrator may change rights. Identity administrators and every other user are refused with `403`.
- The server does not refuse a grant to the administrator's own account.
- A change carries the revision last read (**Stand**), shared by all users. A stale revision gives `409` `REVISION_CONFLICT`.
- A matching deny wins over every allow, over the staff pool link and over ownership. Exception: an active site administrator's **Ansehen** and **Abspielen** (and reading corrections) are decided before grants and deny entries; a deny entry does not remove them.
- A change applies to the next request, because rights are read live.

The server also accepts **Löschen** (delete), **Umbenennen** (rename), **Priorität ändern** (change priority), **Neu zuordnen** (reassign) and **Wieder öffnen** (reopen), which the UI does not offer; the list displays them. **Löschen** is evaluated (deleting also needs **Bearbeiten**). Allow entries for the other four have no effect in this build: renaming and changing priority need **Bearbeiten**, reopening needs **Freigeben**, a metadata reassignment needs **Bearbeiten** and **Freigeben**. Only a deny entry for **Neu zuordnen** is evaluated: it blocks assignment (see Pool leads).

### Effects on shared pool resources

Fachrechte and pool membership also decide who may change the resources that an author pool shares. These checks are separate from the dictation rights above.

| Resource | Who may change it |
|---|---|
| Shared word list of an author pool (**Wortschatz** › **Wörter**, scope **Autorenpool**): import, add, suppress, reactivate | A member of that author pool who also has the **Administrationsrolle**, created the pool, or holds an allow entry with scope **Pool** for that pool or **Alle Diktate** that names **Bearbeiten** or **Freigeben**. Reading the pool list also needs the membership. |
| Decisions on vocabulary proposals for the pool (**Für den Pool übernehmen**, accept for the pool) | The same authority, without the membership requirement. An account with the **Administrationsrolle** may decide every proposal. |
| Pool voice commands and pool templates in **Befehle und Vorlagen** (commands and templates) | Any member of the author pool. There is no lead, administration or Fachrecht check. |
| System templates | Any account with the **Administrationsrolle**. |

- These checks ignore deny entries, and an account whose clinic account is not active is refused.
- The typist example under [Safe defaults](#safe-defaults) (**Bearbeiten** with scope **Pool**) therefore also lets the typist decide the pool's vocabulary proposals, and change its word list once the typist is also a member of the author pool.
- A site administrator who is not a member of an author pool cannot open or change that pool's word list.
- The proposal queue (**Vorschläge**, proposals) stays empty in the released build, because no code creates proposals. See [Local speech recognition](whisperx-setup.md#the-wortschatz-page).

## Release (Freigeben) and acceptance (Abnahme)

Release, acceptance and text export are separate. The action **Freigeben** (release) is held by nobody by default, including authors on their own dictations.

- The final release needs **Freigeben**. Its button is **Endgültig freigeben** (release finally) in the dictation view and the worklist, and **Fertigstellen** (complete) in the view **Prüfen und Freigeben** (review and release). **Zusätzlich abnehmen** (accept additionally), **Zur Korrektur zurückweisen** (return for correction) and **Wieder öffnen** (reopen) also need **Freigeben**. **Zur Abnahme einreichen** (submit for acceptance) needs **Bearbeiten** and **Freigeben**.
- **Wieder öffnen** needs a reason of up to 2000 characters and invalidates the active release; the dictation moves to **Zu korrigieren** (to correct).
- Final release checks every gate and fails closed: the caller's authorization, the current lease and edit lock, a human correction at the transcript head, a trusted KIS context, no open review markers, and acceptance where required. It binds the exact audio, transcript and context revisions and the text hash. It writes the audit event `recording.edited` with the fields `workflow_status` and `workflow_finalize`, then queues the follow-up work as a `ProcessingJob`.
- Trusted context: without `CLINIC_PROFILE=staging` the KIS adapter is disabled, so final release is refused (`untrusted_context`). With `CLINIC_PROFILE=staging` a synthetic MockKIS adapter is used. It is not evidence of a KIS integration and is never trusted under `CLINIC_PROFILE=production`.

The acceptance policy is set per dictation on **Verwaltung › Richtlinien › Abnahme** (`/admin#/abnahme`):

- Enter the **Diktat-ID** (dictation ID) and choose **Richtlinie laden** (load policy).
- Under **Pflichtfreigabe** (mandatory acceptance), the checkbox **Freigabe erforderlich** makes acceptance required. **Berechtigte Freigebende** (authorized approvers) are added by **Benutzer-ID** with **Hinzufügen**. **Speichern** publishes a new version. **Entscheidungen** lists the decisions.
- Only an active site administrator may publish. Other users who may read the dictation get `403`; the refusal is audited as `recording.edited` with outcome `denied` and the field `acceptance_policy`, also while the audit log is off. Users who may not read the dictation get `404`, and no row is written. A published change is audited as `recording.edited` with the field `acceptance_policy` only while `audit_log_enabled` is on.
- Approvers must be existing users with an active clinic account; otherwise `422` `INVALID_APPROVER`. A save based on an outdated version gives `409` `STALE_VERSION`.
- Without a published policy, acceptance is not required.

The page **Abnahme** says **Freigabe** for acceptance. The view **Prüfen und Freigeben** also labels acceptance **Freigeben** (accept) and the return **Zurückweisen** (return); there the final release is **Fertigstellen**. Deciding needs both: the person must be a listed approver and hold the action **Freigeben**. With no approver listed, nobody can accept. A return needs a reason of 3 to 2000 characters; the dictation moves to **Zu korrigieren**, and the current assignee stays assigned and is recorded as the handler of the return.

## Original audio export (Originalaudio exportieren)

**Originalaudio exportieren** (action `audio_export`) is a separate right, distinct from **Exportieren** (text export).

- It is not part of the author's own defaults, and site administrators do not receive it with their view and play access. Grant it explicitly.
- The server checks it on the stock original-file routes: `recordings.get_audio` with `download=true` and `api_v1.download_audio`. The recording data reports it to the browser as `can_download_audio`. No clinic view currently calls an original-audio download.
- The clinic module for hash-verified original output with an unsent `.eml` file (up to 10 MiB) has no caller in the application; only tests use it. Opening a local mail draft with the audio attached stays an Open gap (WG-EXP-009).

Text export (TXT, DOCX, KIS) needs **Exportieren** and exports only the released revision; otherwise `409` `NOT_RELEASED`.

## What is recorded

Pool, membership, link, lead and rights changes are not audit events. They go to the pool history `clinic_pool_history`:

| Change | Pool history entry |
|---|---|
| Pool created, renamed, deactivated, reactivated, validity set | `pool`: `created`, `renamed`, `deactivated`, `reactivated`, `validity_set`, with old and new value (a rename through `PUT /pools/<kind>/<pool_id>` stores only the new name) |
| Member added or removed | `membership`: `added`, `removed` |
| Lead set or removed | `membership`: `role_changed`, with old and new role |
| Link created | `route`: `added` |
| Right added or revoked | `grant`: `added`, `revoked`, with user, effect, scope, pool and actions |

- Each entry records the time and the acting user. The application log also gets the line `clinic pool: <change> <entity> <id> by <user id>`.
- The pool history is append-only in the application: updates and deletes are refused. No database trigger protects it.
- No page or route displays the pool history, and it does not appear in the **System-Auditprotokoll** (system audit log). The revision **Stand** in **Pools verwalten** is the number of its rows.
- A refused rights change writes no audit event. The route guard refuses accounts without **Administrationsrolle** with `403` `forbidden` before the rights endpoint runs. The endpoint logs the warning `clinic grants: change refused for actor … on user …` only for an account with **Administrationsrolle** whose clinic account is not active. Other refusals (missing or wrong CSRF token, invalid content, stale revision, unknown user) are not logged.

These actions write audit events (see [Audit Log](audit-log.md)). Written even while `audit_log_enabled` is off: assignment (`recording.edited`, field `assignee`), final release (`recording.edited`, fields `workflow_status` and `workflow_finalize`), submit, accept, return and reopen (`recording.edited`, field `workflow_status` plus `workflow_submit`, `workflow_accept`, `workflow_reject` or `workflow_reopen`), and a refused acceptance-policy publication (`recording.edited`, outcome `denied`, field `acceptance_policy`). Written only while `audit_log_enabled` is on: a published acceptance policy (`recording.edited`, field `acceptance_policy`) and each foreign dictation row an active site administrator receives in the worklist (`recording.content.read`, `part` `worklist`).

## API reference

All routes exist only in clinic mode (`404` otherwise) and need a signed-in browser session. Writes need the session's CSRF token in `X-CSRF-Token`. The acting user always comes from the session. The pool routes (`/pools`, `/pool-admin`) refuse a body with `actor`, `actor_id`, `user`, `user_id`, `role`, `roles`, `is_admin` or `created_by`, except the `{"role": …}` body of the lead route. The rights routes accept only `expected_revision`, `effect`, `scope`, `pool_id` and `actions`; the revoke body is exactly `{"expected_revision": <n>}`. The assignment body is exactly `{"assignee_user_id": <id>}`; `null` removes the assignment (the UI does not offer this). The acceptance-policy body is exactly `{"acceptance_required": <bool>, "approver_ids": [<id>, …]}`.

| Method and path (prefix `/api/clinic/v1`) | Purpose | Who |
|---|---|---|
| `GET /pools` | Pools with members and revision | Everyone; non-managers see only their own pools and themselves |
| `PUT /pools/<kind>/<pool_id>` with `expected_revision`, `name`, `member_user_ids` | Set name and full membership; stale revision gives `409` `stale_revision` | Active site administrator |
| `GET /pool-admin/pools` | Pool list with active, validity and routable state | Active site administrator |
| `POST /pool-admin/pools` with `kind` (`author` or `staff`) and `name` | Create a pool (`201`) | Active site administrator |
| `PUT /pool-admin/pools/<kind>/<pool_id>/name` with `name` | Rename | Active site administrator |
| `PUT /pool-admin/pools/<kind>/<pool_id>/active` with `active` | Deactivate or reactivate | Active site administrator |
| `PUT /pool-admin/pools/<kind>/<pool_id>/validity` with `valid_from`, `valid_to` | Set the validity window | Active site administrator |
| `POST /pool-admin/pools/author/<pool_id>/links` with `staff_pool_id` | Link an author pool to a staff pool (`201`; `409` `already_linked`) | Active site administrator |
| `GET /pool-admin/pools/<kind>/<pool_id>/usage` | Usage counts | Active site administrator or a lead of the pool |
| `PUT /pool-admin/pools/staff/<pool_id>/members/<user_id>/role` with `role` (`lead` or `member`) | Set or remove the lead | Active site administrator |
| `GET /identity/users/<user_id>/grants` | Rights list with revision | Active site administrator, active identity administrator, the user |
| `POST /identity/users/<user_id>/grants` with `expected_revision`, `effect`, `scope`, `pool_id`, `actions` | Add an allow or deny entry | Active site administrator |
| `DELETE /identity/users/<user_id>/grants/<grant_id>` with `expected_revision` | Revoke an entry | Active site administrator |
| `GET /dictations/<id>/assignment/candidates`, `POST /dictations/<id>/assignment` | Recipients and assignment; `POST` needs `If-Match` and an `Idempotency-Key` | Site administrator or pool lead |
| `GET`, `PUT /dictations/<id>/acceptance-policy` | Read or publish the acceptance policy; `PUT` needs `If-Match` | Publishing: active site administrator |

## Troubleshooting

- **An author's recording is not created as a dictation.** The author is in no active author pool. Add them under **Pools verwalten**, and check that the pool is active and inside its validity.
- **A typist sees an assigned dictation but cannot play or edit it.** Staff pool membership gives view only, and the assignment grants nothing. Grant **Abspielen** and **Bearbeiten**, for example with scope **Pool** on the author pool.
- **The assignment dialog offers nobody.** The author pool has no link to an active staff pool with members. Create the link through the API and check the staff pool's status and members.
- **A pool lead cannot assign.** Check that the person is marked **Leitung**, that the staff pool is active and linked to the dictation's author pool, and that no deny entry for **Neu zuordnen** applies.
- **A page is missing in Verwaltung.** The page's API refused the account; the notice "Für dieses Konto nicht freigegeben: …" names it.

Related pages: [User Management](user-management.md), [Audit Log](audit-log.md), [Retention](retention.md), [System Settings](system-settings.md). Pool model source (clinic fork): [src/clinic/pools.py](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/pools.py).

Return to [Admin Guide](index.md)

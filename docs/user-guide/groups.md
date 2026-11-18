---
layout: default
title: Group Collaboration
parent: User Guide
nav_order: 6
---

# Group Collaboration

Groups collect users who work together. Group tags and group folders belong to a group and are managed by its admins. Applying a group tag organizes a recording. It does not give other users access to that recording.

## Understanding Groups

A site administrator creates groups, and can allow other users to create them. Once you are a member, you can use that group's tags and folders on your own recordings. Your personal tags stay yours.

## Group Tags

Group tags look like personal tags and show a users icon. Only group admins create or delete them.

### Applying Group Tags

Open a recording, click the tags button, and select a group tag you can use. The tag is stored on your recording. Other members of the group do not receive the recording.

A group admin can move a tag into a group, move it to another group, or make it personal again. Only an admin of the destination group can do that. Every recording that already carries the tag must be one the admin owns.

API v1 accepts the same group change on `PUT /api/v1/tags/{id}` and `PATCH /api/v1/folders/{id}`.

### Group Tag Features

A group tag can carry a retention period that overrides the global deletion rule, a protection flag that stops automatic deletion, and a custom prompt used when the summary is regenerated.

## Group Membership

### Creating a Group

If you are allowed to create groups, **Group Management** appears in your account menu. Enter a name and an optional description. Names are unique across the instance, ignoring case. You become the group admin of a group you create. You can rename it, add and remove members, change roles, and manage its tags and folders. You can delete the group while you remain a group admin of it. Other group admins cannot delete it. A site admin can.

### Joining a Group

Site admins and group admins add members. A new member can use the group's tags on their own recordings. Membership does not open other members' recordings.

### Group Roles

Members use the group's tags and folders on their own recordings. Group admins also add and remove members, change roles, and create, edit, and delete the group's tags and folders. They use a group management page that shows only their groups.

### Leaving a Group

When you leave a group, its tags disappear from your tag selectors. Recordings you own stay yours.

## Group Administration for Group Admins

### Accessing Group Management

Group admins, and users who may create groups, see **Group Management** in the account menu. The page lists only groups you administer.

### Managing Group Members

Enter the exact username or the exact email address of the person, choose Member or Admin, and add them. The page does not list other users. The email address is compared without regard to case.

Changing a role changes who can manage the group. Removing a member removes them from the group. Their recordings stay theirs.

### Creating and Managing Group Tags

Create tags that match how the group organizes content. A tag can set a retention period and a protection flag. Deleting a tag removes it from recordings and does not delete the recordings.

## Groups on the Ask Ambrose page

When library search is enabled, group tags appear in the filters of the [Ask Ambrose](inquire-mode.md) page, so a question can be limited to recordings that carry one group's tag. Ambrose searches the recordings you own.

---

If you are not in a group, ask an administrator. Group admins can read [Group Management](../admin-guide/group-management.md).

Return to [User Guide](index.md) →

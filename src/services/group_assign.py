"""Rules for putting a tag or a folder into a group, one rule for every path.

The web routes and API v1 call these functions, so a tag or folder moves into
a group by the same rule wherever the change is made.
"""


def regroup_name_error(tag, new_group_id, name):
    """'group_tag_name_exists_for_group' when another tag of group new_group_id
    has the name name, else None. A tag made personal cannot clash: the
    database keeps tag names unique per owner."""
    from src.models import Tag
    if not new_group_id:
        return None
    clash = Tag.query.filter(Tag.id != tag.id, Tag.name == name, Tag.group_id == new_group_id).first()
    return 'group_tag_name_exists_for_group' if clash is not None else None


def group_tag_apply_error(recording, group_id, user, has_recording_access):
    """Why user may not put a tag of group group_id on recording, as an error key, or None.

    The rule of the single-tag route: a member of the group, the recording's
    owner or an admin of the group, with edit access to the recording. Every
    path that puts a group tag on a recording asks the same, including
    turning a tag already on it into a group tag (#578, #579).
    """
    from src.models import GroupMembership
    membership = GroupMembership.query.filter_by(group_id=group_id, user_id=user.id).first()
    if not membership:
        return 'tag_access_denied'
    if recording.user_id != user.id and membership.role != 'admin':
        return 'group_tag_apply_restricted'
    if not has_recording_access(recording, user, require_edit=True):
        return 'group_tag_apply_denied'
    return None


def regroup_error(source, new_group_id, user, has_recording_access):
    """Why user may not move a tag or folder into group new_group_id, as an error key, or None.

    Only an admin of the new group may make a tag or folder part of it. A tag
    already on recordings puts a group tag on each, so each must allow that
    by the rule for applying a group tag (#578, #579). A folder with another
    user's recording in it needs the caller's edit access to it (#515). Making
    a tag or folder personal (new_group_id empty) needs no check here.
    """
    from src.models import GroupMembership, Tag
    if not new_group_id:
        return None
    is_tag = isinstance(source, Tag)
    membership = GroupMembership.query.filter_by(group_id=new_group_id, user_id=user.id).first()
    if membership is None or membership.role != 'admin':
        return 'tag_group_assign_admin_only' if is_tag else 'folder_group_assign_admin_only'
    if is_tag:
        for association in source.recording_associations:
            error_key = group_tag_apply_error(association.recording, new_group_id, user, has_recording_access)
            if error_key:
                return error_key
        return None
    for recording in source.recordings:
        if recording.user_id == user.id:
            continue
        if has_recording_access is None or not has_recording_access(recording, user, require_edit=True):
            return 'recording_modify_denied'
    return None

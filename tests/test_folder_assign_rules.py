"""Who may put a recording into a folder, on every move path (#515).

Only the owner of a recording moves it, into a personal folder or into a folder
of a group they belong to. Another user's recording is never moved, and a
personal folder of another user never takes a recording.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import app, db
from src.models import Folder, Group, GroupMembership, Recording, SystemSetting, User

from tests.login_client import _client

app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture
def world():
    with app.app_context():
        SystemSetting.set_setting("enable_folders", "true", setting_type="boolean")
        s = uuid.uuid4().hex[:8]
        owner, other = (User(username=f"fa{k}_{s}", email=f"fa{k}_{s}@local.test", password="x")
                        for k in ("o", "x"))
        db.session.add_all([owner, other])
        db.session.commit()
        group = Group(name=f"fa_{s}")
        db.session.add(group)
        db.session.commit()
        db.session.add(GroupMembership(user_id=owner.id, group_id=group.id, role="admin"))
        group_folder = Folder(name=f"gf_{s}", user_id=owner.id, group_id=group.id)
        personal = Folder(name=f"pf_{s}", user_id=owner.id)
        others_folder = Folder(name=f"of_{s}", user_id=other.id)
        rec = Recording(user_id=owner.id, title="r", audio_path="/tmp/r.mp3", status="COMPLETED")
        db.session.add_all([group_folder, personal, others_folder, rec])
        db.session.commit()
        yield dict(owner=owner, other=other, rec=rec.id, group_folder=group_folder.id,
                   personal=personal.id, others_folder=others_folder.id)
        db.session.rollback()
        Recording.query.filter_by(id=rec.id).delete()
        Folder.query.filter(Folder.user_id.in_([owner.id, other.id])).delete(synchronize_session=False)
        GroupMembership.query.filter_by(group_id=group.id).delete()
        db.session.delete(group)
        User.query.filter(User.id.in_([owner.id, other.id])).delete(synchronize_session=False)
        db.session.commit()


def _move(path, client, rec_id, folder_id):
    if path == "web":
        return client.put(f"/api/recordings/{rec_id}/folder", json={"folder_id": folder_id})
    if path == "bulk":
        return client.post("/api/recordings/bulk/folder",
                           json={"recording_ids": [rec_id], "folder_id": folder_id})
    if path == "v1":
        return client.patch(f"/api/v1/recordings/{rec_id}", json={"folder_id": folder_id})
    return client.patch("/api/v1/recordings/batch",
                        json={"recording_ids": [rec_id], "updates": {"folder_id": folder_id}})


PATHS = ["web", "bulk", "v1", "batch"]


def _assert_not_moved(path, resp):
    if path == "bulk":
        assert resp.status_code in (200, 403, 404)
        if resp.status_code == 200:
            assert resp.get_json()["updated_count"] == 0
    elif path == "batch" and resp.status_code == 200:
        assert resp.get_json()["results"][0]["success"] is False
    else:
        assert resp.status_code in (403, 404), resp.get_data(as_text=True)


@pytest.mark.parametrize("path", PATHS)
def test_owner_moves_into_a_group_and_a_personal_folder(world, path):
    client = _client(world["owner"])
    for folder_id in (world["group_folder"], world["personal"]):
        resp = _move(path, client, world["rec"], folder_id)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        db.session.expire_all()
        assert db.session.get(Recording, world["rec"]).folder_id == folder_id


@pytest.mark.parametrize("path", PATHS)
def test_another_user_cannot_move_the_recording(world, path):
    other_folder = Folder(name="other target", user_id=world["other"].id)
    db.session.add(other_folder)
    db.session.commit()
    resp = _move(path, _client(world["other"]), world["rec"], other_folder.id)
    _assert_not_moved(path, resp)
    db.session.expire_all()
    assert db.session.get(Recording, world["rec"]).folder_id is None


@pytest.mark.parametrize("path", PATHS)
def test_owner_cannot_move_into_another_users_personal_folder(world, path):
    resp = _move(path, _client(world["owner"]), world["rec"], world["others_folder"])
    _assert_not_moved(path, resp)
    db.session.expire_all()
    assert db.session.get(Recording, world["rec"]).folder_id is None

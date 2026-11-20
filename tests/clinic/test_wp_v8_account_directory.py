"""Paginated clinic account management through the mounted, versioned identity API."""
from test_wp_identity_admin_api import admin_client, command  # noqa: F401


def test_search_across_pages_and_versioned_profile_edit(admin_client):
    client, _actor = admin_client
    for index in range(17):
        username = f"accountgrid{index:02}"
        res = command(client, "POST", "/admin/users", username=username,
                      email=f"{username}@example.invalid", name=f"Person {index}")
        assert res.status_code == 201
    users = "/admin/users?search=accountgrid&sort=username"
    first = client.get(users).json
    assert first["pagination"]["total"] == 17
    assert len(first["users"]) == 15
    second = client.get(users + "&page=2").json
    assert [row["username"] for row in second["users"]] == ["accountgrid15", "accountgrid16"]
    row = client.get("/admin/users?search=accountgrid16").json["users"][0]
    assert row["name"] == "Person 16"
    assert row["group_grants_available"] is False
    target = f"/admin/users/{row['id']}"
    changes = dict(expected_version=row["version"], name="Geändert", storage_quota_mb=64,
                   monthly_token_budget=4096, monthly_transcription_budget=93, unlock_login=True)
    assert command(client, "PUT", target, **changes).status_code == 200
    assert command(client, "PUT", target, **changes).json["code"] == "version_conflict"
    result = client.get("/admin/users?search=accountgrid16").json["users"][0]
    for field in ("name", "storage_quota_mb", "monthly_token_budget", "monthly_transcription_budget"):
        assert result[field] == changes[field]
    assert result["version"] == row["version"] + 1

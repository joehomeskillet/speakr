"""PAR-ADM-006: recipient eligibility is checked on load, save and use."""

import pytest

from src.clinic import app_wiring, identity, pools
from src.clinic.last_selection import SelectionStore
from src.clinic.routes_preferences import PREFIX, PreferenceRoutes
from src.clinic.workstation_preferences import WorkstationPreferences

from test_wp_grants_api import _account, _login, _suffix, _url, _user, grants_app, pool_db

DEFAULT_URL = PREFIX + "/recipient-default"
RESOLVE_URL = DEFAULT_URL + "/resolve"


@pytest.fixture
def recipients(grants_app):
    grants_app.register_blueprint(PreferenceRoutes(
        allowed_for=lambda _uid: None,
        author_pool_for=app_wiring.choose_author_pool,
        selections=SelectionStore(),
        workstations=WorkstationPreferences(),
    ).blueprint)
    admin = _account(_user(admin=True))
    alpha, schreib1 = _account(_user()), _account(_user())
    authors = pools.create_author_pool("A-" + _suffix(), actor_id=admin.id)
    staff = pools.create_staff_pool("S-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(authors.id, alpha.id, actor_id=admin.id)
    pools.add_staff_member(staff.id, schreib1.id, actor_id=admin.id)
    pools.link_staff_pool(authors.id, staff.id, actor_id=admin.id)
    client, headers = _login(grants_app, alpha)
    return grants_app, admin, alpha, schreib1, staff, client, headers


def test_disabling_typist_removes_option_and_rejects_saved_default(recipients):
    _, admin, _, schreib1, _, client, headers = recipients
    person = {"mode": "person", "target_ids": [str(schreib1.id)]}
    assert client.get(DEFAULT_URL).json["overrides"]["persons"] == person["target_ids"]
    assert client.put(DEFAULT_URL, headers=headers, json=dict(person, expected_version=0)).status_code == 200
    assert client.post(RESOLVE_URL, headers=headers, json={}).json["recipient_user_ids"] == [schreib1.id]

    assert identity.set_status(schreib1.id, "disabled", actor_id=admin.id).status == "disabled"

    current = client.get(DEFAULT_URL).json
    assert current["overrides"]["persons"] == []
    assert (current["version"], current["target_ids"]) == (1, person["target_ids"])
    denied = client.post(RESOLVE_URL, headers=headers, json={"expected_version": 1})
    assert (denied.status_code, denied.json["code"]) == (403, "recipient_not_eligible")
    denied = client.post(RESOLVE_URL, headers=headers, json={"override": person})
    assert (denied.status_code, denied.json["code"]) == (403, "recipient_not_eligible")
    denied = client.put(DEFAULT_URL, headers=headers, json=dict(person, expected_version=1))
    assert (denied.status_code, denied.json["code"]) == (403, "recipient_not_eligible")
    assert client.get(DEFAULT_URL).json == current


@pytest.mark.parametrize("mode", ["staff_pool", "all_eligible"])
def test_aggregate_routing_excludes_inactive_accounts(recipients, mode):
    _, admin, _, schreib1, staff, client, headers = recipients
    pending = _account(_user(), status="pending_activation")
    legacy = _user()
    for member in (pending, legacy):
        pools.add_staff_member(staff.id, member.id, actor_id=admin.id)
    identity.set_status(schreib1.id, "disabled", actor_id=admin.id)

    override = {"mode": mode, "target_ids": [staff.id] if mode == "staff_pool" else []}
    resolved = client.post(RESOLVE_URL, headers=headers, json={"override": override})
    assert resolved.status_code == 200
    assert resolved.json["recipient_user_ids"] == [legacy.id]


def test_deactivated_staff_pool_cannot_be_saved_or_resolved(recipients):
    _, admin, _, _, staff, client, headers = recipients
    default = {"mode": "staff_pool", "target_ids": [staff.id]}
    saved = client.put(DEFAULT_URL, headers=headers, json=dict(default, expected_version=0))
    assert (saved.status_code, saved.json["version"]) == (200, 1)
    pools.set_pool_active("staff", staff.id, False, actor_id=admin.id)

    assert client.get(DEFAULT_URL).json["overrides"]["staff_pools"] == []
    denied = client.post(RESOLVE_URL, headers=headers, json={"expected_version": 1})
    assert (denied.status_code, denied.json["code"]) == (403, "recipient_not_eligible")
    denied = client.put(DEFAULT_URL, headers=headers, json=dict(default, expected_version=1))
    assert (denied.status_code, denied.json["code"]) == (403, "recipient_not_eligible")
    assert client.get(DEFAULT_URL).json["version"] == 1


def test_target_mapping_cannot_bypass_scope_check(recipients):
    _, _, _, _, _, client, headers = recipients
    outsider = _user()
    denied = client.put(DEFAULT_URL, headers=headers, json={
        "mode": "person", "target_ids": {str(outsider.id): True}, "expected_version": 0,
    })
    assert (denied.status_code, denied.json["code"]) == (400, "invalid_target")
    assert client.get(DEFAULT_URL).json["version"] == 0


@pytest.mark.parametrize("mode", ["all_eligible", "self_correction"])
def test_staff_pools_ceiling_blocks_other_saved_routing_modes(recipients, mode):
    app, admin, alpha, _, staff, client, headers = recipients
    assert client.put(DEFAULT_URL, headers=headers, json={"mode": mode, "expected_version": 0}).status_code == 200
    admin_client, admin_headers = _login(app, admin)
    grant = admin_client.put(_url(alpha), headers=admin_headers, json={
        "recipient_choice": "staff_pools_only", "expected_revision": 0,
    })
    assert grant.status_code == 200

    denied = client.post(RESOLVE_URL, headers=headers, json={"expected_version": 1})
    assert (denied.status_code, denied.json["code"]) == (403, "choice_disabled")
    denied = client.put(DEFAULT_URL, headers=headers, json={"mode": mode, "expected_version": 1})
    assert (denied.status_code, denied.json["code"]) == (403, "choice_disabled")
    assert client.get(DEFAULT_URL).json["version"] == 1
    saved = client.put(DEFAULT_URL, headers=headers, json={
        "mode": "staff_pool", "target_ids": [staff.id], "expected_version": 1,
    })
    assert (saved.status_code, saved.json["version"]) == (200, 2)

"""PAR-ADM-006 recipient-choice grants and the personal preferences API."""

import pytest

from src.clinic import app_wiring, pools
from src.clinic.last_selection import SelectionStore
from src.clinic.routes_preferences import PREFIX, PreferenceRoutes
from src.clinic.workstation_preferences import WorkstationPreferences

from test_pools import _suffix, _user, pool_db
from test_wp_grants_api import _account, _login, _url, grants_app

DEFAULT_URL = PREFIX + "/recipient-default"


@pytest.fixture
def recipient_browser(grants_app):
    grants_app.register_blueprint(PreferenceRoutes(
        allowed_for=lambda _uid: None,
        author_pool_for=app_wiring.choose_author_pool,
        selections=SelectionStore(),
        workstations=WorkstationPreferences(),
    ).blueprint)
    admin = _account(_user(admin=True))
    alpha, typist = _user(), _user()
    authors = pools.create_author_pool("A-" + _suffix(), actor_id=admin.id)
    staff = pools.create_staff_pool("S-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(authors.id, alpha.id, actor_id=admin.id)
    pools.add_staff_member(staff.id, typist.id, actor_id=admin.id)
    pools.link_staff_pool(authors.id, staff.id, actor_id=admin.id)
    return _login(grants_app, admin), _login(grants_app, alpha), _url(alpha), typist, staff


@pytest.mark.parametrize(
    ("rule", "override_error"),
    [("default_only", "choice_disabled"), ("staff_pools_only", "person_not_allowed")],
)
def test_admin_rule_limits_alpha_without_preventing_default_edits(recipient_browser, rule, override_error):
    (admin, admin_headers), (alpha, alpha_headers), grants_url, typist, staff = recipient_browser
    body = {"expected_revision": 0, "recipient_choice": rule}
    result = admin.put(grants_url, headers=admin_headers, json=body)
    assert result.status_code == 200
    assert (result.json["revision"], result.json["recipient_choice"]) == (1, rule)
    assert alpha.get(grants_url).json["recipient_choice"] == rule

    current = alpha.get(DEFAULT_URL).json
    assert (current["version"], current["choice"]) == (0, rule)
    assert current["overrides"] == {
        "persons": [], "staff_pools": [staff.id] if rule == "staff_pools_only" else [],
    }
    change = {"expected_version": 0, "mode": "staff_pool", "target_ids": [staff.id]}
    denied = alpha.put(DEFAULT_URL, headers=alpha_headers, json=dict(change, choice="open"))
    assert (denied.status_code, denied.json["code"]) == (403, "choice_disabled")
    assert alpha.get(DEFAULT_URL).json == current

    saved = alpha.put(DEFAULT_URL, headers=alpha_headers, json=change)
    assert saved.status_code == 200
    assert (saved.json["version"], saved.json["choice"]) == (1, rule)
    resolved = alpha.post(DEFAULT_URL + "/resolve", headers=alpha_headers, json={"expected_version": 1})
    assert resolved.status_code == 200
    assert resolved.json["recipient_user_ids"] == [typist.id]
    assert resolved.json["target_ids"] == [staff.id]

    override = {"mode": "person", "target_ids": [str(typist.id)]}
    denied = alpha.post(DEFAULT_URL + "/resolve", headers=alpha_headers, json={"override": override})
    assert (denied.status_code, denied.json["code"]) == (403, override_error)
    pool_override = {"mode": "staff_pool", "target_ids": [staff.id]}
    resolved = alpha.post(DEFAULT_URL + "/resolve", headers=alpha_headers, json={"override": pool_override})
    assert resolved.status_code == (200 if rule == "staff_pools_only" else 403)


def test_grant_update_requires_active_site_admin_and_current_revision(recipient_browser):
    (admin, admin_headers), (alpha, alpha_headers), grants_url, _, _ = recipient_browser
    body = {"expected_revision": 0, "recipient_choice": "staff_pools_only"}
    assert admin.put(grants_url, json=body).status_code == 403
    assert alpha.put(grants_url, headers=alpha_headers, json=body).status_code == 403
    assert admin.put(grants_url, headers=admin_headers, json=dict(body, recipient_choice=[])).status_code == 400
    assert admin.put(grants_url, headers=admin_headers, json=dict(body, actor_id=123)).status_code == 400
    assert admin.put(grants_url, headers=admin_headers, json=body).status_code == 200

    reopen = dict(body, recipient_choice="open")
    assert alpha.put(grants_url, headers=alpha_headers, json=dict(reopen, expected_revision=1)).status_code == 403
    assert admin.put(grants_url, headers=admin_headers, json=reopen).status_code == 409
    assert admin.get(grants_url).json["recipient_choice"] == "staff_pools_only"

    result = admin.put(grants_url, headers=admin_headers, json=dict(reopen, expected_revision=1))
    assert (result.status_code, result.json["revision"], result.json["recipient_choice"]) == (200, 2, "open")
    assert [(row["detail"]["choice"], row["change"]) for row in pools.history()
            if row["entity"] == "recipient_choice"] == [("staff_pools_only", "changed"), ("open", "changed")]


def test_staff_pools_rule_applies_to_existing_and_new_personal_defaults(recipient_browser):
    (admin, admin_headers), (alpha, alpha_headers), grants_url, typist, _ = recipient_browser
    person = {"expected_version": 0, "mode": "person", "target_ids": [str(typist.id)]}
    assert alpha.put(DEFAULT_URL, headers=alpha_headers, json=person).status_code == 200
    rule = {"expected_revision": 0, "recipient_choice": "staff_pools_only"}
    result = admin.put(grants_url, headers=admin_headers, json=rule)
    assert result.status_code == 200

    saved_person = alpha.post(DEFAULT_URL + "/resolve", headers=alpha_headers, json={})
    assert (saved_person.status_code, saved_person.json["code"]) == (403, "person_not_allowed")
    rejected = alpha.put(DEFAULT_URL, headers=alpha_headers, json=dict(person, expected_version=1))
    assert (rejected.status_code, rejected.json["code"]) == (403, "person_not_allowed")
    assert alpha.get(DEFAULT_URL).json["version"] == 1

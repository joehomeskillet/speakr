"""v8-73: training guard hardening and default resolution that skips inactive pools.

Synthetic rows only. Author pool default (web_ingest, app_wiring) and recipient default
(recipient_defaults) must skip a pool that is deactivated or outside valid_from / valid_to.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clinic_support import login
from src.app import app
from src.clinic import app_wiring, pools, recipient_defaults as rd, routes_training, web_ingest
from src.clinic.route_policy import ADMIN, ROUTE_POLICY, RouteRule

from test_pools import _suffix, _user, pool_db as pool_db  # noqa: F401
from test_wp_v8_training_routes import _scene  # noqa: F401
from test_wp_training_manifest import clinic_db  # noqa: F401

P = routes_training.PREFIX
HOUR = timedelta(hours=1)


def _mount():
    if routes_training.blueprint.name not in app.blueprints:
        app.register_blueprint(routes_training.blueprint)
    for endpoint in routes_training.ENDPOINTS:
        ROUTE_POLICY.setdefault(endpoint, RouteRule(ADMIN))


# --- (1) training guard --------------------------------------------------------------------


def test_training_anonymous_is_401_and_browser_session_still_works(clinic_db):  # noqa: F811
    _mount()
    admin = _scene()
    r = login(admin.id).get(P + "/schedule")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert app.test_client().get(P + "/schedule").status_code == 401
    assert app.test_client().post(P + "/runs", json={}).status_code == 401


# --- (2) pools: author pool default ---------------------------------------------------------


def _author_with(*names, actor=None):
    actor = actor or _user()
    author = _user()
    made = {}
    for name in names:
        pool = pools.create_author_pool(name + "-" + _suffix(), actor_id=actor.id)
        pools.add_author_member(pool.id, author.id, actor_id=actor.id)
        made[name] = pool.id
    return actor, author, made


def _expect(author, pool_id):
    assert web_ingest.author_pool_for(author.id) == pool_id
    assert app_wiring.choose_author_pool(author.id) == pool_id


def test_active_author_pool_is_chosen(pool_db):  # noqa: F811
    _actor, author, made = _author_with("A")
    _expect(author, made["A"])


def test_inactive_author_pool_is_skipped(pool_db):  # noqa: F811
    actor, author, made = _author_with("A", "B")
    first = min(made.values(), key=lambda pid: pid)
    pools.set_pool_active("author", first, False, actor_id=actor.id)
    other = next(pid for pid in made.values() if pid != first)
    _expect(author, other)
    pools.set_pool_active("author", other, False, actor_id=actor.id)
    _expect(author, None)


def test_expired_author_pool_is_skipped(pool_db):  # noqa: F811
    actor, author, made = _author_with("A", "B")
    now = datetime.now(timezone.utc)
    expired = made["A"]
    pools.set_pool_validity("author", expired, now - 2 * HOUR, now - HOUR, actor_id=actor.id)
    _expect(author, made["B"])


def test_future_author_pool_is_skipped(pool_db):  # noqa: F811
    actor, author, made = _author_with("A")
    now = datetime.now(timezone.utc)
    pools.set_pool_validity("author", made["A"], now + HOUR, now + 2 * HOUR, actor_id=actor.id)
    _expect(author, None)
    pools.set_pool_validity("author", made["A"], now - HOUR, None, actor_id=actor.id)
    _expect(author, made["A"])


def test_app_wiring_keeps_name_order_among_active_pools(pool_db):  # noqa: F811
    actor, author, made = _author_with("Zeta", "Alpha")
    _expect_wiring = app_wiring.choose_author_pool(author.id)
    assert _expect_wiring == made["Alpha"]
    pools.set_pool_active("author", made["Alpha"], False, actor_id=actor.id)
    assert app_wiring.choose_author_pool(author.id) == made["Zeta"]


# --- (2) pools: recipient default -----------------------------------------------------------


@pytest.fixture
def _rd_tables(pool_db):  # noqa: F811
    rd.create_tables()
    yield


def _world():
    actor = _user()
    author = _user()
    staff_user = _user()
    authors = pools.create_author_pool("Autoren-" + _suffix(), actor_id=actor.id)
    staff = pools.create_staff_pool("Schreib-" + _suffix(), actor_id=actor.id)
    pools.add_author_member(authors.id, author.id, actor_id=actor.id)
    pools.link_staff_pool(authors.id, staff.id, actor_id=actor.id)
    pools.add_staff_member(staff.id, staff_user.id, actor_id=actor.id)
    return actor, author, staff_user, authors, staff


def test_active_staff_pool_is_a_recipient_default(_rd_tables):
    _actor, author, staff_user, authors, staff = _world()
    assert rd.eligible_user_ids(authors.id) == (staff_user.id,)
    rd.set_default(author.id, "all_eligible", actor_id=author.id, expected_version=0)
    sent = rd.submit(author.id, authors.id, actor_id=author.id)
    assert sent.recipient_user_ids == (staff_user.id,)


def test_inactive_staff_pool_is_never_a_recipient_default(_rd_tables):
    actor, author, staff_user, authors, staff = _world()
    rd.set_default(author.id, "staff_pool", actor_id=author.id, expected_version=0,
                   target_ids=[staff.id])
    pools.set_pool_active("staff", staff.id, False, actor_id=actor.id)
    assert rd.eligible_user_ids(authors.id) == ()
    with pytest.raises(rd.RecipientDefaultError):
        rd.submit(author.id, authors.id, actor_id=author.id)


def test_expired_staff_pool_is_never_a_recipient_default(_rd_tables):
    actor, author, _staff_user, authors, staff = _world()
    now = datetime.now(timezone.utc)
    pools.set_pool_validity("staff", staff.id, now - 2 * HOUR, now - HOUR, actor_id=actor.id)
    assert rd.eligible_user_ids(authors.id) == ()


def test_future_staff_pool_is_never_a_recipient_default(_rd_tables):
    actor, author, staff_user, authors, staff = _world()
    now = datetime.now(timezone.utc)
    pools.set_pool_validity("staff", staff.id, now + HOUR, now + 2 * HOUR, actor_id=actor.id)
    assert rd.eligible_user_ids(authors.id) == ()
    pools.set_pool_validity("staff", staff.id, None, None, actor_id=actor.id)
    assert rd.eligible_user_ids(authors.id) == (staff_user.id,)

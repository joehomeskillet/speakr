"""P10 service-level checks against real, disposable SQLite membership data."""

import json

import pytest
from flask import Flask

from src.clinic import pools
from src.clinic.asr_profiles import (
    AsrProfileError,
    create_hotword_set,
    effective_hotwords,
    profile_history,
    set_profile,
)
from src.database import db
from src.models import User


@pytest.fixture
def authors(clinic_on):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        actor = User(username="actor", email="actor@example.invalid", password="x", is_admin=True)
        author = User(username="author", email="author@example.invalid", password="x")
        db.session.add_all([actor, author])
        db.session.commit()
        yield actor.id, author.id


def test_member_gets_only_selected_pool_and_own_terms(authors):
    actor_id, author_id = authors
    pool = pools.create_author_pool("Geteilt", actor_id=actor_id)
    other = pools.create_author_pool("Weitere", actor_id=actor_id)
    for pool_id in (pool.id, other.id):
        pools.add_author_member(pool_id, author_id, actor_id=actor_id)

    shared = create_hotword_set("Geteilt", ["Poolwort", "Form", "Auslassen"],
                                actor_id=actor_id, pool_id=pool.id)
    own = create_hotword_set("Eigen", ["form", "Eigenwort"], actor_id=author_id,
                             owner_user_id=author_id, blocked=["Auslassen"])
    set_profile(author_id, actor_id=actor_id, hotword_set_ids=[own.id, shared.id])

    assert effective_hotwords(author_id, pool.id) == ["Poolwort", "form", "Eigenwort"]
    assert effective_hotwords(author_id, other.id) == ["form", "Eigenwort"]
    assert effective_hotwords(author_id) == ["form", "Eigenwort"]


@pytest.mark.parametrize("pool_kind", ["author", "staff", "unknown"])
def test_target_needs_membership_even_when_actor_is_a_member(authors, pool_kind, monkeypatch):
    actor_id, author_id = authors
    author_pool = pools.create_author_pool("Autoren", actor_id=actor_id)
    staff_pool = pools.create_staff_pool("Schreibkräfte", actor_id=actor_id)
    pools.link_staff_pool(author_pool.id, staff_pool.id, actor_id=actor_id)
    pools.add_staff_member(staff_pool.id, author_id, actor_id=actor_id)
    pools.add_author_member(author_pool.id, actor_id, actor_id=actor_id)
    pools.add_resource(author_pool.id, "vocabulary", "Wortschatz", actor_id=actor_id)
    pool_id = {"author": author_pool.id, "staff": staff_pool.id, "unknown": "missing"}[pool_kind]
    shared = create_hotword_set("Geteilt", ["Poolwort"], actor_id=actor_id, pool_id=pool_id)

    assert pools.resources_for(author_id) == []
    with pytest.raises(AsrProfileError, match="^set_foreign$"):
        set_profile(author_id, actor_id=actor_id, hotword_set_ids=[shared.id])
    assert profile_history(author_id) == []

    # References accepted outside clinic mode remain readable only when authorized.
    monkeypatch.setenv("CLINIC_MODE", "0")
    set_profile(author_id, actor_id=actor_id, hotword_set_ids=[shared.id])
    assert effective_hotwords(author_id, pool_id) == ["Poolwort"]
    monkeypatch.setenv("CLINIC_MODE", "1")
    assert effective_hotwords(author_id, pool_id) == []


def test_revocation_invalidates_saved_version_and_failed_retry_writes_no_history(authors):
    actor_id, author_id = authors
    pool = pools.create_author_pool("Autoren", actor_id=actor_id)
    pools.add_author_member(pool.id, author_id, actor_id=actor_id)
    resource = pools.add_resource(pool.id, "vocabulary", "Wortschatz", actor_id=actor_id)
    shared = create_hotword_set("Geteilt", ["Poolwort"], actor_id=actor_id, pool_id=pool.id)
    own = create_hotword_set("Eigen", ["Eigenwort"], actor_id=author_id, owner_user_id=author_id)
    ids = [shared.id, own.id]
    profile = set_profile(author_id, actor_id=actor_id, hotword_set_ids=ids)
    assert effective_hotwords(author_id, pool.id) == ["Poolwort", "Eigenwort"]
    assert [row["id"] for row in pools.resources_for(author_id)] == [resource.id]

    pools.remove_author_member(pool.id, author_id, actor_id=actor_id)
    assert pools.resources_for(author_id) == []
    assert effective_hotwords(author_id, pool.id) == ["Eigenwort"]
    for _ in range(2):
        with pytest.raises(AsrProfileError, match="^set_foreign$"):
            set_profile(author_id, actor_id=actor_id, hotword_set_ids=ids)
    assert profile.hotword_set_ids == ids
    assert [row.version for row in profile_history(author_id)] == [1]

    pools.add_author_member(pool.id, author_id, actor_id=actor_id)
    assert set_profile(author_id, actor_id=actor_id, hotword_set_ids=ids).version == 1
    assert effective_hotwords(author_id, pool.id) == ["Poolwort", "Eigenwort"]


def test_foreign_author_set_is_rejected_on_write_and_skipped_if_already_stored(authors):
    actor_id, author_id = authors
    foreign = create_hotword_set("Fremd", ["Fremdwort"], actor_id=actor_id, owner_user_id=actor_id)
    with pytest.raises(AsrProfileError, match="^set_foreign$"):
        set_profile(author_id, actor_id=actor_id, hotword_set_ids=[foreign.id])

    profile = set_profile(author_id, actor_id=actor_id)
    profile.hotword_set_ids_json = json.dumps([foreign.id])
    db.session.commit()
    assert effective_hotwords(author_id) == []

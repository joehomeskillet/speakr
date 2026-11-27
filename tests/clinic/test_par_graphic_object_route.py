"""Exercise graphic objects through Flask sessions, the clinic DB and local storage."""

import hashlib
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import generate_csrf
from test_audit_richtext_graphics import png, scopes as scopes

from src.app import app as real_app, load_user, load_user_from_request
from src.clinic.richtext_graphics import MAX_BYTES, GraphicReference, GraphicStore
from src.clinic.routes_richtext_graphics import create_blueprint
from src.database import db
from src.models import User


@pytest.fixture
def web(scopes: list[str], tmp_path: Path) -> Flask:
    # Mount only the blueprint so its own CSRF check, not the app hook, answers.
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic",
                      SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(app)
    login = LoginManager(app)
    login.user_loader(load_user)
    login.request_loader(load_user_from_request)

    @app.get("/test/csrf", defaults={"uid": None})
    @app.get("/test/csrf/<int:uid>")
    def csrf(uid: int | None):
        if uid is not None:
            assert login_user(db.session.get(User, uid))
        return jsonify(token=generate_csrf())

    app.register_blueprint(create_blueprint(GraphicStore(tmp_path)))
    return app


def _collection(dictation_id: str) -> str:
    return f"/api/clinic/v1/dictations/{dictation_id}/graphics"


@pytest.mark.parametrize("csrf_header", ["X-CSRFToken", "X-CSRF-Token"])
def test_graphic_round_trip(web, scopes, world, csrf_header) -> None:
    data = png()
    url = _collection(scopes[0])
    uploader = web.test_client()
    token = uploader.get(f"/test/csrf/{world['user_a']}").json["token"]
    created = uploader.post(url, data=data, content_type="image/png", headers={csrf_header: token})
    assert created.status_code == 201
    reference = GraphicReference(scopes[0], hashlib.sha256(data).hexdigest(), 1, 1, len(data))
    assert created.json == asdict(reference)
    location = urlsplit(created.headers["Location"]).path
    assert location == f"{url}/{reference.object_id}"

    reader = web.test_client()
    reader.get(f"/test/csrf/{world['user_a']}")  # Independent session; no CSRF header on reads.
    for method, body in (("GET", data), ("HEAD", b"")):
        response = reader.open(location, method=method)
        assert response.status_code == 200
        assert response.data == body
        assert response.mimetype == "image/png"
        assert response.cache_control.private and response.cache_control.no_store
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"


def test_graphic_references_are_scoped_and_fail_uniformly(web, scopes, world) -> None:
    url, other_url = map(_collection, scopes)
    owner = web.test_client()
    owner_headers = {"X-CSRFToken": owner.get(f"/test/csrf/{world['user_a']}").json["token"]}
    created = owner.post(url, data=png(), content_type="image/png", headers=owner_headers)
    assert created.status_code == 201
    object_id = created.json["object_id"]
    location = f"{url}/{object_id}"
    other = web.test_client()
    other_headers = {"X-CSRFToken": other.get(f"/test/csrf/{world['user_b']}").json["token"]}

    missing = owner.get(f"{url}/{'0' * 64}")
    assert missing.status_code == 404
    for failure in (
        other.get(location),
        other.get(f"{other_url}/{object_id}"),
        owner.get(f"{_collection('missing-dictation')}/{object_id}"),
        owner.get(f"{url}/invalid-object-id"),
    ):
        assert (failure.status_code, failure.mimetype, failure.data) == (404, missing.mimetype, missing.data)
    assert other.head(location).status_code == 404
    assert other.post(url, data=png(), content_type="image/png", headers=other_headers).status_code == 404


@pytest.mark.parametrize(("media_type", "data", "status"), [
    pytest.param("text/plain", png(), 415, id="wrong-media-type"),
    pytest.param("image/png", b"<svg/>", 415, id="svg-claimed-as-png"),
    pytest.param("image/png", b"https://example.invalid/image.png", 415, id="remote-url"),
    pytest.param("image/png", png().ljust(MAX_BYTES + 1, b"\0"), 413, id="oversized"),
    pytest.param("image/png", png()[:-1], 400, id="truncated-png"),
])
def test_invalid_graphic_uploads_leave_no_objects(web, scopes, world, tmp_path, media_type, data, status) -> None:
    client = web.test_client()
    token = client.get(f"/test/csrf/{world['user_a']}").json["token"]
    response = client.post(_collection(scopes[0]), data=data, content_type=media_type,
                           headers={"X-CSRFToken": token})
    assert response.status_code == status
    assert not list(tmp_path.rglob("*"))


def test_graphic_requires_browser_session_and_csrf_for_writes(web, scopes, world) -> None:
    url = _collection(scopes[0])
    owner = web.test_client()
    token = owner.get(f"/test/csrf/{world['user_a']}").json["token"]
    for headers in ({}, {"X-CSRFToken": "invalid"}):
        assert owner.post(url, data=png(), content_type="image/png", headers=headers).status_code == 403
    created = owner.post(url, data=png(), content_type="image/png", headers={"X-CSRFToken": token})
    assert created.status_code == 201
    location = urlsplit(created.headers["Location"]).path

    anonymous = web.test_client()
    anonymous_csrf = anonymous.get("/test/csrf").json["token"]
    for auth in ({}, {"Authorization": f"Bearer {world['token']}"}):
        assert anonymous.get(location, headers=auth).status_code == 401
        assert anonymous.head(location, headers=auth).status_code == 401
        headers = {**auth, "X-CSRFToken": anonymous_csrf}
        assert anonymous.post(url, data=png(), content_type="image/png", headers=headers).status_code == 401

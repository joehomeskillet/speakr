import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic.routes_worklist import PREFIX, create_blueprint
from src.clinic.worklist_filters import WorklistFilterStore
from src.clinic.worklist_layout import LayoutStore

FILTERS = f'{PREFIX}/filters'
SPEC = {'all': [{'field': 'status', 'op': 'eq', 'value': 'to_write'}], 'any': []}


@pytest.fixture
def app_store(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-filters-crud')
    login = LoginManager(app)
    store = WorklistFilterStore()
    app.register_blueprint(create_blueprint(lambda _actor: [], store, LayoutStore()))

    class Actor(UserMixin):
        def __init__(self, uid):
            self.id = int(uid)

    login.user_loader(Actor)

    @app.get('/synthetic-login/<int:uid>')
    def browser(uid):
        login_user(Actor(uid))
        return {'csrf': generate_csrf()}

    return app, store


def login(client, uid=7):
    return {'X-CSRF-Token': client.get(f'/synthetic-login/{uid}').json['csrf']}


def test_get_one_returns_spec_and_hides_foreign(app_store):
    app, store = app_store
    own = store.create(7, 'Offen', SPEC)
    store.create(8, 'Fremd', SPEC)
    foreign = store.create(8, 'Fremd2', SPEC)
    with app.test_client() as client:
        login(client)
        assert client.get(f'{FILTERS}/{own.id}').json == {
            'id': own.id, 'name': 'Offen', 'version': 1, 'spec': SPEC}
        assert client.get(f'{FILTERS}/{foreign.id}').status_code == 404
        assert client.get(f'{FILTERS}/f999').status_code == 404
        assert set(client.get(FILTERS).json['items'][0]) == {'id', 'name', 'version'}


def test_create_update_delete_round_trip(app_store):
    app, store = app_store
    with app.test_client() as client:
        headers = login(client)
        created = client.post(FILTERS, json={'name': 'Neu', 'spec': SPEC}, headers=headers)
        assert created.status_code == 201
        body = created.json
        assert body == {'id': body['id'], 'name': 'Neu', 'version': 1, 'spec': SPEC}
        new_spec = {'all': [{'field': 'status', 'op': 'eq', 'value': 'done'}], 'any': []}
        updated = client.put(f"{FILTERS}/{body['id']}", json={'name': 'Neu2', 'spec': new_spec},
                             headers=dict(headers, **{'If-Match': '"1"'}))
        assert updated.status_code == 200
        assert updated.json['name'] == 'Neu2' and updated.json['spec'] == new_spec
        assert updated.json['version'] == 3
        assert store.get(7, body['id']).spec == new_spec
        spec_only = client.put(f"{FILTERS}/{body['id']}", json={'spec': SPEC},
                               headers=dict(headers, **{'If-Match': '3'}))
        assert spec_only.json['version'] == 4
        assert client.delete(f"{FILTERS}/{body['id']}", headers=headers).status_code == 400
        gone = client.delete(f"{FILTERS}/{body['id']}", headers=dict(headers, **{'If-Match': '4'}))
        assert gone.status_code == 204
        assert store.list(7) == []
        assert client.get(f"{FILTERS}/{body['id']}").status_code == 404


def test_stale_version_409_returns_authoritative_version(app_store):
    app, store = app_store
    saved = store.create(7, 'Offen', SPEC)
    with app.test_client() as client:
        headers = login(client)
        for call in (
            lambda: client.put(f'{FILTERS}/{saved.id}', json={'spec': {}},
                               headers=dict(headers, **{'If-Match': '5'})),
            lambda: client.delete(f'{FILTERS}/{saved.id}', headers=dict(headers, **{'If-Match': '5'})),
        ):
            response = call()
            assert response.status_code == 409
            assert response.json['code'] == 'stale_version'
            assert (response.json['id'], response.json['version']) == (saved.id, 1)
        assert store.get(7, saved.id).version == 1
        duplicate = client.post(FILTERS, json={'name': 'offen', 'spec': SPEC}, headers=headers)
        assert duplicate.status_code == 409


def test_foreign_user_cannot_modify(app_store):
    app, store = app_store
    store.create(8, 'Fremd', SPEC)
    foreign = store.create(8, 'Fremd2', SPEC)
    with app.test_client() as client:
        headers = login(client)
        put = client.put(f'{FILTERS}/{foreign.id}', json={'name': 'Mein'},
                         headers=dict(headers, **{'If-Match': '1'}))
        delete = client.delete(f'{FILTERS}/{foreign.id}', headers=dict(headers, **{'If-Match': '1'}))
        assert (put.status_code, delete.status_code) == (404, 404)
    assert store.get(8, foreign.id).name == 'Fremd2' and store.get(8, foreign.id).version == 1


def test_csrf_and_payload_validation(app_store):
    app, store = app_store
    saved = store.create(7, 'Offen', SPEC)
    with app.test_client() as client:
        headers = login(client)
        assert client.post(FILTERS, json={'name': 'X', 'spec': SPEC}).status_code == 403
        assert client.put(f'{FILTERS}/{saved.id}', json={'name': 'Y'},
                          headers={'If-Match': '1'}).status_code == 403
        assert client.delete(f'{FILTERS}/{saved.id}', headers={'If-Match': '1'}).status_code == 403
        assert client.post(FILTERS, json={'name': 'X'}, headers=headers).status_code == 400
        bad = {'all': [{'field': 'nope', 'op': 'eq', 'value': 1}]}
        assert client.post(FILTERS, json={'name': 'X', 'spec': bad}, headers=headers).status_code == 400
        assert client.put(f'{FILTERS}/{saved.id}', json={'spec': bad},
                          headers=dict(headers, **{'If-Match': '1'})).status_code == 400
        assert store.get(7, saved.id).version == 1


def test_anonymous_401_and_no_clinic_mode_404(app_store, monkeypatch):
    app, store = app_store
    saved = store.create(7, 'Offen', SPEC)
    with app.test_client() as client:
        assert client.get(f'{FILTERS}/{saved.id}').status_code == 401
        assert client.post(FILTERS, json={'name': 'X', 'spec': SPEC}).status_code == 401
        assert client.put(f'{FILTERS}/{saved.id}', json={'name': 'Y'}).status_code == 401
        assert client.delete(f'{FILTERS}/{saved.id}').status_code == 401
        headers = login(client)
        monkeypatch.delenv('CLINIC_MODE')
        assert client.get(f'{FILTERS}/{saved.id}').status_code == 404
        assert client.post(FILTERS, json={'name': 'X', 'spec': SPEC}, headers=headers).status_code == 404
        assert client.put(f'{FILTERS}/{saved.id}', json={'name': 'Y'},
                          headers=dict(headers, **{'If-Match': '1'})).status_code == 404
        assert client.delete(f'{FILTERS}/{saved.id}',
                             headers=dict(headers, **{'If-Match': '1'})).status_code == 404

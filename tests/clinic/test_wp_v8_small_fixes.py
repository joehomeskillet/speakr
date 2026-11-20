"""v8-76 small fixes: peaks computation failure code and no group-admin link in clinic mode."""

from types import SimpleNamespace

from flask import render_template

from src.app import app
from src.clinic import routes_audio
from src.services.waveform_peaks import PeaksError

from test_wp_v8_peaks_route import client_for, scene, url  # noqa: F401  (scene is a fixture)


def test_peaks_computation_failure_has_its_own_problem_code(scene, monkeypatch):  # noqa: F811
    flask, data, _ = scene

    def broken(path):
        raise PeaksError('synthetic failure')

    monkeypatch.setattr(routes_audio, 'compute_peaks', broken)
    response = client_for(flask, data.author).get(url(data.dictation, data.head))
    assert response.status_code == 500
    assert response.mimetype == 'application/problem+json'
    assert response.json['code'] == 'PEAKS_FAILED'
    assert response.headers['Cache-Control'] == 'no-store'


def test_missing_audio_source_keeps_503_dependency_unavailable(scene):  # noqa: F811
    flask, data, state = scene
    state.fail_sources = True
    response = client_for(flask, data.author).get(url(data.dictation, data.head))
    assert response.status_code == 503
    assert response.json['code'] == 'DEPENDENCY_UNAVAILABLE'


def _render(clinic_mode, is_admin=True):
    user = SimpleNamespace(is_admin=is_admin, is_authenticated=True, id=1)
    with app.test_request_context('/'):
        return render_template('includes/organizer_modals.html', clinic_mode=clinic_mode, current_user=user)


def test_groups_link_hidden_in_clinic_mode():
    html = _render(True)
    assert '/admin#groups' not in html
    assert 'folderManagement.sharingEmptyAdminBefore' in html


def test_groups_link_rendered_when_clinic_mode_is_off():
    html = _render(False)
    assert html.count('href="/admin#groups"') == 2

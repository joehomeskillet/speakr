"""Clinic config: mode switch, contract loaders, feature locks, client profile.

Covers the configuration part of S01 (R-042, R-048, R-050).
"""

import json
import shutil

import pytest

from src.app import app
from src.clinic import config


@pytest.fixture
def clinic(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')


def test_clinic_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv('CLINIC_MODE', raising=False)
    assert config.clinic_mode_enabled() is False
    # Off means nothing is locked: upstream behaviour stays as it is.
    assert config.feature_locked('chat') is False
    assert config.feature_locked('some_unknown_feature') is False
    assert config.setting_ceiling('enable_public_sharing') == (False, None)


@pytest.mark.parametrize('value,expected', [
    ('1', True), ('true', True), ('TRUE', True), ('yes', True), ('on', True),
    ('0', False), ('false', False), ('', False), ('nope', False),
])
def test_clinic_mode_values(monkeypatch, value, expected):
    monkeypatch.setenv('CLINIC_MODE', value)
    assert config.clinic_mode_enabled() is expected


def test_contracts_load():
    defaults = config.dev_defaults()
    assert defaults['clinical_mode'] is True
    assert defaults['features']['public_sharing'] is False
    assert 'managed_web_online' in config.client_capabilities()['profiles']
    assert 'microphone_capture' in config.import_agent_boundary()['denied_operations']


def test_default_client_profile_is_managed_web_online():
    assert config.default_client_profile() == 'managed_web_online'


@pytest.mark.parametrize('feature', [
    'public_sharing', 'chat', 'inquire', 'user_webhooks', 'personal_api_tokens',
    'token_query_parameters', 'auto_deletion', 'email_exports', 'speaker_identification',
])
def test_locked_features_in_clinic_mode(clinic, feature):
    assert config.feature_locked(feature) is True


def test_unknown_feature_is_locked_in_clinic_mode(clinic):
    assert config.feature_locked('never_heard_of_it') is True


def test_feature_enabled_by_the_defaults_is_unlocked(clinic, monkeypatch, tmp_path):
    directory = tmp_path / 'cfg'
    shutil.copytree(config.config_dir(), directory)
    path = directory / config.DEV_DEFAULTS_FILE
    data = json.loads(path.read_text(encoding='utf-8'))
    data['features']['user_webhooks'] = True
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('CLINIC_CONFIG_DIR', str(directory))
    assert config.feature_locked('user_webhooks') is False
    assert config.feature_locked('public_sharing') is True


def test_missing_contract_fails_closed(clinic, monkeypatch, tmp_path):
    monkeypatch.setenv('CLINIC_CONFIG_DIR', str(tmp_path / 'nothing'))
    with pytest.raises(FileNotFoundError):
        config.feature_locked('public_sharing')


def test_unknown_default_profile_is_rejected(monkeypatch, tmp_path):
    directory = tmp_path / 'cfg'
    shutil.copytree(config.config_dir(), directory)
    path = directory / config.DEV_DEFAULTS_FILE
    data = json.loads(path.read_text(encoding='utf-8'))
    data['web_client']['default_profile'] = 'made_up_profile'
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('CLINIC_CONFIG_DIR', str(directory))
    with pytest.raises(ValueError):
        config.default_client_profile()


def test_client_cannot_select_its_profile(clinic):
    """T-058 (header part): a forged channel header, User-Agent or Host changes nothing."""
    headers = {'X-Clinic-Channel': 'managed', 'User-Agent': 'ManagedClinicClient/1.0',
               'X-Forwarded-Host': 'managed.klinik.invalid'}
    with app.test_request_context('/', headers=headers, base_url='http://managed.klinik.invalid'):
        assert config.client_profile() == 'managed_web_online'
    with app.test_request_context('/', headers={'X-Clinic-Channel': 'byod_internal'}):
        assert config.client_profile() == 'managed_web_online'


@pytest.mark.parametrize('key,forced', [
    ('enable_public_sharing', False), ('enable_internal_sharing', False),
    ('api_query_tokens_enabled', False), ('sso_api_tokens_enabled', False),
    ('enable_incognito_mode', False), ('enable_auto_deletion', False),
    ('global_retention_days', 0), ('allow_registration', False),
])
def test_setting_ceilings(clinic, key, forced):
    assert config.setting_ceiling(key) == (True, forced)


def test_setting_without_ceiling_is_untouched(clinic):
    assert config.setting_ceiling('help_center_url') == (False, None)


# --- local text model: chat / inquire / generative summary ----------------------

@pytest.mark.parametrize('url', [
    'http://localhost:11434/v1', 'http://127.0.0.1:8000', 'http://127.5.5.5/v1',
    'http://[::1]:8000/v1', 'http://10.1.2.3/v1', 'https://172.16.0.9/v1',
    'http://172.31.255.255', 'http://192.168.1.20:8080/v1', 'http://ollama:11434/v1',
    'http://llm_server/v1', 'ollama:11434', 'http://[fd12:3456::1]/v1',
])
def test_local_model_urls(monkeypatch, url):
    # service names are local only when on the list (finding 11); name these two
    monkeypatch.setenv('CLINIC_LOCAL_MODEL_HOSTS', 'localhost,ollama,llm_server')
    assert config.is_local_model_url(url) is True


@pytest.mark.parametrize('url', [
    None, '', '   ', 'https://api.openai.com/v1', 'http://llm.example.com', 'http://8.8.8.8/v1',
    'http://172.32.0.1', 'http://172.15.0.1', 'http://192.169.0.1', 'http://11.0.0.1',
    'http://2130706433/v1', 'http://0x7f000001/v1', 'http://10.0.0.1@evil.example.com/v1',
    'http://127.0.0.1.evil.example.com', 'http://localhost.evil.com', 'http://[2001:db8::1]/v1',
    'http://', 'http://:80',
])
def test_non_local_model_urls(url):
    assert config.is_local_model_url(url) is False


@pytest.mark.parametrize('feature', ['chat', 'inquire', 'generative_summary'])
def test_text_model_features_follow_the_model_host(clinic, monkeypatch, feature):
    monkeypatch.setenv('CLINIC_LOCAL_MODEL_HOSTS', 'ollama')
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'https://api.openai.com/v1')
    assert config.feature_locked(feature) is True
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', '')
    assert config.feature_locked(feature) is True
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'http://ollama:11434/v1')
    assert config.feature_locked(feature) is False


def test_local_model_does_not_unlock_other_features(clinic, monkeypatch):
    monkeypatch.setenv('CLINIC_LOCAL_MODEL_HOSTS', 'ollama')
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'http://ollama:11434/v1')
    for feature in ('public_sharing', 'user_webhooks', 'personal_api_tokens', 'incognito_processing'):
        assert config.feature_locked(feature) is True


def test_text_model_features_are_not_locked_outside_clinic_mode(monkeypatch):
    monkeypatch.delenv('CLINIC_MODE', raising=False)
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'https://api.openai.com/v1')
    assert config.feature_locked('chat') is False

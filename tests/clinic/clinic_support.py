"""Helpers shared by the clinic tests."""

import json

from src.app import app
from src.models import SystemSetting

SECRET_MARKER = 'CONFIDENTIAL-FINDING-'


def login(user_id):
    """A test client with a browser session of the user."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
    return client


def set_setting(key, value, setting_type='boolean'):
    with app.app_context():
        SystemSetting.set_setting(key, value, None, setting_type)


def dump(response):
    return json.dumps(response.get_json(silent=True)) + response.get_data(as_text=True)

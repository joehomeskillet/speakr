"""A signed-in Flask test client for tests that keep an app context open.

Moved here unchanged from the removed share tests (it was ``_client`` there).
The client forgets the user cached in ``g`` before every request, so a test
that switches between users inside one app context is served as the right one.
"""

from flask import g
from flask.testing import FlaskClient

from src.app import app


class _Client(FlaskClient):
    def open(self, *args, **kwargs):
        g.pop('_login_user', None)
        return super().open(*args, **kwargs)


def _client(user):
    c = _Client(app, app.response_class)
    with c.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    return c

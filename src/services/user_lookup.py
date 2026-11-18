"""Finding another account by its exact username or email address.

Used for adding a member to a group. No partial match, so no answer tells
whether part of a username or address exists. The email comparison ignores
case.
"""

from src.database import db
from src.models import User


def _exact_matches(identifier):
    """Accounts with exactly this username, then the one with exactly this email address."""
    users = User.query.filter(User.username == identifier).all()
    if '@' in identifier:
        by_email = User.query.filter(db.func.lower(User.email) == identifier.lower()).first()
        if by_email is not None and all(user.id != by_email.id for user in users):
            users.append(by_email)
    return users


def find_user_exactly(identifier):
    """The account with exactly this username, or else this email address.

    The email comparison ignores case. A person whose username or email is
    known can always be named.
    """
    identifier = identifier.strip() if isinstance(identifier, str) else ''
    if not identifier:
        return None
    users = _exact_matches(identifier)
    return users[0] if users else None

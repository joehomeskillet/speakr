"""Local operator commands; app startup can opt in via register_identity_commands."""

import getpass
import sys
import warnings

import click
from flask import Flask
from flask.cli import AppGroup
from sqlalchemy.exc import SQLAlchemyError

from src.clinic import identity
from src.clinic.config import clinic_mode_enabled
from src.database import db


def _require_terminal() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise click.ClickException("Ein interaktives Terminal ohne Ausgabeumleitung ist erforderlich.")


def _password(prompt: str) -> str:
    # getpass can otherwise fall back to visible input even with a TTY.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            raise click.ClickException("Verdeckte Passworteingabe nicht verfügbar.") from None
        except (EOFError, KeyboardInterrupt):
            raise click.Abort() from None


@click.group("clinic-identity", cls=AppGroup)
def identity_commands() -> None:
    """Lokale Identitätsverwaltung an der Serverkonsole."""
    if not clinic_mode_enabled():
        raise click.ClickException("Der Klinikmodus ist deaktiviert.")


@identity_commands.command(epilog="Beispiel: flask clinic-identity bootstrap --username admin --email admin@example.invalid")
@click.option("--username", required=True)
@click.option("--email", required=True)
@click.option("--name")
def bootstrap(username: str, email: str, name: str | None) -> None:
    """Erste lokale Identitätsadministration einrichten."""
    _require_terminal()
    password = _password("Neues Administrationspasswort: ")
    if password != _password("Passwort wiederholen: "):
        raise click.ClickException("Die Passwörter stimmen nicht überein.")
    try:
        created = identity.bootstrap_first_admin(username, email, password, name=name)
    except identity.PasswordPolicyError:
        raise click.ClickException("Das Passwort entspricht nicht der Passwortrichtlinie (15–128 Zeichen, Sperrliste).") from None
    except identity.IdentityConflict:
        raise click.ClickException("Benutzername oder E-Mail-Adresse ist ungültig oder bereits vergeben.") from None
    except (identity.IdentityError, SQLAlchemyError):
        db.session.rollback()
        raise click.ClickException("Die Einrichtung konnte nicht abgeschlossen werden. Bitte erneut versuchen.") from None
    click.echo("Identitätsadministration eingerichtet." if created else "Identitätsadministration bereits eingerichtet.")


@identity_commands.command(epilog="Beispiel: flask clinic-identity recovery --operator admin --user-id 2 --expected-version 1")
@click.option("--operator", required=True, help="Benutzername oder E-Mail der Identitätsadministration.")
@click.option("--user-id", type=click.IntRange(min=1), required=True)
@click.option("--expected-version", type=click.IntRange(min=1), required=True)
def recovery(operator: str, user_id: int, expected_version: int) -> None:
    """Einmalcode für die lokale Aktivierung oder Wiederherstellung ausgeben."""
    _require_terminal()
    password = _password("Passwort der Identitätsadministration: ")
    try:
        auth = identity.authenticate_local(operator, password)
        # Keep the issuer's authorization stable through the recovery commit.
        policy = db.session.get(
            identity.ClinicAccountPolicy, auth.user_id, populate_existing=True, with_for_update=True,
        )
        if (
            not policy or not policy.is_identity_admin or policy.status != "active"
            or policy.login_policy not in identity.LOCAL_POLICIES
            or policy.security_version != auth.security_version
        ):
            raise identity.IdentityDenied()
        issued = identity.issue_recovery(user_id, issued_by=auth.user_id, expected_version=expected_version)
    except (identity.AuthenticationFailed, identity.IdentityDenied):
        raise click.ClickException("Ausgabe eines Einmalcodes nicht erlaubt.") from None
    except identity.VersionConflict:
        raise click.ClickException("Die Kontoversion ist veraltet. Bitte die aktuelle Version prüfen.") from None
    except (identity.IdentityError, SQLAlchemyError):
        db.session.rollback()
        raise click.ClickException("Die Ausgabe ist fehlgeschlagen. Bitte vor einem neuen Versuch die Kontoversion prüfen.") from None
    click.echo(issued.code)


def register_identity_commands(app: Flask) -> None:
    """Register the server-console commands when clinic mode is enabled."""
    if clinic_mode_enabled():
        app.cli.add_command(identity_commands)

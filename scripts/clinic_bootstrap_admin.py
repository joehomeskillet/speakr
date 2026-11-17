#!/usr/bin/env python3
"""Operator console: create the first identity administrator, once.

The initial password is never an argument. It is read from an environment
variable (default CLINIC_BOOTSTRAP_PASSWORD), from stdin (--password-stdin)
or, on a terminal, from a hidden prompt. It is never printed or logged.
A second run changes nothing and exits 0.

Exit codes: 0 created or already initialised, 2 invalid input, 1 other error.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_PASSWORD_ENV = "CLINIC_BOOTSTRAP_PASSWORD"


def _read_password(args, env, stdin):
    if args.password_stdin:
        return stdin.readline().rstrip("\r\n")  # no other trimming
    if env.get(args.password_env):
        return env[args.password_env]
    if stdin.isatty():
        first = getpass.getpass("Initial administrator password: ")
        if getpass.getpass("Repeat password: ") != first:
            return None
        return first
    return None


def run(argv=None, env=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV,
                        help="environment variable holding the password")
    parser.add_argument("--password-stdin", action="store_true",
                        help="read the password from the first line of stdin")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin

    password = _read_password(args, env, stdin)
    if not password:
        print("Error: no password supplied (environment variable, --password-stdin or terminal).",
              file=sys.stderr)
        return 2

    from src.app import app
    from src.clinic import identity

    with app.app_context():
        identity.create_tables()
        try:
            created = identity.bootstrap_first_admin(
                args.username, args.email, password, name=args.name)
        except identity.PasswordPolicyError as exc:
            print(f"Error: password rejected ({exc.code}).", file=sys.stderr)
            return 2
        except identity.IdentityConflict as exc:
            print(f"Error: account data rejected ({exc}).", file=sys.stderr)
            return 2
        except identity.IdentityError:
            print("Error: bootstrap failed.", file=sys.stderr)
            return 1
    if created:
        print(f"First identity administrator '{args.username}' created.")
    else:
        print("Already initialised; nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())

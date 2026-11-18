"""Run one verification command and preserve its combined output verbatim.

Usage: rtk /usr/bin/python3 clinic-evidence/archive-runtime/capture.py NAME COMMAND ...
The command must itself start with rtk. Logs are stored beside this script.
"""

from pathlib import Path
import shlex
import subprocess
import sys


def main():
    if len(sys.argv) < 4 or sys.argv[2] != 'rtk':
        raise SystemExit('Expected a log name and one rtk command')
    name = sys.argv[1]
    if not name.replace('-', '').replace('_', '').isalnum():
        raise SystemExit('Log names may contain letters, digits, hyphens and underscores')
    command = sys.argv[2:]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    directory = Path(__file__).resolve().parent
    (directory / (name + '.log')).write_bytes(result.stdout)
    (directory / (name + '.command.txt')).write_text(shlex.join(command) + '\n')
    (directory / (name + '.exit.txt')).write_text(str(result.returncode) + '\n')
    sys.stdout.buffer.write(result.stdout)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())

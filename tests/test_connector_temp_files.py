"""Temporary files of the transcription connectors.

No connector writes the reply of the transcription service to a file.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
CONNECTORS = ROOT / "src" / "services" / "transcription"


def test_no_connector_writes_a_file():
    """No remaining connector writes temporary files or dumps."""
    writes = re.compile(r"tempfile\.|gettempdir|mkstemp|mkdtemp|open\([^)]*[\x27\"][wax]b?\+?[\x27\"]"
                        r"|\.write_text\(|\.write_bytes\(|json\.dump\(")
    found = {}
    for path in sorted((CONNECTORS / "connectors").glob("*.py")):
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                 if writes.search(line) and not line.strip().startswith("#")]
        if lines:
            found[path.name] = lines
    assert set(found) == set(), found

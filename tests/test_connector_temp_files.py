"""Temporary files of the transcription connectors.

No connector writes the reply of the transcription service to a file.
"""

import io
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services import incognito_temp

ROOT = Path(__file__).resolve().parent.parent
CONNECTORS = ROOT / "src" / "services" / "transcription"


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """A system temporary folder and an incognito folder of this test only."""
    system = tmp_path / "system-tmp"
    system.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system))
    monkeypatch.setenv("INCOGNITO_TEMP_DIR", str(tmp_path / "incognito"))
    request_dir = incognito_temp.new_request_dir()
    return SimpleNamespace(system=system, request_dir=Path(request_dir), other=tmp_path)


def _audio(folder, name="tmpabc123.wav"):
    path = Path(folder) / name
    path.write_bytes(b"RIFF" + b"\x00" * 64)
    return path


def test_dir_for_stream(folders):
    upload = _audio(folders.request_dir)
    with open(upload, "rb") as audio:
        assert incognito_temp.dir_for_stream(audio) == str(folders.request_dir)
    with open(_audio(folders.other), "rb") as audio:
        assert incognito_temp.dir_for_stream(audio) is None
    assert incognito_temp.dir_for_stream(io.BytesIO(b"x")) is None
    with open(upload, "rb") as audio, os.fdopen(os.dup(audio.fileno()), "rb") as by_descriptor:
        assert incognito_temp.dir_for_stream(by_descriptor) is None     # its name is a number


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

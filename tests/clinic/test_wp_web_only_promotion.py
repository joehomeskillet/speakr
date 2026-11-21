"""The temporary clinic HTTP process may enqueue, but may neither bootstrap nor own jobs."""
import json
import os
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from test_wp_mount_correction import PASSWORD, ROOT, SERVER, STRANGER, TYPIST, _env


def test_shared_session_and_upload_without_bootstrap_or_background_jobs(tmp_path):
    env = _env(tmp_path, True)
    env.update(SECRET_KEY_FILE=str(tmp_path / "test-session-key"), CLINIC_ORIGINALS_ROOT=str(tmp_path / "uploads"))
    args = ["0", PASSWORD, TYPIST, STRANGER]
    # Provision once in the normal process and obtain a real signed author session.
    seed = SERVER.split('server = make_server(')[0] + """
import re
from pathlib import Path
from src.clinic.pools import ClinicAuthorPoolMember
with app.app_context():
    author = User.query.filter_by(email='author213@local.test').one()
    pool = ClinicAuthorPool.query.filter_by(name='Autoren 213').one()
    db.session.add(ClinicAuthorPoolMember(pool_id=pool.id,user_id=author.id,created_by=author.id))
    db.session.commit()
client = app.test_client()
form = client.get('/login').get_data(as_text=True)
token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', form)[1]
res = client.post('/login',data={'email':'author213@local.test', 'password':password, 'csrf_token':token})
assert res.status_code in (302,303) and '/login' not in res.location
Path('session.qa').write_text(client.get_cookie('session').value)
"""
    env["PYTHONPATH"] = str(ROOT)
    subprocess.run([sys.executable, "-c", seed, *args], env={**env, "SPEAKR_SKIP_BACKGROUND_TASKS": "true"},
                   cwd=tmp_path, check=True, capture_output=True, timeout=90)
    originals = tmp_path / "uploads/clinic/originals/v1"
    originals.mkdir(parents=True, exist_ok=True)
    final = originals / "synthetic-preserved"
    final.write_bytes(b"synthetic")
    leftover = originals / ".partial-web-only"
    os.link(final, leftover)
    probe = """
from io import BytesIO
from pathlib import Path
import json, re, threading, wave
from sqlalchemy import event
from sqlalchemy.engine import Engine
writes = []
def observe(conn, cursor, stmt, parameters, context, executemany):
    verb = stmt.split()[0].upper()
    if verb in ('CREATE','ALTER','DROP','INSERT','UPDATE','DELETE'):
        writes.append(verb)
event.listen(Engine,'before_cursor_execute',observe)
from src.app import app, db
from src.services.job_queue import job_queue
from src.models import ProcessingJob
event.remove(Engine,'before_cursor_execute',observe)
assert writes == [], writes
assert job_queue._app is app and not job_queue._is_owner and not job_queue._running
assert not [t for t in threading.enumerate() if t.name == 'JobQueueOwnerWatch' or 'Worker-' in t.name]
client=app.test_client()
route='/api/clinic/v1/recordings'
assert client.get(route).status_code == 401
client.set_cookie('session',Path('session.qa').read_text())
assert client.get(route).status_code == 200
home=client.get('/').get_data(as_text=True)
token=re.search(r'<meta name="csrf-token" content="([^"]+)"',home)[1]
assert client.post('/upload').status_code in (400,403)
media=BytesIO()
with wave.open(media,'wb') as audio:
    audio.setparams((1,2,16000,0,'NONE','not compressed')); audio.writeframes(b'\\x00\\x01'*1600)
media.seek(0)
uploaded=client.post('/upload', data={'file':(media,'synthetic.wav')},headers={'X-CSRFToken':token})
assert uploaded.status_code==202, uploaded.status_code
with app.app_context():
    jobs=ProcessingJob.query.filter_by(recording_id=uploaded.get_json()['id']).all()
    assert jobs and all(j.status=='queued' for j in jobs)
    assert not job_queue._running and not job_queue._is_owner
print('PROOF',json.dumps({'jobs':len(jobs),'bootstrap_writes':len(writes)}))
"""
    replica_env = {
        **env, "CLINIC_WEB_ONLY":"1", "SPEAKR_SKIP_BACKGROUND_TASKS":"false",
        "ADMIN_USERNAME":"unused-qa-bootstrap", "ADMIN_EMAIL":"unused@local.test", "ADMIN_PASSWORD":"synthetic-unused",
    }
    entry = ["bash", str(ROOT / "scripts/docker-entrypoint.sh"), sys.executable]
    run = subprocess.run([*entry, "-c", probe], env=replica_env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, run.stderr[-2500:]
    assert json.loads(run.stdout.split("PROOF ",1)[1])["bootstrap_writes"] == 0
    assert leftover.exists()

    # Use the normal Gunicorn application and the browser session signed before promotion.
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        address = "127.0.0.1:" + str(port.getsockname()[1])
    with (tmp_path / "gunicorn.log").open("w") as output:
        server = subprocess.Popen([*entry, "-m", "gunicorn", "--bind", address, "--workers", "1",
                                  "--worker-class", "gthread", "--threads", "2", "--no-control-socket", "src.app:app"],
                                 env=replica_env, cwd=tmp_path, stdout=output, stderr=output)
        try:
            ready_by = time.monotonic() + 45
            while time.monotonic() < ready_by:
                assert server.poll() is None
                try:
                    with urlopen("http://" + address + "/login", timeout=3) as response:
                        assert response.status == 200
                    break
                except (URLError, TimeoutError):
                    time.sleep(.1)
            else:
                raise AssertionError("Gunicorn never became ready")
            url = "http://" + address + "/api/clinic/v1/recordings"
            with pytest.raises(HTTPError) as anonymous:
                urlopen(url, timeout=5)
            assert anonymous.value.code == 401
            req = Request(url, headers={"Cookie":"session=" + (tmp_path / "session.qa").read_text()})
            with urlopen(req, timeout=5) as authenticated:
                assert authenticated.status == 200
        finally:
            server.terminate()
            server.wait(timeout=15)
    assert leftover.exists()


def test_invalid_mode_and_missing_session_key_fail_closed(tmp_path, monkeypatch):
    from src.clinic.config import web_only
    from src.utils.security import resolve_secret_key

    path = tmp_path / "uninitialized"
    with pytest.raises(ValueError, match="existing session signing key"):
        resolve_secret_key(None, "sqlite://", key_file=path, allow_generate=False)
    assert not path.exists()
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    monkeypatch.setenv("CLINIC_WEB_ONLY","1")
    with pytest.raises(RuntimeError, match="requires enabled clinic mode"):
        web_only()


def test_uninitialized_database_is_refused_without_creating_tables(tmp_path):
    import sqlite3

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    env = {**_env(tmp_path, True), "CLINIC_WEB_ONLY": "1", "SECRET_KEY": "temporary-synthetic-test-key", "PYTHONPATH":str(ROOT)}
    result = subprocess.run([sys.executable, "-c", "from src.app import app"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and "no such table: clinic_dictation" in result.stderr
    with sqlite3.connect(tmp_path / "w213.db") as database:
        assert database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []

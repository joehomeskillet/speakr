"""v8-62: app wiring gaps (derived audio source, durable protection store, author pool choice).

Synthetic data only. The real application runs in child processes against one throwaway database
(CLINIC_MODE on), so the real wiring in src/app.py and the real routes decide. Phase two starts a
new process on the same database to prove the protection state survives an app restart.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SETUP = r'''
import hashlib, json, sys
from src.app import app, db
from src.init_db import initialize_database
from src.models import Recording, User
from src.clinic import audio_revisions, app_wiring, revisions
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember

wav_path, storage_key = sys.argv[1], "clinic/originals/v1/synthetic"
out = {}
with app.app_context():
    initialize_database(app)
    u = User(username="v862_a", email="v862_a@local.test", password="x", is_admin=False)
    db.session.add(u); db.session.flush()
    rec = Recording(user_id=u.id, title="Alpha", status="COMPLETED", original_filename="a.wav")
    db.session.add(rec); db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=u.id, version_counter=0)
    db.session.add(dic); db.session.flush()
    data = open(wav_path, "rb").read()
    original = ClinicOriginal(dictation_id=dic.id, content_hash=hashlib.sha256(data).hexdigest(),
                              size_bytes=len(data), storage_key=storage_key, receipt="{}", origin_ref="synthetic")
    db.session.add(original); db.session.flush()
    audio_revisions.init_audio_revision(dic, original, 1000)
    revisions.add_asr_revision(dic, original, 'Synthetic transcript.', 'test')
    pools = {}
    for name in ("Zeta", "Alpha", "Mid"):
        p = ClinicAuthorPool(name=name, created_by=u.id)
        db.session.add(p); db.session.flush(); pools[name] = p.id
        db.session.add(ClinicAuthorPoolMember(pool_id=p.id, user_id=u.id, created_by=u.id))
    db.session.commit()
    out.update(uid=u.id, did=dic.id, pools=pools,
               chosen=[app_wiring.choose_author_pool(u.id), app_wiring.choose_author_pool(u.id)],
               none=app_wiring.choose_author_pool(u.id + 999))
print("RESULT" + json.dumps(out))
'''

PHASE = r'''
import json, sys, wave, io
from src.app import app

uid, did, phase = int(sys.argv[1]), sys.argv[2], sys.argv[3]
c = app.test_client()
def sign_in(client):
    with client.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return {"X-CSRF-Token": client.get("/api/csrf-token").json["csrf_token"]}
h = sign_in(c)
P = f"/api/clinic/v1/dictations/{did}/protection"
out = {}
if phase == "one":
    r = c.get(f"/api/clinic/v1/dictations/{did}/audio/derived")
    out["derived"] = r.status_code
    if r.status_code == 200:
        with wave.open(io.BytesIO(r.data), "rb") as w:
            out["wav"] = [w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()]
    out["before"] = c.get(P).get_json()
    out["set"] = c.put(P, headers=h, json={"secret": "synthetic-secret", "version": 1}).get_json()
    assert c.get(f"/api/clinic/v1/dictations/{did}/audio/derived").status_code == 200
    c = app.test_client()
    h = sign_in(c)
    out["locked_audio"] = c.get(f"/api/clinic/v1/dictations/{did}/audio/derived").status_code
    correction = f"/api/clinic/v1/dictations/{did}/correction"
    wrong = c.post(P.removesuffix('/protection') + '/unlock', headers=h, json={'secret': 'wrong-code'})
    assert wrong.status_code == 403
    assert c.get(correction).status_code == 404
    assert b'Synthetic transcript.' not in c.get(correction).data
    assert c.post(P.removesuffix('/protection') + '/unlock', headers=h,
                  json={'secret': 'synthetic-secret'}).status_code == 200
    assert c.get(correction).json['text'] == 'Synthetic transcript.'
    out["stale"] = c.put(P, headers=h, json={"secret": "x", "version": 1}).get_json()
else:
    out["after"] = c.get(P).get_json()
    assert c.post(P.removesuffix('/protection') + '/unlock', headers=h,
                  json={'secret': 'synthetic-secret'}).status_code == 200
    out["clear"] = c.put(P, headers=h, json={"secret": None, "version": 2}).get_json()
    out["cleared"] = c.get(P).get_json()
print("RESULT" + json.dumps(out))
'''


def _run(script, env, *args):
    done = subprocess.run([sys.executable, "-c", script, *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-3000:]
    line = next(x for x in done.stdout.splitlines() if x.startswith("RESULT"))
    return json.loads(line[len("RESULT"):])


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("v862")
    originals = tmp / "originals"
    key = originals / "clinic" / "originals" / "v1"
    key.mkdir(parents=True)
    wav_file = key / "synthetic"
    with wave.open(str(wav_file), "wb") as w:
        w.setparams((1, 2, 16000, 16000, "NONE", "not compressed"))
        w.writeframes(b"\x01\x00" * 16000)
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp / 'v862.db'}",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false", "CLINIC_MODE": "1",
           "CLINIC_ORIGINALS_ROOT": str(originals)}
    setup = _run(SETUP, env, str(wav_file))
    one = _run(PHASE, env, str(setup["uid"]), setup["did"], "one")
    two = _run(PHASE, env, str(setup["uid"]), setup["did"], "two")  # new process, same database
    return {"setup": setup, "one": one, "two": two}


def test_derived_audio_renders_from_the_stored_original(result):
    assert result["one"]["locked_audio"] == 404
    assert result["one"]["derived"] == 200
    assert result["one"]["wav"] == [1, 2, 16000, 16000]


def test_protection_state_survives_an_app_restart(result):
    assert result["one"]["before"]["protected"] is False
    assert result["one"]["set"] == {"protected": True, "version": 2}
    assert result["one"]["stale"]["code"] == "stale_version" or "stale" in json.dumps(result["one"]["stale"])
    assert result["two"]["after"]["protected"] is True
    assert result["two"]["after"]["version"] == 2
    assert result["two"]["clear"] == {"protected": False, "version": 3}
    assert result["two"]["cleared"]["protected"] is False


def test_author_pool_choice_is_deterministic_and_by_name(result):
    pools = result["setup"]["pools"]
    assert result["setup"]["chosen"] == [pools["Alpha"], pools["Alpha"]]
    assert result["setup"]["none"] is None

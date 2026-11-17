import os
import shutil
import stat
import wave
from pathlib import Path

import pytest

from src.clinic import decoder

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / 'clinic-spec/sources/dns-functional/fixtures/generated/audio'

needs_ffmpeg = pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg missing')


def _copy(name, tmp_path):
    dst = tmp_path / 'in' / name
    dst.parent.mkdir(exist_ok=True)
    shutil.copy(FIX / name, dst)
    return dst


def _stub(tmp_path, body):
    path = tmp_path / 'stub.sh'
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@needs_ffmpeg
@pytest.mark.parametrize('name', ['tone_16k_mono.wav', 'tone_48k_stereo.wav'])
def test_good_wav_decodes_and_original_untouched(name, tmp_path):
    src = _copy(name, tmp_path)
    before = src.read_bytes()
    res = decoder.decode_to_wav(src, tmp_path)
    assert res.status == decoder.OK
    assert res.container == 'wav'
    assert res.input_sha256 == decoder.sha256_file(FIX / name)
    assert src.read_bytes() == before
    assert decoder.sha256_file(src) == res.input_sha256
    with wave.open(res.output_path, 'rb') as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
    assert res.output_sha256 == decoder.sha256_file(res.output_path)
    assert res.duration_s > 0


@needs_ffmpeg
def test_silence_wav_is_ok(tmp_path):
    res = decoder.decode_to_wav(_copy('silence_16k_mono.wav', tmp_path), tmp_path)
    assert res.status == decoder.OK


def test_empty_input(tmp_path):
    res = decoder.decode_to_wav(_copy('empty.bin', tmp_path), tmp_path)
    assert res.status == decoder.EMPTY


@needs_ffmpeg
def test_truncated_wav_is_corrupt(tmp_path):
    res = decoder.decode_to_wav(_copy('truncated_wav.bin', tmp_path), tmp_path)
    assert res.status == decoder.CORRUPT
    assert res.output_sha256 is None
    assert not list(tmp_path.glob('*.wav'))


def test_unknown_magic_is_unsupported(tmp_path):
    src = tmp_path / 'x.bin'
    src.write_bytes(b'MZ\x90\x00' + b'\x00' * 32)
    assert decoder.decode_to_wav(src, tmp_path).status == decoder.UNSUPPORTED


@pytest.mark.parametrize('magic,container', [(b'\x02dss', 'dss'), (b'\x03ds2', 'ds2')])
def test_dss_without_binary_is_decoder_missing(magic, container, tmp_path, monkeypatch):
    monkeypatch.delenv(decoder.DSS_CODEC_BIN_ENV, raising=False)
    src = tmp_path / 'fake.ds2'
    src.write_bytes(magic + b'\x00' * 1024)
    res = decoder.decode_to_wav(src, tmp_path)
    assert res.status == decoder.DECODER_MISSING
    assert res.container == container
    assert res.input_sha256 == decoder.sha256_file(src)


def test_dss_binary_path_missing_is_decoder_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(decoder.DSS_CODEC_BIN_ENV, str(tmp_path / 'nope'))
    src = tmp_path / 'fake.ds2'
    src.write_bytes(b'\x03ds2' + b'\x00' * 64)
    assert decoder.decode_to_wav(src, tmp_path).status == decoder.DECODER_MISSING


def test_timeout_with_stub_command(tmp_path, monkeypatch):
    monkeypatch.setenv(decoder.DSS_CODEC_BIN_ENV, _stub(tmp_path, 'sleep 30'))
    src = tmp_path / 'fake.ds2'
    src.write_bytes(b'\x03ds2' + b'\x00' * 64)
    res = decoder.decode_to_wav(src, tmp_path, timeout_s=1)
    assert res.status == decoder.TIMEOUT
    assert res.input_sha256 == decoder.sha256_file(src)


def test_stub_nonzero_exit_is_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv(decoder.DSS_CODEC_BIN_ENV, _stub(tmp_path, 'echo bad >&2; exit 3'))
    src = tmp_path / 'fake.dss'
    src.write_bytes(b'\x02dss' + b'\x00' * 64)
    res = decoder.decode_to_wav(src, tmp_path)
    assert res.status == decoder.CORRUPT
    assert 'bad' in res.detail


@needs_ffmpeg
def test_output_size_cap(tmp_path):
    src = _copy('tone_48k_stereo.wav', tmp_path)
    res = decoder.decode_to_wav(src, tmp_path, max_output_bytes=1000)
    assert res.status == decoder.CORRUPT
    assert not list(tmp_path.glob('*.16k.wav'))


def test_dss_call_convention_matches_real_cli(tmp_path, monkeypatch):
    # Stub records argv; real dss-decode takes `-O <out> <input>` and exits 1 on error.
    log = tmp_path / 'argv.txt'
    body = f'printf "%s\\n" "$@" > {log}; exit 1'
    monkeypatch.setenv(decoder.DSS_CODEC_BIN_ENV, _stub(tmp_path, body))
    src = tmp_path / 'fake.ds2'
    src.write_bytes(b'\x03ds2' + b'\x00' * 64)
    assert decoder.decode_to_wav(src, tmp_path).status == decoder.CORRUPT
    argv = log.read_text().split('\n')
    assert argv[argv.index('-O') + 1] == str(tmp_path / 'fake.16k.wav')
    assert argv[argv.index('-r') + 1] == '16000'
    assert argv[-2] == str(src)


# Real-decoder test: defined only when a pinned binary and a private DS2 fixture
# are configured (no skip markers; the default run never references the fixture).
_REAL_BIN = os.environ.get('CLINIC_DSS_CODEC_BIN', '')
_REAL_FIXTURE = os.environ.get('CLINIC_PRIVATE_DS2_FIXTURE', '')

if _REAL_BIN and _REAL_FIXTURE and Path(_REAL_BIN).is_file() and Path(_REAL_FIXTURE).is_file():

    def test_real_ds2_decodes_to_16k_mono_pcm(tmp_path):
        src = Path(_REAL_FIXTURE)
        before = decoder.sha256_file(src)
        res = decoder.decode_to_wav(src, tmp_path, timeout_s=300)
        assert res.status == decoder.OK, res.detail
        assert res.container == 'ds2'
        assert res.input_sha256 == before
        assert decoder.sha256_file(src) == before
        with wave.open(res.output_path, 'rb') as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert 10 < res.duration_s < 4 * 3600
        assert res.output_sha256 == decoder.sha256_file(res.output_path)

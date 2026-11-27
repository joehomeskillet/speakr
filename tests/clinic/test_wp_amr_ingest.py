"""AMR-NB/WB is recognized from bytes and decoded to a separate WAV."""
import shutil
import wave

import pytest

from src.clinic import decoder

needs_ffmpeg = pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg missing')

# Packed sizes include the TOC byte, matching the ffmpeg AMR demuxer.
_NB_PACKED = (13, 14, 16, 18, 20, 21, 27, 32, 6, 7, 6, 6, 0, 0, 0, 1)
_WB_PACKED = (18, 24, 33, 37, 41, 47, 51, 59, 61, 6, 6, 0, 0, 0, 1, 1)


def _synthetic_amr(header: bytes, sizes: tuple, mode: int, count: int = 10) -> bytes:
    """Build a storage-format AMR file. Speech bits are a non-zero pattern."""
    packed = sizes[mode]
    toc = bytes([(mode << 3) | 0x04])
    speech = bytes((index * 17 + 3) & 0xFF for index in range(packed - 1))
    return header + (toc + speech) * count


@needs_ffmpeg
@pytest.mark.parametrize('payload,container', [
    (_synthetic_amr(b'#!AMR\n', _NB_PACKED, 7), 'amr-nb'),
    (_synthetic_amr(b'#!AMR-WB\n', _WB_PACKED, 2), 'amr-wb'),
])
def test_synthetic_amr_decodes_to_mono_pcm(payload, container, tmp_path):
    src = tmp_path / 'clip.bin'
    src.write_bytes(payload)
    before = src.read_bytes()
    res = decoder.decode_to_wav(src, tmp_path)
    assert res.status == decoder.OK, res.detail
    assert res.container == container
    assert src.read_bytes() == before
    assert res.input_sha256 == decoder.sha256_file(src)
    assert res.output_path != str(src)
    with wave.open(res.output_path, 'rb') as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        pcm = wav.readframes(wav.getnframes())
    assert pcm and any(pcm)
    assert res.duration_s and res.duration_s > 0
    assert res.output_sha256 == decoder.sha256_file(res.output_path)


def test_renamed_non_amr_fails_without_changing_original(tmp_path):
    src = tmp_path / 'scan.amr'
    src.write_bytes(b'%PDF-1.4\nnot an AMR payload\n')
    before = src.read_bytes()
    res = decoder.decode_to_wav(src, tmp_path)
    assert res.status == decoder.UNSUPPORTED
    assert res.container is None
    assert res.output_path is None
    assert src.read_bytes() == before
    assert res.input_sha256 == decoder.sha256_file(src)
    assert list(tmp_path.glob('*.wav')) == []

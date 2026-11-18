import hashlib
import io
import struct
import wave
from dataclasses import replace

import pytest

from src.app import app, db
from src.clinic.audio_render import AudioSource, render_audio, render_current_revision
from src.clinic.audio_revisions import (
    SOURCE_ORIGINAL, SOURCE_SEGMENT, AudioRevisionError, EDLEntry, EditDecisionList,
    apply_cut, apply_insert, apply_insert_to_edl, apply_overwrite, apply_trim,
    compute_edl_hash, create_segment, init_audio_revision, make_initial_edl, redo, undo,
)
from src.clinic.models import ClinicDictation, ClinicOriginal


def _wav(samples, rate=1000):
    dest = io.BytesIO()
    with wave.open(dest, "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return dest.getvalue()


def _samples(artifact, rate=1000):
    with wave.open(io.BytesIO(artifact.wav), "rb") as reader:
        assert reader.getframerate() == rate
        count = reader.getnframes()
        return struct.unpack(f"<{count}h", reader.readframes(count))


def test_edits_and_sixteen_undo_redo_steps(world):
    raw, x, y = _wav((1, 2, 3, 4, 5, 6)), _wav((50, 60)), _wav((70, 80))
    digest = hashlib.sha256(raw).hexdigest()
    with app.app_context():
        dictation = ClinicDictation(recording_id=world["rec_a"], author_user_id=world["user_a"])
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(dictation_id=dictation.id, content_hash=digest,
                                  storage_key="original.wav", size_bytes=len(raw),
                                  receipt="synthetic", origin_ref="synthetic")
        db.session.add(original)
        db.session.flush()
        seg_x, seg_y = (create_segment(dictation, f"{n}.wav", 2, hashlib.sha256(data).hexdigest())
                        for n, data in (("x", x), ("y", y)))
        sources = {(SOURCE_ORIGINAL, original.id): AudioSource(raw),
                   (SOURCE_SEGMENT, seg_x.id): AudioSource(x), (SOURCE_SEGMENT, seg_y.id): AudioSource(y)}

        def render(revision):
            return render_current_revision(dictation, revision.id, sources, sample_rate=1000)

        states = [(init_audio_revision(dictation, original, 6), (1, 2, 3, 4, 5, 6)),
                  (apply_insert(dictation, 2, seg_x), (1, 2, 50, 60, 3, 4, 5, 6)),
                  (apply_overwrite(dictation, 2, seg_y), (1, 2, 70, 80, 3, 4, 5, 6)),
                  (apply_cut(dictation, 1, 2), (1, 70, 80, 3, 4, 5, 6)),
                  (apply_trim(dictation, 5), (1, 70, 80, 3, 4))]
        with pytest.raises(AudioRevisionError, match="stale audio revision"):
            render(states[0][0])
        anchor = render(states[-1][0])
        assert _samples(anchor) == states[-1][1]
        for n in range(12):
            data = _wav((n,))
            tail = create_segment(dictation, f"tail-{n}.wav", 1, hashlib.sha256(data).hexdigest())
            revision = apply_insert(dictation, 5 + n, tail)
            sources[(SOURCE_SEGMENT, tail.id)] = AudioSource(data)
            states.append((revision, (1, 70, 80, 3, 4, *range(n + 1))))
        for step, direction in ((undo, reversed(states[:-1])), (redo, states[1:])):
            for revision, expected in direction:
                assert step(dictation).id == revision.id
                assert _samples(render(revision)) == expected
        assert [s.source_sha256 for s in anchor.spans] == [digest, hashlib.sha256(y).hexdigest(), digest]
        db.session.refresh(original)
        assert original.content_hash == hashlib.sha256(raw).hexdigest() == digest


def test_mixed_rates_use_absolute_half_open_bounds():
    edl = make_initial_edl("dictation", "original", 3)
    edl = apply_insert_to_edl(edl, 1, "segment", SOURCE_SEGMENT, 1, 3)
    segment_hash = hashlib.sha256(b"synthetic original before decoding").hexdigest()
    sources = {(SOURCE_ORIGINAL, "original"): AudioSource(_wav((1, 2, 3))),
               (SOURCE_SEGMENT, "segment"): AudioSource(_wav(range(133), 44100), segment_hash)}
    result = render_audio(edl, sources, sample_rate=1000)
    assert _samples(result) == (1, 44, 88, 2, 3)
    span = result.spans[1]
    assert (span.source_rate, span.source_start_sample, span.source_end_sample) == (44100, 44, 132)
    assert (span.output_start_sample, span.output_end_sample, span.source_sha256) == (1, 3, segment_hash)


def test_fractional_output_boundaries_do_not_accumulate_rounding_error():
    edl = make_initial_edl("dictation", "original", 2)
    edl = apply_insert_to_edl(edl, 1, "segment", SOURCE_SEGMENT, 0, 1)
    sources = {(SOURCE_ORIGINAL, "original"): AudioSource(_wav((1, 2))),
               (SOURCE_SEGMENT, "segment"): AudioSource(_wav((3,)))}
    result = render_audio(edl, sources, sample_rate=1500)
    assert _samples(result, rate=1500) == (1, 3, 3, 2)
    assert [(s.output_start_sample, s.output_end_sample) for s in result.spans] == [(0, 1), (1, 3), (3, 4)]


@pytest.mark.parametrize(("replacement", "message"), [
    ((1, 3, 2, 4), "overlapping source"),
    ((2, 4, 2, 4), "out-of-bounds source"),
    ((-1, 1, 2, 4), "invalid sample rate or millisecond boundary"),
    ((0, 0, 2, 4), "out-of-bounds source"),
    ((2, 3, 1, 2), "invalid output interval"),
])
def test_invalid_intervals(replacement, message):
    source = make_initial_edl("dictation", "original", 2).entries[0]
    entries = (source, EDLEntry("original", SOURCE_ORIGINAL, *replacement))
    edl = EditDecisionList("dictation", None, 4, entries, compute_edl_hash(entries))
    with pytest.raises(AudioRevisionError, match=message):
        render_audio(edl, {(SOURCE_ORIGINAL, "original"): AudioSource(_wav((1, 2, 3)))}, sample_rate=1000)


def test_unavailable_source_stale_edl_and_truncated_media_can_be_retried():
    edl = make_initial_edl("dictation", "original", 2)
    key = (SOURCE_ORIGINAL, "original")
    raw = _wav((5, 6))
    sources = {key: AudioSource(raw)}
    with pytest.raises(AudioRevisionError, match="source unavailable"):
        render_audio(edl, {(SOURCE_SEGMENT, "original"): AudioSource(raw)}, sample_rate=1000)
    with pytest.raises(AudioRevisionError, match="hash mismatch"):
        render_audio(replace(edl, content_hash="stale"), sources, sample_rate=1000)
    with pytest.raises(AudioRevisionError, match="truncated audio source"):
        render_audio(edl, {key: AudioSource(raw[:-1])}, sample_rate=1000)
    assert render_audio(edl, sources, sample_rate=1000).wav == raw
    assert render_audio(edl, sources, sample_rate=1000) == render_audio(edl, sources, sample_rate=1000)

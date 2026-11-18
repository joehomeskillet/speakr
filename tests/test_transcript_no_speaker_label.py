"""Without diarization no speaker label appears in a rendered transcript.

Segments without a speaker (None, empty, or the "Unknown Speaker"
placeholder of older stored transcripts) render without the "[...]:" prefix.
Every rendered line of the basic export carries its [hh:mm:ss] timestamp, with
or without a speaker. With a speaker the label stays.
"""
from src.utils.transcript_render import BASIC_TEMPLATE_FORMAT, render_segments


def _segments(speaker):
    return [
        {"speaker": speaker, "sentence": "Guten Tag.", "start_time": 0.0, "end_time": 2.0},
        {"speaker": speaker, "sentence": "Wie geht es Ihnen?", "start_time": 3725.0, "end_time": 3730.0},
    ]


def test_basic_export_without_speaker_has_no_prefix_but_timestamps():
    for speaker in (None, '', 'Unknown Speaker', 'UNKNOWN_SPEAKER'):
        out = render_segments(_segments(speaker), BASIC_TEMPLATE_FORMAT)
        assert out == "[00:00:00] Guten Tag.\n[01:02:05] Wie geht es Ihnen?"
        assert 'Unknown' not in out and 'UNKNOWN' not in out


def test_basic_export_with_speaker_keeps_label_and_adds_timestamp():
    out = render_segments(_segments('Dr. Meier'), BASIC_TEMPLATE_FORMAT)
    assert out == "[00:00:00] [Dr. Meier]: Guten Tag.\n[01:02:05] [Dr. Meier]: Wie geht es Ihnen?"


def test_user_template_without_speaker_drops_only_the_speaker_slot():
    assert render_segments(_segments(None), "{{speaker}}: {{text}}") == "Guten Tag.\nWie geht es Ihnen?"
    assert render_segments(_segments(None), "[{{start_time}}] {{speaker|upper}}: {{text}}") == \
        "[00:00:00] Guten Tag.\n[01:02:05] Wie geht es Ihnen?"
    assert render_segments(_segments('Anna'), "{{speaker}}: {{text}}") == \
        "Anna: Guten Tag.\nAnna: Wie geht es Ihnen?"

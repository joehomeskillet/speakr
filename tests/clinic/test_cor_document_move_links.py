"""Audio provenance on correction saves with explicit paragraph moves."""

from copy import deepcopy

import pytest

from src.clinic import alignment, alignment_tokens
from src.clinic.audio_revisions import current_audio_revision
from src.clinic.correction import ClinicTextAudioLink
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.database import db

import test_cor_document_api as document_api
import test_correction
from test_cor_document_api import api  # noqa: F401 (fixture)
from test_wp_v8_routes_alignment import _seed


LINES = ('😀🩺 A\u0308rztin', 'Plan bleibt.', 'Plan bleibt.')
NORMALIZED_FIRST = '😀🩺 Ärztin'
TIMES = ((100, 900), (2100, 3300), (5400, 7300))


def _save_from(api_client, opened, document, *, moves=None, position=100):
    client, headers, ids = api_client
    snapshot = opened.json
    return document_api._save(
        client, headers, ids, document_api._plain(document), if_match=opened.headers['ETag'],
        base=snapshot['text_revision_id'], version=snapshot.get('document_version', 0),
        state=snapshot['state']['version'] if snapshot['state'] else None,
        document=document, extra=None if moves is None else {'move_map': moves}, position=position,
    )


@pytest.fixture
def move_base(request, monkeypatch):
    monkeypatch.setattr(test_correction, 'BODY', '\n'.join(LINES))
    monkeypatch.setattr(document_api, 'SEGMENTS', [
        dict(text=text, start=start / 1000, end=end / 1000)
        for text, (start, end) in zip(LINES, TIMES)
    ])
    api_client = request.getfixturevalue('api')
    client, _, ids = api_client
    opened = document_api._get(client, ids)
    assert opened.status_code == 200, opened.json
    assert [(link['audio_start_ms'], link['audio_end_ms']) for link in opened.json['links']] == list(TIMES)
    document = document_api._nodes(LINES)
    for node, link in zip(document['nodes'], opened.json['links']):
        node['runs'][0]['audio_anchor_id'] = link['id']
    saved = _save_from(api_client, opened, document)
    assert saved.status_code == 200, saved.json
    base = document_api._get(client, ids)
    assert base.json['text_revision_id'] == ids['asr']
    assert base.json['document_version'] == 1
    assert base.json['document'] == document
    return api_client, base


def _move(segment_id: str, old_start: int, new_start: int, text: str) -> dict:
    return dict(segment_id=segment_id, old_start=old_start, old_end=old_start + len(text),
                new_start=new_start, new_end=new_start + len(text))


def _utf16_len(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def test_explicit_moves_preserve_only_the_moved_audio_segments_on_reopen(move_base) -> None:
    api_client, base = move_base
    client, _, ids = api_client
    first, second, third = deepcopy(base.json['document']['nodes'])
    typed = {'id': 'typed-p', 'kind': 'paragraph', 'runs': [{'id': 'typed-r', 'text': LINES[0]}]}
    second['runs'][0].update(text='Plan geändert.', audio_anchor_id=None)
    document = {'schema_version': 1, 'nodes': [typed, third, first, second]}
    one, two, three = LINES
    # Raw code-point offsets differ from both NFC offsets and UTF-16 offsets.
    moves = [
        _move('r3', len(one + '\n' + two + '\n'), len(one + '\n'), three),
        _move('r1', 0, len(one + '\n' + three + '\n'), one),
    ]
    saved = _save_from(api_client, base, document, moves=moves)
    assert saved.status_code == 200, saved.json
    reopened = document_api._get(client, ids)
    assert reopened.status_code == 200, reopened.json
    assert reopened.json['text_revision_id'] == saved.json['text_revision_id'] != ids['asr']
    assert reopened.json['document'] == saved.json['document']
    for index in (0, 3):  # Typed copy and replaced run.
        assert reopened.json['document']['nodes'][index]['runs'][0]['audio_anchor_id'] is None
    assert reopened.json['text'] == '\n'.join((NORMALIZED_FIRST, three, NORMALIZED_FIRST, 'Plan geändert.'))
    # GET reports browser (UTF-16) offsets over the normalized stored transcript.
    third_start = _utf16_len(NORMALIZED_FIRST + '\n')
    first_start = third_start + len(three + '\n')
    assert sorted((link['text_start'], link['text_end'], link['audio_start_ms'],
                   link['audio_end_ms'], link['granularity']) for link in reopened.json['links']) == [
        (third_start, third_start + len(three), *TIMES[2], 'segment'),
        (first_start, first_start + _utf16_len(NORMALIZED_FIRST), *TIMES[0], 'segment'),
    ]
    selection = client.get(document_api.BASE.format(ids['dictation']),
                           query_string={'offset': third_start}).json['selection']
    assert selection['aligned'] and selection['audio_start_ms'] == TIMES[2][0]


@pytest.mark.parametrize('invalid', ['forged-id', 'duplicate-id', 'overlap', 'wrong-occurrence', 'utf16'])
def test_invalid_moves_do_not_write(move_base, invalid: str) -> None:
    api_client, base = move_base
    client, _, ids = api_client
    first, second, third = base.json['document']['nodes']
    document = {'schema_version': 1, 'nodes': [third, first, second]}
    one, two, three = LINES
    third_old_start = len(one + '\n' + two + '\n')
    move = _move('r3', third_old_start, 0, three)
    invalid_moves = {
        'forged-id': [dict(move, segment_id='forged')],
        'duplicate-id': [move, move],
        'overlap': [move, dict(move, segment_id='r2')],
        'wrong-occurrence': [_move('r3', len(one + '\n'), 0, two)],
        'utf16': [_move('r3', _utf16_len(one + '\n' + two + '\n'), 0, three)],
    }
    links = db.session.query(ClinicTextAudioLink).filter_by(dictation_id=ids['dictation'])
    before = (document_api._revisions(ids), document_api._snapshots(), links.count())
    rejected = _save_from(api_client, base, document, moves=invalid_moves[invalid], position=789)
    assert rejected.status_code == 400, rejected.json
    assert (document_api._revisions(ids), document_api._snapshots(), links.count()) == before
    reopened = document_api._get(client, ids)
    assert reopened.status_code == 200, reopened.json
    assert reopened.json == base.json


def test_swapping_equal_paragraphs_creates_revision_without_current_word_alignment(move_base) -> None:
    api_client, base = move_base
    client, _, ids = api_client
    dictation = db.session.get(ClinicDictation, ids['dictation'])
    audio = current_audio_revision(dictation)
    assert audio is not None
    before = db.session.get(ClinicTranscriptRevision, base.json['text_revision_id'])
    old_alignment = _seed(dictation, audio, before)
    assert old_alignment.state == 'succeeded'

    first, second, third = base.json['document']['nodes']
    document = {'schema_version': 1, 'nodes': [first, third, second]}
    one, two, three = LINES
    second_start, third_start = len(one + '\n'), len(one + '\n' + two + '\n')
    moves = [_move('r2', second_start, third_start, two), _move('r3', third_start, second_start, three)]
    saved = _save_from(api_client, base, document, moves=moves)
    assert saved.status_code == 200, saved.json
    reopened = document_api._get(client, ids)
    assert reopened.status_code == 200, reopened.json
    revision_id = reopened.json['text_revision_id']
    assert revision_id == saved.json['text_revision_id'] != before.id
    assert reopened.json['text'] == before.body
    assert [node['id'] for node in reopened.json['document']['nodes']] == ['p1', 'p3', 'p2']
    playback = {link['text_start']: link['audio_start_ms'] for link in reopened.json['links']}
    second_offset = _utf16_len(NORMALIZED_FIRST + '\n')
    assert playback[second_offset] == TIMES[2][0]
    assert playback[second_offset + len(three + '\n')] == TIMES[1][0]
    row, state = alignment.read_for(dictation.id, audio.id, revision_id)
    assert state == 'stale' and row.id == old_alignment.id
    after = db.session.get(ClinicTranscriptRevision, revision_id)
    old_tokens = alignment_tokens.tokens_for(dictation, before).document
    new_tokens = alignment_tokens.tokens_for(dictation, after).document
    assert alignment_tokens.utf16_spans(old_tokens).keys().isdisjoint(
        alignment_tokens.utf16_spans(new_tokens)
    )

"""Tests for the author suggestion service (suggest only, never assign)."""

from __future__ import annotations

import json
import math

import pytest

from src.clinic import author_suggestion as a
from src.clinic.author_suggestion import ScoredCandidate as S

QUERY = (1.0, 0.0)


def vec(score: float) -> tuple[float, float]:
    """Unit vector whose cosine with QUERY equals ``score``."""
    return (score, math.sqrt(1.0 - score * score))


def run(scores, speech=10.0):
    return a.suggest_author(QUERY, [(uid, vec(s)) for uid, s in scores], speech_seconds=speech)


def test_constants_documented_values():
    assert a.SINGLE_MIN_SCORE == 0.75
    assert a.SINGLE_MIN_GAP == 0.10
    assert a.CHOICE_MIN_SCORE == 0.60
    assert a.MIN_SPEECH_SECONDS == 3.0


# --- band boundaries on exact scores -------------------------------------


@pytest.mark.parametrize(
    "ranked, band",
    [
        ([S(1, 0.75), S(2, 0.65)], a.BAND_SINGLE),  # score and gap exactly on edge
        ([S(1, 0.7499), S(2, 0.50)], a.BAND_CHOICE),  # just below single score
        ([S(1, 0.90), S(2, 0.80)], a.BAND_SINGLE),  # gap exactly 0.10
        ([S(1, 0.90), S(2, 0.8001)], a.BAND_CHOICE),  # gap just under 0.10
        ([S(1, 0.60)], a.BAND_CHOICE),  # lower edge inclusive
        ([S(1, 0.5999)], a.BAND_NONE),  # just under lower edge
        ([S(1, 0.75)], a.BAND_SINGLE),  # no runner-up
    ],
)
def test_classify_scores_boundaries(ranked, band):
    assert a.classify_scores(ranked)[0] == band


@pytest.mark.parametrize(
    "scores, band",
    [
        ([(1, 0.75), (2, 0.65)], a.BAND_SINGLE),
        ([(1, 0.90), (2, 0.80)], a.BAND_SINGLE),
        ([(1, 0.60), (2, 0.10)], a.BAND_CHOICE),
        ([(1, 0.59), (2, 0.10)], a.BAND_NONE),
    ],
)
def test_end_to_end_edges_survive_float_noise(scores, band):
    assert run(scores).band == band


# --- cases ---------------------------------------------------------------


def test_single_suggestion():
    r = run([(7, 0.90), (3, 0.40)])
    assert r.band == a.BAND_SINGLE
    assert r.reason == a.REASON_SINGLE
    assert [s.user_id for s in r.suggestions] == [7]
    assert r.top_score == pytest.approx(0.90)
    assert r.gap == pytest.approx(0.50)


def test_top2_when_gap_small():
    r = run([(1, 0.88), (2, 0.84), (3, 0.30)])
    assert r.band == a.BAND_CHOICE
    assert [s.user_id for s in r.suggestions] == [1, 2]


def test_top2_in_mid_band():
    r = run([(1, 0.70), (2, 0.65), (3, 0.62)])
    assert r.band == a.BAND_CHOICE
    assert [s.user_id for s in r.suggestions] == [1, 2]


def test_mid_band_drops_runner_up_below_floor():
    r = run([(1, 0.70), (2, 0.20)])
    assert r.band == a.BAND_CHOICE
    assert [s.user_id for s in r.suggestions] == [1]


def test_below_floor_no_suggestion():
    r = run([(1, 0.50), (2, 0.40)])
    assert r.band == a.BAND_NONE
    assert r.reason == a.REASON_BELOW_THRESHOLD
    assert r.suggestions == ()
    assert r.top_score == pytest.approx(0.50)


# --- short dictation -----------------------------------------------------


def test_short_dictation_gives_no_suggestion_even_with_perfect_match():
    r = run([(1, 0.99)], speech=2.99)
    assert r.band == a.BAND_NONE
    assert r.reason == a.REASON_SHORT_SPEECH
    assert r.suggestions == ()


def test_speech_exactly_three_seconds_is_allowed():
    assert run([(1, 0.99)], speech=3.0).band == a.BAND_SINGLE


def test_zero_speech_is_short():
    assert run([(1, 0.99)], speech=0).reason == a.REASON_SHORT_SPEECH


# --- determinism, empty --------------------------------------------------


def test_ties_sorted_by_user_id_independent_of_input_order():
    cands = [(9, vec(0.80)), (2, vec(0.80)), (5, vec(0.80))]
    r1 = a.suggest_author(QUERY, cands, speech_seconds=5)
    r2 = a.suggest_author(QUERY, list(reversed(cands)), speech_seconds=5)
    assert r1 == r2
    assert r1.band == a.BAND_CHOICE
    assert [s.user_id for s in r1.suggestions] == [2, 5]


def test_order_by_score_descending():
    r = run([(1, 0.70), (2, 0.78), (3, 0.74)])
    assert [s.user_id for s in r.suggestions] == [2, 3]


def test_empty_candidates():
    r = a.suggest_author(QUERY, [], speech_seconds=10)
    assert r.band == a.BAND_NONE
    assert r.reason == a.REASON_NO_CANDIDATES
    assert r.suggestions == ()
    assert r.top_score is None and r.gap is None


def test_candidates_may_be_a_generator():
    r = a.suggest_author(QUERY, ((i, vec(0.9)) for i in (1,)), speech_seconds=5)
    assert r.band == a.BAND_SINGLE


# --- serialisation and no assignment -------------------------------------


def test_to_dict_is_json_serialisable_and_requires_confirmation():
    d = run([(1, 0.88), (2, 0.84)]).to_dict()
    again = json.loads(json.dumps(d))
    assert again["band"] == a.BAND_CHOICE
    assert again["requires_confirmation"] is True
    assert [s["user_id"] for s in again["suggestions"]] == [1, 2]
    assert set(again["suggestions"][0]) == {"user_id", "score"}


def test_no_assignment_field_in_result():
    d = run([(1, 0.99)]).to_dict()
    assert not any("assign" in k or k == "author_user_id" for k in d)


def test_none_result_serialises():
    d = run([], speech=1).to_dict()
    assert json.loads(json.dumps(d))["suggestions"] == []


# --- invalid input -------------------------------------------------------


@pytest.mark.parametrize(
    "query, cands, speech, code",
    [
        ((0.0, 0.0), [(1, (1.0, 0.0))], 5, "invalid_embedding"),
        ((), [(1, (1.0, 0.0))], 5, "invalid_embedding"),
        ((1.0, float("nan")), [(1, (1.0, 0.0))], 5, "invalid_embedding"),
        ((1.0, 0.0), [(1, (0.0, 0.0))], 5, "invalid_embedding"),
        ((1.0, 0.0), [(1, (1.0, 0.0, 0.0))], 5, "dimension_mismatch"),
        ((1.0, 0.0), [(1, (1.0, 0.0)), (1, (0.0, 1.0))], 5, "duplicate_candidate"),
        ((1.0, 0.0), [], -1, "invalid_speech_seconds"),
        ((1.0, 0.0), [], float("nan"), "invalid_speech_seconds"),
        ((1.0, 0.0), [], "5", "invalid_speech_seconds"),
    ],
)
def test_invalid_input_rejected(query, cands, speech, code):
    with pytest.raises(a.AuthorSuggestionError) as exc:
        a.suggest_author(query, cands, speech_seconds=speech)
    assert exc.value.code == code

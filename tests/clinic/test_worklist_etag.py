"""Tests for the work-list change token and refresh policy (PAR-WF-002)."""

import hashlib
import re
from datetime import datetime, timezone, tzinfo

import pytest

from src.clinic import worklist_etag
from src.clinic.worklist_etag import (
    BAD_COUNTS,
    BAD_ETAG,
    BAD_HEADER,
    BAD_ID,
    BAD_INTERVAL,
    BAD_POLICY,
    BAD_ROW,
    BAD_VERSION,
    MISSING_ID,
    MISSING_VERSION,
    RefreshError,
    RefreshPolicy,
    clamp_interval,
    if_none_match,
    list_etag,
    next_delay,
)

AWARE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
ETAG_RE = re.compile(r'^W/"[0-9a-f]{32}"$')


class _BrokenZone(tzinfo):
    """A tzinfo whose utcoffset is None: as useless as a naive datetime."""

    def utcoffset(self, _dt):
        return None

    def dst(self, _dt):
        return None

    def tzname(self, _dt):
        return None


def _rows(*pairs):
    return [{"id": i, "version": v} for i, v in pairs]


def _code(err):
    return err.value.code


# --- list_etag: format, digest, order, sensitivity ---------------------------


def test_etag_format_is_weak_with_32_hex():
    assert ETAG_RE.fullmatch(list_etag(_rows((1, 2), (2, 1))))


def test_etag_is_first_32_hex_of_sha256_over_sorted_id_version_lines():
    rows = _rows((2, 1), (1, "x"), (3, AWARE))
    lines = sorted(["2:1", "1:x", f"3:{AWARE.isoformat()}"])
    expected = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:32]
    assert list_etag(rows) == f'W/"{expected}"'


def test_etag_of_empty_list_is_sha256_of_empty_input():
    expected = hashlib.sha256(b"").hexdigest()[:32]
    assert list_etag([]) == f'W/"{expected}"'


@pytest.mark.parametrize('order', [
    [0, 1, 2, 3],
    [3, 2, 1, 0],
    [2, 0, 3, 1],
])
def test_etag_is_independent_of_row_order(order):
    pairs = [(1, 1), (2, "v2"), (3, AWARE), (4, 0)]
    shuffled = [pairs[i] for i in order]
    assert list_etag(_rows(*shuffled)) == list_etag(_rows(*pairs))


def test_etag_is_stable_for_equal_lists():
    rows = _rows((1, 1), (2, "v2"), (3, AWARE))
    assert list_etag(rows) == list_etag([dict(row) for row in reversed(rows)])
    assert list_etag(rows) == list_etag(iter(rows))


def test_etag_changes_when_a_row_is_added():
    base = _rows((1, 1), (2, 2))
    assert list_etag(base + _rows((3, 3))) != list_etag(base)


def test_etag_changes_when_a_row_is_removed():
    base = _rows((1, 1), (2, 2))
    assert list_etag(base[1:]) != list_etag(base)


def test_etag_changes_when_a_version_changes():
    assert list_etag(_rows((1, 1), (2, 3))) != list_etag(_rows((1, 1), (2, 2)))


def test_non_mapping_row_raises():
    with pytest.raises(RefreshError) as err:
        list_etag([(1, 2)])
    assert _code(err) == BAD_ROW


def test_non_iterable_rows_raise_refresh_error():
    with pytest.raises(RefreshError) as err:
        list_etag(42)
    assert _code(err) == BAD_ROW


# --- list_etag: id and version validation ------------------------------------


@pytest.mark.parametrize('bad,code', [
    (None, MISSING_ID),
    (True, BAD_ID),
    (1.5, BAD_ID),
    ("", BAD_ID),
    ("a\x00b", BAD_ID),
    ([], BAD_ID),
])
def test_bad_row_id_raises(bad, code):
    with pytest.raises(RefreshError) as err:
        list_etag([{"id": bad, "version": 1}])
    assert _code(err) == code


def test_row_without_version_raises():
    with pytest.raises(RefreshError) as err:
        list_etag([{"id": 1}])
    assert _code(err) == MISSING_VERSION


@pytest.mark.parametrize('version', [3, "v7", AWARE])
def test_version_may_be_int_str_or_aware_datetime(version):
    assert ETAG_RE.fullmatch(list_etag([{"id": 1, "version": version}]))


@pytest.mark.parametrize('bad', [True, 1.5, float("nan"), float("inf"), "", "v\x00", [], {}])
def test_bad_version_raises(bad):
    with pytest.raises(RefreshError) as err:
        list_etag([{"id": 1, "version": bad}])
    assert _code(err) == BAD_VERSION


def test_naive_datetime_version_raises_refresh_error_which_is_value_error():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(RefreshError) as err:
        list_etag([{"id": 1, "version": naive}])
    assert _code(err) == BAD_VERSION
    assert isinstance(err.value, ValueError)


def test_datetime_version_without_utcoffset_raises():
    with pytest.raises(RefreshError) as err:
        list_etag([{"id": 1, "version": datetime(2026, 1, 1, tzinfo=_BrokenZone())}])
    assert _code(err) == BAD_VERSION


def test_updated_at_is_used_when_version_is_absent():
    assert list_etag([{"id": 1, "updated_at": 7}]) == list_etag([{"id": 1, "version": 7}])


def test_updated_at_falls_back_when_version_is_none():
    row = {"id": 1, "version": None, "updated_at": 7}
    assert list_etag([row]) == list_etag([{"id": 1, "version": 7}])


# --- if_none_match ------------------------------------------------------------


def _etag():
    return list_etag(_rows((1, 1), (2, 2)))


def test_none_or_empty_header_means_client_has_no_copy():
    etag = _etag()
    assert if_none_match(None, etag) is False
    assert if_none_match("", etag) is False
    assert if_none_match("   ", etag) is False


def test_star_header_is_current():
    assert if_none_match("*", 'W/"abc"') is True
    assert if_none_match("  *  ", 'W/"abc"') is True


def test_comma_separated_header_matches_when_any_item_equals_the_etag():
    etag = _etag()
    assert if_none_match(f'W/"other", {etag}', etag) is True
    assert if_none_match(f'  W/"other" ,  {etag}  ', etag) is True
    assert if_none_match(f'{etag},W/"other"', etag) is True


def test_comma_separated_header_without_a_match_is_not_current():
    etag = _etag()
    assert if_none_match('W/"other", "another"', etag) is False


def test_weak_prefix_is_ignored_on_both_sides():
    etag = _etag()
    strong = etag[2:]
    assert if_none_match(strong, etag) is True
    assert if_none_match(f"W/{strong}", etag) is True
    assert if_none_match(etag, strong) is True


def test_different_token_is_not_current():
    etag = _etag()
    assert if_none_match('W/"deadbeef"', etag) is False
    assert if_none_match(etag, list_etag(_rows((1, 2)))) is False


def test_empty_items_in_the_header_never_match():
    etag = _etag()
    assert if_none_match(",,", etag) is False


@pytest.mark.parametrize('header,etag', [(None, ""), ("*", 42), (42, 'W/"abc"')])
def test_if_none_match_rejects_invalid_inputs(header, etag):
    with pytest.raises(RefreshError):
        if_none_match(header, etag)


def test_if_none_match_rejects_control_characters_in_header():
    with pytest.raises(RefreshError) as err:
        if_none_match('W/"a\x00b"', _etag())
    assert _code(err) == BAD_HEADER


def test_if_none_match_rejects_empty_etag_with_stable_code():
    with pytest.raises(RefreshError) as err:
        if_none_match("*", "")
    assert _code(err) == BAD_ETAG


# --- RefreshPolicy and clamp_interval -----------------------------------------


@pytest.mark.parametrize('value,expected', [(3, 10), (9999, 300), (45, 45), (10, 10), (300, 300)])
def test_clamp_interval_default_policy(value, expected):
    assert clamp_interval(value) == expected


def test_clamp_interval_uses_the_given_policy_bounds():
    policy = RefreshPolicy(interval_s=30, min_s=20, max_s=100)
    assert clamp_interval(5, policy) == 20
    assert clamp_interval(150, policy) == 100
    assert clamp_interval(60, policy) == 60


@pytest.mark.parametrize('bad', [True, -1, -100, 1.5, float("nan"), float("inf"), "30", None])
def test_clamp_interval_rejects_bool_non_int_and_negative(bad):
    with pytest.raises(RefreshError) as err:
        clamp_interval(bad)
    assert _code(err) == BAD_INTERVAL


def test_clamp_interval_rejects_non_policy():
    with pytest.raises(RefreshError) as err:
        clamp_interval(45, "nope")
    assert _code(err) == BAD_POLICY


@pytest.mark.parametrize('fields', [
    {"min_s": 0},
    {"min_s": 31},
    {"interval_s": 5, "min_s": 10},
    {"interval_s": 30, "min_s": 10, "max_s": 29},
    {"max_s": 5},
    {"interval_s": 20, "max_s": 10},
    {"auto_enabled": 1},
    {"interval_s": 10.0},
    {"min_s": True},
])
def test_invalid_policy_is_rejected_at_construction(fields):
    with pytest.raises(RefreshError) as err:
        RefreshPolicy(**fields)
    assert _code(err) == BAD_POLICY


@pytest.mark.parametrize('policy', [
    RefreshPolicy(),
    RefreshPolicy(interval_s=10, min_s=10, max_s=10),
    RefreshPolicy(interval_s=1, min_s=1),
    RefreshPolicy(auto_enabled=False),
])
def test_valid_policy_boundaries_are_accepted(policy):
    assert policy.interval_s >= policy.min_s


# --- next_delay ----------------------------------------------------------------


def test_next_delay_is_none_when_auto_refresh_is_off():
    assert next_delay(RefreshPolicy(auto_enabled=False)) is None
    assert next_delay(RefreshPolicy(auto_enabled=False), unchanged_polls=3, failures=3) is None


def test_next_delay_zero_unchanged_polls_gives_interval_s():
    delay = next_delay(RefreshPolicy())
    assert delay == 30
    assert isinstance(delay, float)


@pytest.mark.parametrize('unchanged,expected', [
    (0, 30.0),
    (1, 45.0),
    (2, 67.5),
    (3, 101.25),
    (5, 227.8125),
    (6, 300.0),
    (50, 300.0),
])
def test_next_delay_unchanged_polls_multiply_by_one_and_a_half_up_to_max(unchanged, expected):
    assert next_delay(RefreshPolicy(), unchanged_polls=unchanged) == expected


@pytest.mark.parametrize('failures,expected', [
    (0, 30.0),
    (1, 60.0),
    (2, 120.0),
    (3, 240.0),
    (4, 300.0),
    (10, 300.0),
])
def test_next_delay_failures_multiply_by_two_up_to_max(failures, expected):
    assert next_delay(RefreshPolicy(), failures=failures) == expected


@pytest.mark.parametrize('unchanged,failures,expected', [
    (1, 1, 90.0),
    (2, 2, 270.0),
    (3, 3, 300.0),
    (10, 10, 300.0),
])
def test_next_delay_combines_multiplicatively_and_stays_capped(unchanged, failures, expected):
    delay = next_delay(RefreshPolicy(), unchanged_polls=unchanged, failures=failures)
    assert delay == expected
    assert delay <= 300.0


def test_next_delay_is_deterministic_without_jitter():
    first = next_delay(RefreshPolicy(), unchanged_polls=3, failures=1)
    second = next_delay(RefreshPolicy(), unchanged_polls=3, failures=1)
    assert first == second == 202.5


def test_next_delay_is_never_below_min_s():
    policy = RefreshPolicy(interval_s=10, min_s=10, max_s=300)
    for unchanged in range(0, 8):
        for failures in range(0, 8):
            delay = next_delay(policy, unchanged_polls=unchanged, failures=failures)
            assert policy.min_s <= delay <= policy.max_s


@pytest.mark.parametrize('kwargs', [
    {"unchanged_polls": -1},
    {"failures": -1},
    {"unchanged_polls": True},
    {"failures": 1.5},
    {"unchanged_polls": float("nan")},
    {"failures": float("inf")},
])
def test_next_delay_rejects_bad_counts(kwargs):
    with pytest.raises(RefreshError) as err:
        next_delay(RefreshPolicy(), **kwargs)
    assert _code(err) == BAD_COUNTS


def test_next_delay_rejects_non_policy():
    with pytest.raises(RefreshError) as err:
        next_delay("nope")
    assert _code(err) == BAD_POLICY


# --- manual refresh is not limited by the policy -------------------------------


def test_manual_refresh_is_never_delayed_is_documented():
    assert "Manual refresh is never delayed." in worklist_etag.__doc__
    assert "Manual refresh is never delayed." in worklist_etag.next_delay.__doc__


def test_refresh_error_is_a_value_error():
    assert issubclass(RefreshError, ValueError)

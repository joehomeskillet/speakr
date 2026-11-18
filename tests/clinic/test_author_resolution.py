"""Tests for author resolution from device id and embedded author name."""

from __future__ import annotations

import pytest

from src.clinic.author_resolution import (
    RESOLVED,
    UNRESOLVED,
    CONFLICT,
    Resolution,
    AuthorResolutionError,
    normalise_alias,
    resolve_author,
)


class TestNormaliseAlias:
    def test_nfc_normalisation(self):
        # Composed vs decomposed umlaut
        assert normalise_alias("M\u00fcller") == normalise_alias("Mu\u0308ller")

    def test_strip_whitespace(self):
        assert normalise_alias("  M\u00fcller  ") == "m\u00fcller"

    def test_collapse_inner_whitespace(self):
        assert normalise_alias("M\u00fcller,\t\tHans") == "m\u00fcller, hans"
        assert normalise_alias("M\u00fcller,\nHans") == "m\u00fcller, hans"

    def test_casefold(self):
        assert normalise_alias("M\u00dcLLER") == "m\u00fcller"
        assert normalise_alias("M\u00fcller") == "m\u00fcller"

    def test_no_reordering(self):
        # "Müller, Hans" and "Hans Müller" are NOT equal
        assert normalise_alias("M\u00fcller, Hans") != normalise_alias("Hans M\u00fcller")

    def test_equal_after_normalisation(self):
        assert normalise_alias("M\u00fcller,  Hans ") == normalise_alias("m\u00fcller, hans")

    def test_empty_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            normalise_alias("")
        assert exc.value.code == "empty"
        with pytest.raises(AuthorResolutionError) as exc:
            normalise_alias("   ")
        assert exc.value.code == "empty"

    def test_control_character_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            normalise_alias("M\u00fcller\x00Hans")
        assert exc.value.code == "control_character"
        with pytest.raises(AuthorResolutionError) as exc:
            normalise_alias("M\u00fcller\x7fHans")
        assert exc.value.code == "control_character"

    def test_non_string_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            normalise_alias(123)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"


class TestResolveAuthor:
    DEVICE_MAP = {"AB-12": 1, "CD-34": 2}
    ALIAS_MAP = {"m\u00fcller, hans": [1], "schmidt, anna": [2, 3]}

    def test_only_device_resolves(self):
        result = resolve_author(
            device_id="ab-12",
            embedded_author=None,
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(RESOLVED, 1, "device", "device_only")

    def test_device_id_case_insensitive(self):
        result = resolve_author(
            device_id="Ab-12",
            embedded_author=None,
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(RESOLVED, 1, "device", "device_only")

    def test_device_id_stripped(self):
        result = resolve_author(
            device_id="  AB-12  ",
            embedded_author=None,
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(RESOLVED, 1, "device", "device_only")

    def test_unknown_device_id_unresolved(self):
        result = resolve_author(
            device_id="UNKNOWN",
            embedded_author=None,
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(UNRESOLVED, None, None, "no_input")

    def test_only_alias_resolves_single_user(self):
        result = resolve_author(
            device_id=None,
            embedded_author="M\u00fcller, Hans",
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(RESOLVED, 1, "embedded", "alias_only")

    def test_only_alias_resolves_ambiguous(self):
        result = resolve_author(
            device_id=None,
            embedded_author="Schmidt, Anna",
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(CONFLICT, None, None, "ambiguous_alias")

    def test_both_resolve_same_user_device_wins(self):
        result = resolve_author(
            device_id="AB-12",
            embedded_author="M\u00fcller, Hans",
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(RESOLVED, 1, "device", "device_alias_agree")

    def test_both_resolve_different_users_conflict(self):
        result = resolve_author(
            device_id="CD-34",
            embedded_author="M\u00fcller, Hans",
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(CONFLICT, None, None, "device_alias_conflict")

    def test_device_resolves_one_of_ambiguous_alias(self):
        # alias maps to [2, 3], device maps to 2
        result = resolve_author(
            device_id="CD-34",
            embedded_author="Schmidt, Anna",
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(RESOLVED, 2, "device", "device_alias_agree")

    def test_both_absent_unresolved(self):
        result = resolve_author(
            device_id=None,
            embedded_author=None,
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(UNRESOLVED, None, None, "no_input")

    def test_empty_string_counts_as_absent(self):
        result = resolve_author(
            device_id="",
            embedded_author="   ",
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        assert result == Resolution(UNRESOLVED, None, None, "no_input")

    def test_near_miss_unresolved(self):
        # "Mueller" vs "M\u00fcller" - no fuzzy match
        alias_map = {"mueller, hans": [1]}
        result = resolve_author(
            device_id=None,
            embedded_author="M\u00fcller, Hans",
            device_map={},
            alias_map=alias_map,
        )
        assert result == Resolution(UNRESOLVED, None, None, "no_input")

    def test_invalid_device_map_non_int_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id="AB-12",
                embedded_author=None,
                device_map={"AB-12": "not-an-int"},
                alias_map={},
            )
        assert exc.value.code == "invalid_user_id"

    def test_invalid_device_map_negative_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id="AB-12",
                embedded_author=None,
                device_map={"AB-12": -1},
                alias_map={},
            )
        assert exc.value.code == "invalid_user_id"

    def test_invalid_device_map_bool_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id="AB-12",
                embedded_author=None,
                device_map={"AB-12": True},
                alias_map={},
            )
        assert exc.value.code == "invalid_user_id"

    def test_invalid_alias_map_non_int_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id=None,
                embedded_author="M\u00fcller, Hans",
                device_map={},
                alias_map={"m\u00fcller, hans": ["not-an-int"]},
            )
        assert exc.value.code == "invalid_user_id"

    def test_invalid_alias_map_negative_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id=None,
                embedded_author="M\u00fcller, Hans",
                device_map={},
                alias_map={"m\u00fcller, hans": [-1]},
            )
        assert exc.value.code == "invalid_user_id"

    def test_invalid_alias_map_bool_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id=None,
                embedded_author="M\u00fcller, Hans",
                device_map={},
                alias_map={"m\u00fcller, hans": [True]},
            )
        assert exc.value.code == "invalid_user_id"

    def test_maps_not_mutated(self):
        device_map = {"AB-12": 1}
        alias_map = {"m\u00fcller, hans": [1]}
        original_device = dict(device_map)
        original_alias = {k: list(v) for k, v in alias_map.items()}
        resolve_author(
            device_id="AB-12",
            embedded_author="M\u00fcller, Hans",
            device_map=device_map,
            alias_map=alias_map,
        )
        assert device_map == original_device
        assert {k: list(v) for k, v in alias_map.items()} == original_alias

    def test_device_map_key_non_string_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id="AB-12",
                embedded_author=None,
                device_map={123: 1},
                alias_map={},
            )
        assert exc.value.code == "invalid_type"

    def test_alias_map_key_non_string_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id=None,
                embedded_author="M\u00fcller, Hans",
                device_map={},
                alias_map={123: [1]},
            )
        assert exc.value.code == "invalid_type"

    def test_alias_map_value_not_collection_raises(self):
        with pytest.raises(AuthorResolutionError) as exc:
            resolve_author(
                device_id=None,
                embedded_author="M\u00fcller, Hans",
                device_map={},
                alias_map={"m\u00fcller, hans": 1},
            )
        assert exc.value.code == "invalid_type"

    def test_resolution_dataclass_immutable(self):
        result = resolve_author(
            device_id="AB-12",
            embedded_author=None,
            device_map=self.DEVICE_MAP,
            alias_map=self.ALIAS_MAP,
        )
        with pytest.raises(Exception):
            result.status = "changed"
"""Tests for src.clinic.saved_filters."""

from __future__ import annotations

import pytest

from src.clinic.saved_filters import (
    MAX_FILTERS_PER_USER,
    FIELDS,
    OPS,
    FilterError,
    FilterStore,
    apply_filter,
    matches,
    search_metadata,
    validate_filter,
)


class TestValidateFilter:
    def test_empty_spec_returns_normalised(self):
        result = validate_filter({})
        assert result == {"all": [], "any": []}

    def test_none_spec_returns_normalised(self):
        result = validate_filter(None)
        assert result == {"all": [], "any": []}

    def test_valid_all_conditions(self):
        spec = {"all": [{"field": "status", "op": "eq", "value": "done"}]}
        result = validate_filter(spec)
        assert result["all"][0]["field"] == "status"
        assert result["all"][0]["op"] == "eq"
        assert result["all"][0]["value"] == "done"

    def test_valid_any_conditions(self):
        spec = {"any": [{"field": "priority", "op": "eq", "value": 1}]}
        result = validate_filter(spec)
        assert result["any"][0]["field"] == "priority"

    def test_both_all_and_any(self):
        spec = {
            "all": [{"field": "status", "op": "eq", "value": "done"}],
            "any": [{"field": "priority", "op": "eq", "value": 128}],
        }
        result = validate_filter(spec)
        assert len(result["all"]) == 1
        assert len(result["any"]) == 1

    def test_unknown_field_raises(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "unknown", "op": "eq", "value": 1}]})
        assert exc.value.code == "unknown_field"

    def test_meta_field_valid(self):
        result = validate_filter({"all": [{"field": "meta.patient_id", "op": "eq", "value": "123"}]})
        assert result["all"][0]["field"] == "meta.patient_id"

    def test_meta_field_invalid_key(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "meta.PATIENT", "op": "eq", "value": "123"}]})
        assert exc.value.code == "unknown_field"

    def test_meta_field_too_long_key(self):
        long_key = "a" * 41
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": f"meta.{long_key}", "op": "eq", "value": "123"}]})
        assert exc.value.code == "unknown_field"

    def test_unknown_op_raises(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "unknown", "value": 1}]})
        assert exc.value.code == "unknown_op"

    def test_regex_op_explicitly_refused(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "regex", "value": ".*"}]})
        assert exc.value.code == "unknown_op"

    def test_matches_op_explicitly_refused(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "matches", "value": ".*"}]})
        assert exc.value.code == "unknown_op"

    def test_bad_value_for_in_not_list(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "in", "value": "not a list"}]})
        assert exc.value.code == "bad_value"

    def test_bad_value_for_in_too_many_items(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "in", "value": list(range(51))}]})
        assert exc.value.code == "bad_value"

    def test_bad_value_for_in_nested_items(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "in", "value": [[1], [2]]}]})
        assert exc.value.code == "bad_value"

    def test_bad_value_for_is_null_not_bool(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "is_null", "value": "true"}]})
        assert exc.value.code == "bad_value"

    def test_bad_value_for_scalar_op_gets_list(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "eq", "value": ["a", "b"]}]})
        assert exc.value.code == "bad_value"

    def test_too_many_conditions_raises(self):
        conditions = [{"field": "status", "op": "eq", "value": "x"} for _ in range(21)]
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": conditions})
        assert exc.value.code == "too_many_conditions"

    def test_nested_condition_raises(self):
        with pytest.raises(FilterError) as exc:
            validate_filter(
                {"all": [{"field": "status", "op": "eq", "value": {"nested": "dict"}}]}
            )
        assert exc.value.code == "bad_shape"

    def test_unexpected_top_level_key_raises(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [], "unexpected": "key"})
        assert exc.value.code == "bad_shape"

    def test_all_not_a_list_raises(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": "not a list"})
        assert exc.value.code == "bad_shape"

    def test_condition_missing_keys_raises(self):
        with pytest.raises(FilterError) as exc:
            validate_filter({"all": [{"field": "status", "op": "eq"}]})
        assert exc.value.code == "bad_shape"


class TestMatches:
    SAMPLE_ROWS = [
        {
            "status": "done",
            "priority": 1,
            "author": "Dr. Müller",
            "pool": "radiology",
            "title": "CT Abdomen",
            "created": "2024-01-15",
            "deadline": "2024-01-20",
            "assignee": "Schreibkraft A",
            "audio_seconds": 120,
            "meta.patient_id": "P123",
        },
        {
            "status": "pending",
            "priority": 0,
            "author": "Dr. Schmidt",
            "pool": "cardiology",
            "title": "EKG",
            "created": "2024-01-16",
            "deadline": "2024-01-18",
            "assignee": "Schreibkraft B",
            "audio_seconds": 45,
            "meta.patient_id": "P456",
        },
        {
            "status": "in_review",
            "priority": 128,
            "author": "Dr. Müller",
            "pool": "radiology",
            "title": "MRT Kopf",
            "created": "2024-01-17",
            "deadline": None,
            "assignee": "Schreibkraft C",
            "audio_seconds": 300,
            "meta.patient_id": "P789",
        },
        {
            "status": "done",
            "priority": 1,
            "author": "Dr. Weber",
            "pool": "neurology",
            "title": "EEG",
            "created": "2024-01-18",
            "deadline": "2024-01-25",
            "assignee": "Schreibkraft A",
            "audio_seconds": 60,
            "meta.patient_id": "P123",
        },
        {
            "status": "failed",
            "priority": 0,
            "author": "Dr. Schmidt",
            "pool": "cardiology",
            "title": "Belastungs-EKG",
            "created": "2024-01-19",
            "deadline": "2024-01-22",
            "assignee": None,
            "audio_seconds": 90,
            "meta.patient_id": "P999",
        },
        {
            "status": "pending",
            "priority": 1,
            "author": "Dr. Müller",
            "pool": "radiology",
            "title": "Röntgen Thorax",
            "created": "2024-01-20",
            "deadline": "2024-01-22",
            "assignee": "Schreibkraft B",
            "audio_seconds": 15,
            "meta.patient_id": "P123",
        },
    ]

    @pytest.mark.parametrize(
        "op,field,value,expected_indices",
        [
            ("eq", "status", "done", [0, 3]),
            ("ne", "status", "done", [1, 2, 4, 5]),
            ("eq", "priority", 1, [0, 3, 5]),
            ("eq", "priority", 0, [1, 4]),
            ("eq", "priority", 128, [2]),
            ("lt", "priority", 1, [1, 4]),
            ("le", "priority", 1, [0, 1, 3, 4, 5]),
            ("gt", "priority", 1, [2]),
            ("ge", "priority", 1, [0, 2, 3, 5]),
            ("contains", "author", "müller", [0, 2, 5]),
            ("contains", "title", "EKG", [1, 4]),
            ("startswith", "title", "CT", [0]),
            ("startswith", "title", "Röntgen", [5]),
            ("in", "status", ["done", "pending"], [0, 1, 3, 5]),
            ("is_null", "deadline", True, [2]),
            ("is_null", "deadline", False, [0, 1, 3, 4, 5]),
            ("is_null", "assignee", True, [4]),
            ("is_null", "assignee", False, [0, 1, 2, 3, 5]),
        ],
    )
    def test_all_operators(self, op, field, value, expected_indices):
        spec = {"all": [{"field": field, "op": op, "value": value}]}
        validated = validate_filter(spec)
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert matched == expected_indices, f"op={op} field={field} value={value}"

    def test_priority_codes_compared_numerically(self):
        spec = {"all": [{"field": "priority", "op": "gt", "value": 1}]}
        validated = validate_filter(spec)
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert matched == [2]

    def test_date_comparison_iso_string_and_date_object(self):
        from datetime import date

        spec1 = {"all": [{"field": "created", "op": "ge", "value": "2024-01-18"}]}
        validated1 = validate_filter(spec1)
        matched1 = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated1, row)]

        spec2 = {"all": [{"field": "created", "op": "ge", "value": date(2024, 1, 18)}]}
        validated2 = validate_filter(spec2)
        matched2 = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated2, row)]

        assert matched1 == matched2 == [3, 4, 5]

    def test_contains_case_insensitive_nfc(self):
        spec = {"all": [{"field": "author", "op": "contains", "value": "MÜLLER"}]}
        validated = validate_filter(spec)
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert matched == [0, 2, 5]

    def test_none_comparison_returns_false(self):
        spec = {"all": [{"field": "deadline", "op": "lt", "value": "2024-01-25"}]}
        validated = validate_filter(spec)
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert 2 not in matched

    def test_all_is_and(self):
        spec = {
            "all": [
                {"field": "status", "op": "eq", "value": "done"},
                {"field": "priority", "op": "eq", "value": 1},
            ]
        }
        validated = validate_filter(spec)
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert matched == [0, 3]

    def test_any_is_or(self):
        spec = {
            "any": [
                {"field": "status", "op": "eq", "value": "done"},
                {"field": "status", "op": "eq", "value": "failed"},
            ]
        }
        validated = validate_filter(spec)
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert matched == [0, 3, 4]

    def test_both_all_and_any_means_all_and_any(self):
        spec = {
            "all": [{"field": "author", "op": "eq", "value": "Dr. Müller"}],
            "any": [
                {"field": "status", "op": "eq", "value": "done"},
                {"field": "status", "op": "eq", "value": "pending"},
            ],
        }
        validated = validate_filter(spec)
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert matched == [0, 5]

    def test_empty_spec_matches_all(self):
        validated = validate_filter({})
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert matched == list(range(6))

    def test_meta_field_search(self):
        spec = {"all": [{"field": "meta.patient_id", "op": "eq", "value": "P123"}]}
        validated = validate_filter(spec)
        matched = [i for i, row in enumerate(self.SAMPLE_ROWS) if matches(validated, row)]
        assert matched == [0, 3, 5]


class TestApplyFilter:
    def test_returns_matching_rows(self):
        rows = [
            {"status": "done", "priority": 1},
            {"status": "pending", "priority": 0},
        ]
        spec = {"all": [{"field": "status", "op": "eq", "value": "done"}]}
        result = apply_filter(spec, rows)
        assert len(result) == 1
        assert result[0]["status"] == "done"

    def test_validates_before_applying(self):
        rows = [{"status": "done"}]
        spec = {"all": [{"field": "unknown", "op": "eq", "value": 1}]}
        with pytest.raises(FilterError) as exc:
            apply_filter(spec, rows)
        assert exc.value.code == "unknown_field"


class TestSearchMetadata:
    SAMPLE_ROWS = [
        {"title": "CT Abdomen", "author": "Dr. Müller", "meta.patient_id": "P123"},
        {"title": "EKG", "author": "Dr. Schmidt", "meta.patient_id": "P456"},
        {"title": "MRT Kopf", "author": "Dr. Müller", "meta.patient_id": "P789"},
    ]

    def test_case_insensitive_substring(self):
        result = search_metadata(self.SAMPLE_ROWS, "müller", ["author"])
        assert len(result) == 2

    def test_multiple_terms_and_combined(self):
        result = search_metadata(self.SAMPLE_ROWS, "dr mrt", ["title", "author"])
        assert len(result) == 1
        assert result[0]["title"] == "MRT Kopf"

    def test_only_listed_keys_searched(self):
        result = search_metadata(self.SAMPLE_ROWS, "P123", ["title", "author"])
        assert len(result) == 0

        result = search_metadata(self.SAMPLE_ROWS, "P123", ["meta.patient_id"])
        assert len(result) == 1

    def test_non_whitelisted_key_raises(self):
        with pytest.raises(FilterError) as exc:
            search_metadata(self.SAMPLE_ROWS, "test", ["not_allowed"])
        assert exc.value.code == "bad_value"

    def test_empty_query_returns_all(self):
        result = search_metadata(self.SAMPLE_ROWS, "", ["title"])
        assert len(result) == 3

    def test_query_over_200_chars_raises(self):
        with pytest.raises(FilterError) as exc:
            search_metadata(self.SAMPLE_ROWS, "a" * 201, ["title"])
        assert exc.value.code == "bad_value"

    def test_nfc_normalisation(self):
        rows = [{"title": "Café"}]
        result = search_metadata(rows, "café", ["title"])
        assert len(result) == 1


class TestFilterStore:
    def test_save_and_list(self):
        store = FilterStore()
        fid = store.save(1, "My Filter", {"all": [{"field": "status", "op": "eq", "value": "done"}]})
        assert fid == "f1"
        listed = store.list(1)
        assert len(listed) == 1
        assert listed[0] == (fid, "My Filter", {"all": [{"field": "status", "op": "eq", "value": "done"}], "any": []})

    def test_limit_reached_at_21(self):
        store = FilterStore()
        for i in range(20):
            store.save(1, f"Filter {i}", {"all": []})
        with pytest.raises(FilterError) as exc:
            store.save(1, "Filter 20", {"all": []})
        assert exc.value.code == "limit_reached"

    def test_delete_frees_slot(self):
        store = FilterStore()
        for i in range(20):
            store.save(1, f"Filter {i}", {"all": []})
        store.delete(1, "f1")
        fid = store.save(1, "New Filter", {"all": []})
        assert fid == "f1"

    def test_name_stripped_and_nfc(self):
        store = FilterStore()
        fid = store.save(1, "  Filter Name  ", {"all": []})
        listed = store.list(1)
        assert listed[0][1] == "Filter Name"

    def test_name_length_limit(self):
        store = FilterStore()
        with pytest.raises(FilterError) as exc:
            store.save(1, "a" * 61, {"all": []})
        assert exc.value.code == "bad_value"

    def test_duplicate_name_casefold_raises(self):
        store = FilterStore()
        store.save(1, "My Filter", {"all": []})
        with pytest.raises(FilterError) as exc:
            store.save(1, "my filter", {"all": []})
        assert exc.value.code == "duplicate_name"

    def test_users_isolated(self):
        store = FilterStore()
        store.save(1, "Filter A", {"all": []})
        store.save(2, "Filter B", {"all": []})
        assert len(store.list(1)) == 1
        assert len(store.list(2)) == 1
        assert store.list(1)[0][1] == "Filter A"
        assert store.list(2)[0][1] == "Filter B"

    def test_unknown_id_raises_not_found(self):
        store = FilterStore()
        with pytest.raises(FilterError) as exc:
            store.rename(1, "f999", "New Name")
        assert exc.value.code == "not_found"

        with pytest.raises(FilterError) as exc:
            store.delete(1, "f999")
        assert exc.value.code == "not_found"

    def test_another_users_id_raises_not_found(self):
        store = FilterStore()
        store.save(1, "Filter A", {"all": []})
        with pytest.raises(FilterError) as exc:
            store.rename(2, "f1", "New Name")
        assert exc.value.code == "not_found"

    def test_rename_updates_name(self):
        store = FilterStore()
        fid = store.save(1, "Old Name", {"all": []})
        store.rename(1, fid, "New Name")
        listed = store.list(1)
        assert listed[0][1] == "New Name"

    def test_rename_duplicate_name_raises(self):
        store = FilterStore()
        fid1 = store.save(1, "Name One", {"all": []})
        store.save(1, "Name Two", {"all": []})
        with pytest.raises(FilterError) as exc:
            store.rename(1, fid1, "Name Two")
        assert exc.value.code == "duplicate_name"

    def test_list_sorted_by_name_casefold(self):
        store = FilterStore()
        store.save(1, "Zebra", {"all": []})
        store.save(1, "alpha", {"all": []})
        store.save(1, "Beta", {"all": []})
        listed = store.list(1)
        names = [item[1] for item in listed]
        assert names == ["alpha", "Beta", "Zebra"]

    def test_save_validates_spec(self):
        store = FilterStore()
        with pytest.raises(FilterError) as exc:
            store.save(1, "Bad", {"all": [{"field": "unknown", "op": "eq", "value": 1}]})
        assert exc.value.code == "unknown_field"

    def test_hostile_value_large_string_finishes_quickly(self):
        import time

        store = FilterStore()
        large_value = "a" * 100000
        spec = {"all": [{"field": "title", "op": "contains", "value": large_value}]}
        start = time.time()
        store.save(1, "Large", spec)
        elapsed = time.time() - start
        assert elapsed < 1.0

    def test_hostile_value_deeply_nested_dict_finishes_quickly(self):
        import time

        store = FilterStore()
        deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
        spec = {"all": [{"field": "title", "op": "eq", "value": deep}]}
        start = time.time()
        with pytest.raises(FilterError):
            store.save(1, "Deep", spec)
        elapsed = time.time() - start
        assert elapsed < 1.0


class TestConstants:
    def test_max_filters_per_user(self):
        assert MAX_FILTERS_PER_USER == 20

    def test_fields_tuple(self):
        assert "status" in FIELDS
        assert "priority" in FIELDS
        assert "meta.patient_id" not in FIELDS

    def test_ops_tuple(self):
        assert "eq" in OPS
        assert "contains" in OPS
        assert "regex" not in OPS
        assert "matches" not in OPS
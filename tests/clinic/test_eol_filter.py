import dataclasses
import itertools

import pytest

from src.clinic.eol_filter import (
    CLARIFY,
    IMPORT,
    SKIP,
    Decision,
    EolFilterError,
    FileFacts,
    Policy,
    decide,
)


def test_policy_defaults():
    policy = Policy()
    assert policy.eol_filter_enabled is True
    assert policy.auto_import_non_eol is False


class TestFilterOnAutoImportOff:
    """Default policy: EOL filter on, auto-import of non-EOL files off."""

    def test_eol_confirmed_imports(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=True,
            stable=True,
            author_known=True,
            author_conflict=False,
        )
        decision = decide(facts)
        assert decision == Decision(IMPORT, "eol_confirmed")

    def test_not_eol_skips_when_eol_false(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=False,
            stable=True,
            author_known=True,
            author_conflict=False,
        )
        decision = decide(facts)
        assert decision == Decision(SKIP, "not_eol")

    def test_not_eol_skips_when_eol_unknown(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=None,
            stable=True,
            author_known=True,
            author_conflict=False,
        )
        decision = decide(facts)
        assert decision == Decision(SKIP, "not_eol")

    def test_unknown_author_clarifies_regardless_of_eol(self):
        for eol in (True, False, None):
            facts = FileFacts(
                name="dictation.wav",
                size=100,
                eol_flag=eol,
                stable=True,
                author_known=False,
                author_conflict=False,
            )
            assert decide(facts) == Decision(CLARIFY, "author_unknown")

    def test_author_conflict_clarifies_regardless_of_eol(self):
        for eol in (True, False, None):
            facts = FileFacts(
                name="dictation.wav",
                size=100,
                eol_flag=eol,
                stable=True,
                author_known=True,
                author_conflict=True,
            )
            assert decide(facts) == Decision(CLARIFY, "author_conflict")

    def test_empty_file_skips(self):
        facts = FileFacts(
            name="dictation.wav",
            size=0,
            eol_flag=True,
            stable=True,
            author_known=True,
            author_conflict=False,
        )
        assert decide(facts) == Decision(SKIP, "empty")


class TestAutoImportOn:
    def test_stable_non_eol_imports(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=False,
            stable=True,
            author_known=True,
            author_conflict=False,
        )
        policy = Policy(auto_import_non_eol=True)
        assert decide(facts, policy) == Decision(IMPORT, "auto_import_stable")

    def test_unknown_eol_with_stable_imports(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=None,
            stable=True,
            author_known=True,
            author_conflict=False,
        )
        policy = Policy(auto_import_non_eol=True)
        assert decide(facts, policy) == Decision(IMPORT, "auto_import_stable")

    def test_unstable_non_eol_skips(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=False,
            stable=False,
            author_known=True,
            author_conflict=False,
        )
        policy = Policy(auto_import_non_eol=True)
        assert decide(facts, policy) == Decision(SKIP, "not_stable")

    def test_unknown_author_still_clarifies(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=False,
            stable=True,
            author_known=False,
            author_conflict=False,
        )
        policy = Policy(auto_import_non_eol=True)
        assert decide(facts, policy) == Decision(CLARIFY, "author_unknown")

    def test_conflict_still_clarifies(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=False,
            stable=True,
            author_known=True,
            author_conflict=True,
        )
        policy = Policy(auto_import_non_eol=True)
        assert decide(facts, policy) == Decision(CLARIFY, "author_conflict")


class TestFilterOff:
    def test_known_author_imports(self):
        for eol in (True, False, None):
            facts = FileFacts(
                name="dictation.wav",
                size=100,
                eol_flag=eol,
                stable=True,
                author_known=True,
                author_conflict=False,
            )
            policy = Policy(eol_filter_enabled=False)
            assert decide(facts, policy) == Decision(IMPORT, "filter_off")

    def test_unknown_author_clarifies(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=True,
            stable=True,
            author_known=False,
            author_conflict=False,
        )
        policy = Policy(eol_filter_enabled=False)
        assert decide(facts, policy) == Decision(CLARIFY, "author_unknown")

    def test_conflict_clarifies(self):
        facts = FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=True,
            stable=True,
            author_known=True,
            author_conflict=True,
        )
        policy = Policy(eol_filter_enabled=False)
        assert decide(facts, policy) == Decision(CLARIFY, "author_conflict")


class TestNoTimeField:
    def test_facts_has_no_time_field(self):
        field_names = {f.name for f in dataclasses.fields(FileFacts)}
        assert "mtime" not in field_names
        assert "created" not in field_names
        assert "time" not in field_names

    def test_mtime_kwarg_raises_type_error(self):
        with pytest.raises(TypeError):
            FileFacts(
                name="dictation.wav",
                size=100,
                eol_flag=True,
                stable=True,
                author_known=True,
                mtime=123,
            )

    def test_created_kwarg_raises_type_error(self):
        with pytest.raises(TypeError):
            FileFacts(
                name="dictation.wav",
                size=100,
                eol_flag=True,
                stable=True,
                author_known=True,
                created=123,
            )


class TestDeleteSource:
    def test_delete_source_is_false_for_all_truth_table_cells(self):
        eol_flags = (True, False, None)
        stables = (True, False)
        author_knowns = (True, False)
        conflicts = (True, False)
        policies = [
            Policy(),
            Policy(auto_import_non_eol=True),
            Policy(eol_filter_enabled=False),
            Policy(eol_filter_enabled=False, auto_import_non_eol=True),
        ]
        combos = itertools.product(
            eol_flags, stables, author_knowns, conflicts, policies
        )
        for eol, stable, known, conflict, policy in combos:
            facts = FileFacts(
                name="x.wav",
                size=1,
                eol_flag=eol,
                stable=stable,
                author_known=known,
                author_conflict=conflict,
            )
            decision = decide(facts, policy)
            assert decision.delete_source is False


class TestInvalidFacts:
    def _valid_facts(self):
        return FileFacts(
            name="dictation.wav",
            size=100,
            eol_flag=True,
            stable=True,
            author_known=True,
            author_conflict=False,
        )

    def test_negative_size(self):
        facts = dataclasses.replace(self._valid_facts(), size=-1)
        with pytest.raises(ValueError):
            decide(facts)

    def test_bool_size(self):
        facts = dataclasses.replace(self._valid_facts(), size=True)
        with pytest.raises(ValueError):
            decide(facts)

    def test_empty_name(self):
        facts = dataclasses.replace(self._valid_facts(), name="")
        with pytest.raises(ValueError):
            decide(facts)

    def test_name_with_control_character(self):
        facts = dataclasses.replace(self._valid_facts(), name="dict\x00ation.wav")
        with pytest.raises(ValueError):
            decide(facts)

    def test_non_bool_stable(self):
        facts = dataclasses.replace(self._valid_facts(), stable=1)
        with pytest.raises(ValueError):
            decide(facts)

    def test_non_bool_author_known(self):
        facts = dataclasses.replace(self._valid_facts(), author_known="yes")
        with pytest.raises(ValueError):
            decide(facts)

    def test_non_bool_author_conflict(self):
        facts = dataclasses.replace(self._valid_facts(), author_conflict=1)
        with pytest.raises(ValueError):
            decide(facts)

    def test_string_eol_flag(self):
        facts = dataclasses.replace(self._valid_facts(), eol_flag="true")
        with pytest.raises(ValueError):
            decide(facts)

    def test_non_bool_eol_filter_enabled(self):
        policy = Policy(eol_filter_enabled="yes")
        with pytest.raises(ValueError):
            decide(self._valid_facts(), policy)

    def test_non_bool_auto_import_non_eol(self):
        policy = Policy(auto_import_non_eol=1)
        with pytest.raises(ValueError):
            decide(self._valid_facts(), policy)


class TestReasonPriority:
    """Reason priority: empty > conflict > unknown author > EOL/auto rules."""

    def test_empty_wins_over_conflict(self):
        facts = FileFacts(
            name="x.wav",
            size=0,
            eol_flag=True,
            stable=True,
            author_known=True,
            author_conflict=True,
        )
        assert decide(facts).reason == "empty"

    def test_conflict_wins_over_unknown_author(self):
        facts = FileFacts(
            name="x.wav",
            size=1,
            eol_flag=True,
            stable=True,
            author_known=False,
            author_conflict=True,
        )
        assert decide(facts).reason == "author_conflict"

    def test_unknown_author_wins_over_eol_rule(self):
        facts = FileFacts(
            name="x.wav",
            size=1,
            eol_flag=False,
            stable=True,
            author_known=False,
            author_conflict=False,
        )
        assert decide(facts).reason == "author_unknown"

    def test_author_resolved_uses_eol_rule(self):
        facts = FileFacts(
            name="x.wav",
            size=1,
            eol_flag=False,
            stable=True,
            author_known=True,
            author_conflict=False,
        )
        assert decide(facts).reason == "not_eol"


class TestErrorCode:
    def test_error_has_stable_code(self):
        assert EolFilterError.code == "eol_filter_invalid"

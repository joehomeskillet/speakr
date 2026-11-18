"""Drop folder scan planning acceptance tests (PAR-IMP-008)."""

import pytest

from src.clinic.dropfolder_rules import (
    ALLOWED_EXTENSIONS,
    DropFolderRulesError,
    Entry,
    ScanPlan,
    StabilityTracker,
    author_for,
)


def _entry(
    name: str,
    size: int = 1000,
    mtime_ns: int = 1,
    is_dir: bool = False,
    is_symlink: bool = False,
) -> Entry:
    return Entry(name, size, mtime_ns, is_dir, is_symlink)


class TestIgnoredReasons:
    """AC1: every ignored reason is reported with the exact required string."""

    @pytest.mark.parametrize(
        ("entry", "reason"),
        [
            (_entry("dictations", is_dir=True), "directory"),
            (_entry("latest.wav", is_symlink=True), "symlink"),
            (_entry("~$draft.wav"), "temporary"),
            (_entry(".hidden.wav"), "temporary"),
            (_entry("download.tmp"), "temporary"),
            (_entry("upload.part"), "temporary"),
            (_entry("stream.partial"), "temporary"),
            (_entry("video.crdownload"), "temporary"),
            (_entry("notes.txt"), "unsupported_extension"),
            (_entry("empty.wav", size=0), "empty"),
            (_entry("bad/name.wav"), "unsafe_name"),
            (_entry("bad\\name.wav"), "unsafe_name"),
            (_entry("bad\x00name.wav"), "unsafe_name"),
            (_entry("bad\x01name.wav"), "unsafe_name"),
            (_entry("a" * 256), "unsafe_name"),
        ],
    )
    def test_ignored_reason(self, entry: Entry, reason: str) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan([entry])
        assert plan.ready == ()
        assert plan.waiting == ()
        assert plan.ignored == ((entry.name, reason),)

    def test_temporary_prefix_takes_precedence_over_extension(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan([_entry(".secret.wav")])
        assert plan.ignored == ((".secret.wav", "temporary"),)

    def test_empty_takes_precedence_over_extension(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan([_entry("empty.dss", size=0)])
        assert plan.ignored == (("empty.dss", "empty"),)

    def test_unsafe_name_takes_precedence_over_other_reasons(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan([_entry(".bad\x00name.wav")])
        assert plan.ignored == ((".bad\x00name.wav", "unsafe_name"),)


class TestStability:
    """AC2/AC3/AC4: stability tracking across scans."""

    def test_new_file_is_waiting_on_first_scan(self) -> None:
        tracker = StabilityTracker(required_scans=2)
        plan = tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        assert plan.ready == ()
        assert plan.waiting == ("a.wav",)
        assert plan.ignored == ()

    def test_file_becomes_ready_after_required_scans(self) -> None:
        tracker = StabilityTracker(required_scans=2)
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        plan = tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        assert plan.ready == ("a.wav",)
        assert plan.waiting == ()

    def test_file_needs_consecutive_unchanged_scans(self) -> None:
        tracker = StabilityTracker(required_scans=3)
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        plan = tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        assert plan.ready == ("a.wav",)

    def test_size_change_resets_count(self) -> None:
        tracker = StabilityTracker(required_scans=2)
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        plan = tracker.plan([_entry("a.wav", size=200, mtime_ns=1)])
        assert plan.ready == ()
        assert plan.waiting == ("a.wav",)

    def test_mtime_change_resets_count(self) -> None:
        tracker = StabilityTracker(required_scans=2)
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        plan = tracker.plan([_entry("a.wav", size=100, mtime_ns=2)])
        assert plan.ready == ()
        assert plan.waiting == ("a.wav",)

    def test_disappeared_file_is_forgotten(self) -> None:
        tracker = StabilityTracker(required_scans=2)
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        plan = tracker.plan([_entry("b.wav", size=200, mtime_ns=3)])
        assert plan.ready == ()
        assert plan.waiting == ("b.wav",)
        # If a.wav reappears, it starts from scratch; b.wav is now stable.
        plan2 = tracker.plan(
            [
                _entry("a.wav", size=100, mtime_ns=1),
                _entry("b.wav", size=200, mtime_ns=3),
            ]
        )
        assert plan2.ready == ("b.wav",)
        assert plan2.waiting == ("a.wav",)

    def test_ready_and_waiting_sorted_by_name(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan(
            [
                _entry("zebra.wav"),
                _entry("alpha.wav"),
                _entry("mike.wav"),
            ]
        )
        assert plan.ready == ("alpha.wav", "mike.wav", "zebra.wav")
        assert plan.waiting == ()

    def test_waiting_sorted_by_name(self) -> None:
        tracker = StabilityTracker(required_scans=2)
        plan = tracker.plan(
            [
                _entry("zebra.wav"),
                _entry("alpha.wav"),
            ]
        )
        assert plan.ready == ()
        assert plan.waiting == ("alpha.wav", "zebra.wav")

    def test_ignored_sorted_by_name(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan(
            [
                _entry("zebra.txt"),
                _entry("alpha.txt"),
            ]
        )
        assert plan.ignored == (
            ("alpha.txt", "unsupported_extension"),
            ("zebra.txt", "unsupported_extension"),
        )

    def test_ignored_entries_hold_no_state(self) -> None:
        tracker = StabilityTracker(required_scans=2)
        tracker.plan([_entry("bad/name.wav")])
        tracker.plan([_entry("bad/name.wav")])
        plan = tracker.plan([_entry("good.wav")])
        assert plan.waiting == ("good.wav",)

    def test_required_scans_one_makes_stable_file_ready_immediately(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan([_entry("a.wav", size=100, mtime_ns=1)])
        assert plan.ready == ("a.wav",)
        assert plan.waiting == ()

    @pytest.mark.parametrize("value", [0, -1, -5])
    def test_required_scans_below_one_raises(self, value: int) -> None:
        with pytest.raises(ValueError, match="required_scans"):
            StabilityTracker(required_scans=value)

    def test_required_scans_bool_rejected(self) -> None:
        with pytest.raises(DropFolderRulesError) as exc:
            StabilityTracker(required_scans=True)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_required_scans_type"


class TestAuthorFor:
    """AC5: author prefix resolution."""

    def test_known_author_resolved(self) -> None:
        assert author_for("Meier_visit1.wav", {"Meier": 7}) == 7

    def test_unknown_author_returns_none(self) -> None:
        assert author_for("Mueller_visit1.wav", {"Meier": 7}) is None

    def test_name_without_underscore_returns_none(self) -> None:
        assert author_for("Meier.wav", {"Meier": 7}) is None

    def test_empty_prefix_returns_none(self) -> None:
        assert author_for("_visit1.wav", {"Meier": 7}) is None

    def test_prefix_over_64_chars_returns_none(self) -> None:
        long_prefix = "a" * 65
        assert author_for(f"{long_prefix}_visit1.wav", {long_prefix: 7}) is None

    def test_match_is_case_sensitive(self) -> None:
        assert author_for("meier_visit1.wav", {"Meier": 7}) is None

    def test_nfc_normalization(self) -> None:
        # e + combining acute vs precomposed e-acute
        decomposed = "Ame\u0301lie_visit1.wav"
        precomposed_author = "Am\u00e9lie"
        assert author_for(decomposed, {precomposed_author: 42}) == 42

    def test_path_basename_used(self) -> None:
        assert author_for("/some/path/Meier_visit1.wav", {"Meier": 7}) == 7

    def test_author_id_bool_rejected(self) -> None:
        with pytest.raises(DropFolderRulesError) as exc:
            author_for("Meier_visit1.wav", {"Meier": True})  # type: ignore[dict-item]
        assert exc.value.code == "invalid_author_id_type"

    def test_author_id_non_int_rejected(self) -> None:
        with pytest.raises(DropFolderRulesError) as exc:
            author_for("Meier_visit1.wav", {"Meier": "seven"})  # type: ignore[dict-item]
        assert exc.value.code == "invalid_author_id_type"


class TestValidation:
    """Input validation and edge cases."""

    def test_duplicate_name_raises(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        with pytest.raises(ValueError, match="duplicate_name"):
            tracker.plan(
                [
                    _entry("a.wav"),
                    _entry("a.wav"),
                ]
            )

    def test_name_longer_than_255_is_unsafe_name(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        name = "x" * 256
        plan = tracker.plan([_entry(name)])
        assert plan.ignored == ((name, "unsafe_name"),)

    def test_size_bool_rejected(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        with pytest.raises(DropFolderRulesError) as exc:
            tracker.plan([Entry("a.wav", True, 1)])  # type: ignore[arg-type]
        assert exc.value.code == "invalid_size_type"

    def test_mtime_bool_rejected(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        with pytest.raises(DropFolderRulesError) as exc:
            tracker.plan([Entry("a.wav", 100, True)])  # type: ignore[arg-type]
        assert exc.value.code == "invalid_mtime_type"

    def test_name_bool_rejected(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        with pytest.raises(DropFolderRulesError) as exc:
            tracker.plan([Entry(True, 100, 1)])  # type: ignore[arg-type]
        assert exc.value.code == "invalid_name_type"

    def test_empty_name_rejected(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        with pytest.raises(DropFolderRulesError) as exc:
            tracker.plan([_entry("")])
        assert exc.value.code == "invalid_name_empty"

    def test_negative_size_rejected(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        with pytest.raises(DropFolderRulesError) as exc:
            tracker.plan([_entry("a.wav", size=-1)])
        assert exc.value.code == "invalid_size_value"

    def test_non_sequence_listing_rejected(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        with pytest.raises(DropFolderRulesError) as exc:
            tracker.plan("not a listing")  # type: ignore[arg-type]
        assert exc.value.code == "invalid_listing_type"

    def test_author_for_name_not_string(self) -> None:
        with pytest.raises(DropFolderRulesError) as exc:
            author_for(123, {"Meier": 7})  # type: ignore[arg-type]
        assert exc.value.code == "invalid_name_type"

    def test_author_for_known_authors_not_mapping(self) -> None:
        with pytest.raises(DropFolderRulesError) as exc:
            author_for("Meier_visit1.wav", [7])  # type: ignore[arg-type]
        assert exc.value.code == "invalid_known_authors_type"


class TestAllowedExtensions:
    """Extension handling."""

    @pytest.mark.parametrize("ext", ALLOWED_EXTENSIONS)
    def test_allowed_extension_is_accepted(self, ext: str) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan([_entry(f"a{ext}")])
        assert plan.ready == (f"a{ext}",)

    def test_extension_match_is_case_insensitive(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan([_entry("a.WAV"), _entry("b.Mp3")])
        assert plan.ready == ("a.WAV", "b.Mp3")

    def test_no_extension_is_unsupported(self) -> None:
        tracker = StabilityTracker(required_scans=1)
        plan = tracker.plan([_entry("a")])
        assert plan.ignored == (("a", "unsupported_extension"),)


class TestScanPlanImmutability:
    def test_scan_plan_is_frozen(self) -> None:
        plan = ScanPlan((), (), ())
        with pytest.raises(AttributeError):
            plan.ready = ("x",)  # type: ignore[misc]

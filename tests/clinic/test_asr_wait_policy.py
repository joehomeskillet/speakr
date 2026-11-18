"""ASR wait policy for the clinic dictation editor.

Acceptance criteria from PAR-ASR-003:
1. pending/running below offer_manual_after_s -> waiting, can_start_manual=False;
   at or above -> offer_manual, can_start_manual=True (boundary 119/120);
   at or above hard_timeout_s -> timed_out, manual allowed.
2. failed -> asr_failed with can_start_manual=True immediately;
   skipped -> skipped with manual allowed;
   succeeded -> asr_done (manual not needed, can_start_manual=False).
3. manual_started=True always gives manual_active while ASR is pending/running,
   and late_result_policy == 'side_revision'; if ASR then succeeded,
   the view stays manual_active (manual work is never replaced).
4. late_result_policy is the constant string 'side_revision' for every view.
5. Every view has a non-empty German message (no raw state tokens).
6. No coupling to approval: no parameter or field contains 'accept' or 'approv'.
7. Invalid input raises WaitError: unknown status, negative or bool waited_s,
   hard_timeout_s <= offer_manual_after_s, non-bool manual_started.
"""

import dataclasses
import inspect

import pytest

from src.clinic.asr_wait_policy import (
    ASR_STATES,
    WaitConfig,
    WaitError,
    WaitView,
    view_state,
)


class TestWaitConfig:
    def test_defaults(self):
        cfg = WaitConfig()
        assert cfg.offer_manual_after_s == 120
        assert cfg.hard_timeout_s == 900

    def test_custom_values(self):
        cfg = WaitConfig(offer_manual_after_s=60, hard_timeout_s=300)
        assert cfg.offer_manual_after_s == 60
        assert cfg.hard_timeout_s == 300

    def test_hard_timeout_must_exceed_offer_manual(self):
        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=120, hard_timeout_s=120)
        assert exc.value.code == "bad_config"

        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=200, hard_timeout_s=100)
        assert exc.value.code == "bad_config"

    def test_negative_thresholds_rejected(self):
        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=-1, hard_timeout_s=900)
        assert exc.value.code == "bad_config"

        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=120, hard_timeout_s=-1)
        assert exc.value.code == "bad_config"

    def test_bool_thresholds_rejected(self):
        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=True, hard_timeout_s=900)
        assert exc.value.code == "bad_config"

        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=120, hard_timeout_s=False)
        assert exc.value.code == "bad_config"

    def test_non_int_thresholds_rejected(self):
        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=120.5, hard_timeout_s=900)
        assert exc.value.code == "bad_config"

        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=120, hard_timeout_s="900")
        assert exc.value.code == "bad_config"


class TestViewStateBasics:
    def test_asr_states_constant(self):
        assert ASR_STATES == ("pending", "running", "succeeded", "failed", "skipped")

    def test_wait_view_is_frozen_dataclass(self):
        view = WaitView("waiting", False, "side_revision", "Test")
        assert dataclasses.is_dataclass(view)
        assert view.__dataclass_params__.frozen is True

    def test_wait_config_is_frozen_dataclass(self):
        cfg = WaitConfig()
        assert dataclasses.is_dataclass(cfg)
        assert cfg.__dataclass_params__.frozen is True

    def test_wait_error_has_code(self):
        err = WaitError("msg", code="test_code")
        assert err.code == "test_code"
        assert str(err) == "msg"


class TestPendingAndRunning:
    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_below_offer_manual_is_waiting(self, status):
        view = view_state(status, 119, manual_started=False)
        assert view.state == "waiting"
        assert view.can_start_manual is False
        assert view.late_result_policy == "side_revision"
        assert view.message == "Spracherkennung läuft …"

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_at_offer_manual_boundary_is_offer_manual(self, status):
        view = view_state(status, 120, manual_started=False)
        assert view.state == "offer_manual"
        assert view.can_start_manual is True
        assert view.late_result_policy == "side_revision"
        assert "länger" in view.message
        assert "Manuell" in view.message

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_between_offer_and_hard_timeout_is_offer_manual(self, status):
        view = view_state(status, 500, manual_started=False)
        assert view.state == "offer_manual"
        assert view.can_start_manual is True

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_at_hard_timeout_boundary_is_timed_out(self, status):
        view = view_state(status, 900, manual_started=False)
        assert view.state == "timed_out"
        assert view.can_start_manual is True
        assert view.late_result_policy == "side_revision"
        assert "sehr lange" in view.message

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_above_hard_timeout_is_timed_out(self, status):
        view = view_state(status, 1000, manual_started=False)
        assert view.state == "timed_out"
        assert view.can_start_manual is True


class TestFailedStatus:
    def test_failed_immediate_asr_failed(self):
        view = view_state("failed", 0, manual_started=False)
        assert view.state == "asr_failed"
        assert view.can_start_manual is True
        assert view.late_result_policy == "side_revision"
        assert "fehlgeschlagen" in view.message

    def test_failed_after_some_wait(self):
        view = view_state("failed", 500, manual_started=False)
        assert view.state == "asr_failed"
        assert view.can_start_manual is True

    def test_failed_manual_already_started(self):
        view = view_state("failed", 100, manual_started=True)
        assert view.state == "asr_failed"
        assert view.can_start_manual is True


class TestSkippedStatus:
    def test_skipped_immediate(self):
        view = view_state("skipped", 0, manual_started=False)
        assert view.state == "skipped"
        assert view.can_start_manual is True
        assert view.late_result_policy == "side_revision"
        assert "übersprungen" in view.message


class TestSucceededStatus:
    def test_succeeded_without_manual_is_asr_done(self):
        view = view_state("succeeded", 0, manual_started=False)
        assert view.state == "asr_done"
        assert view.can_start_manual is False
        assert view.late_result_policy == "side_revision"
        assert "abgeschlossen" in view.message

    def test_succeeded_with_manual_stays_manual_active(self):
        view = view_state("succeeded", 500, manual_started=True)
        assert view.state == "manual_active"
        assert view.can_start_manual is False
        assert view.late_result_policy == "side_revision"
        assert "Nebenversion" in view.message


class TestManualStarted:
    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_manual_started_pending_running_is_manual_active(self, status):
        view = view_state(status, 10, manual_started=True)
        assert view.state == "manual_active"
        assert view.can_start_manual is True
        assert view.late_result_policy == "side_revision"
        assert "aktiv" in view.message
        assert "läuft weiter" in view.message

    def test_manual_started_succeeded_is_manual_active(self):
        view = view_state("succeeded", 500, manual_started=True)
        assert view.state == "manual_active"
        assert view.can_start_manual is False
        assert "Nebenversion" in view.message

    def test_manual_started_failed_is_asr_failed(self):
        view = view_state("failed", 100, manual_started=True)
        assert view.state == "asr_failed"
        assert view.can_start_manual is True

    def test_manual_started_skipped_is_skipped(self):
        view = view_state("skipped", 100, manual_started=True)
        assert view.state == "skipped"
        assert view.can_start_manual is True


class TestLateResultPolicyAlwaysSideRevision:
    @pytest.mark.parametrize("status", ASR_STATES)
    @pytest.mark.parametrize("waited", [0, 119, 120, 500, 900, 1000])
    @pytest.mark.parametrize("manual", [False, True])
    def test_late_result_policy_is_side_revision(self, status, waited, manual):
        view = view_state(status, waited, manual_started=manual)
        assert view.late_result_policy == "side_revision", (
            f"expected side_revision for {status}, waited={waited}, manual={manual}"
        )


class TestMessages:
    @pytest.mark.parametrize("status", ASR_STATES)
    @pytest.mark.parametrize("waited", [0, 119, 120, 500, 900, 1000])
    @pytest.mark.parametrize("manual", [False, True])
    def test_message_non_empty_german_no_raw_tokens(self, status, waited, manual):
        view = view_state(status, waited, manual_started=manual)
        msg = view.message
        assert isinstance(msg, str) and msg.strip() != ""
        # No raw state tokens should appear in user-facing message
        forbidden = {"waiting", "offer_manual", "timed_out", "manual_active",
                     "asr_done", "asr_failed", "skipped", "side_revision"}
        for token in forbidden:
            assert token not in msg.lower(), f"message contains raw token {token!r}: {msg!r}"


class TestNoApprovalCoupling:
    def test_view_state_signature_has_no_accept_or_approv(self):
        sig = inspect.signature(view_state)
        for param in sig.parameters.values():
            assert "accept" not in param.name.lower()
            assert "approv" not in param.name.lower()

    def test_wait_config_fields_have_no_accept_or_approv(self):
        for field in dataclasses.fields(WaitConfig):
            assert "accept" not in field.name.lower()
            assert "approv" not in field.name.lower()

    def test_wait_view_fields_have_no_accept_or_approv(self):
        for field in dataclasses.fields(WaitView):
            assert "accept" not in field.name.lower()
            assert "approv" not in field.name.lower()


class TestInputValidation:
    def test_unknown_status_raises(self):
        with pytest.raises(WaitError) as exc:
            view_state("unknown", 0, manual_started=False)
        assert exc.value.code == "unknown_status"

    def test_negative_waited_s_raises(self):
        with pytest.raises(WaitError) as exc:
            view_state("pending", -1, manual_started=False)
        assert exc.value.code == "bad_waited_s"

    def test_bool_waited_s_raises(self):
        with pytest.raises(WaitError) as exc:
            view_state("pending", True, manual_started=False)
        assert exc.value.code == "bad_waited_s"

        with pytest.raises(WaitError) as exc:
            view_state("pending", False, manual_started=False)
        assert exc.value.code == "bad_waited_s"

    def test_float_waited_s_raises(self):
        with pytest.raises(WaitError) as exc:
            view_state("pending", 120.0, manual_started=False)
        assert exc.value.code == "bad_waited_s"

    def test_string_waited_s_raises(self):
        with pytest.raises(WaitError) as exc:
            view_state("pending", "120", manual_started=False)
        assert exc.value.code == "bad_waited_s"

    def test_non_bool_manual_started_raises(self):
        with pytest.raises(WaitError) as exc:
            view_state("pending", 0, manual_started=0)
        assert exc.value.code == "bad_manual_started"

        with pytest.raises(WaitError) as exc:
            view_state("pending", 0, manual_started=1)
        assert exc.value.code == "bad_manual_started"

        with pytest.raises(WaitError) as exc:
            view_state("pending", 0, manual_started="true")
        assert exc.value.code == "bad_manual_started"

    def test_config_validation_in_view_state(self):
        # WaitConfig validates at construction, so the error is raised there
        with pytest.raises(WaitError) as exc:
            WaitConfig(offer_manual_after_s=120, hard_timeout_s=100)
        assert exc.value.code == "bad_config"


class TestCustomConfig:
    def test_custom_thresholds_shift_boundaries(self):
        cfg = WaitConfig(offer_manual_after_s=60, hard_timeout_s=180)
        # Below custom offer threshold
        view = view_state("pending", 59, manual_started=False, config=cfg)
        assert view.state == "waiting"
        assert view.can_start_manual is False

        # At custom offer threshold
        view = view_state("pending", 60, manual_started=False, config=cfg)
        assert view.state == "offer_manual"
        assert view.can_start_manual is True

        # At custom hard timeout
        view = view_state("pending", 180, manual_started=False, config=cfg)
        assert view.state == "timed_out"
        assert view.can_start_manual is True

    def test_custom_config_with_manual_started(self):
        cfg = WaitConfig(offer_manual_after_s=60, hard_timeout_s=180)
        view = view_state("pending", 10, manual_started=True, config=cfg)
        assert view.state == "manual_active"
        assert view.late_result_policy == "side_revision"
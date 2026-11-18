"""T-098 catalog slice: local-first defaults and preserved supplements.

Checks the active clinic profiles, the demo fixture and the startup SSO
default. Grok additions, retained V2 invariants and the original PAR/BT
identifiers must all be present. Original BT given/when/then text is compared
with the DNS catalog and is not rewritten.

Acceptance rows stay not_run. This module does not execute the BT-ADM-001 UI
lab, a Windows SSO session (BT-ADM-010) or a real Entra tenant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.auth.sso import get_sso_config, is_sso_enabled
from src.clinic import config
from src.clinic.entra import (
    EntraConfig,
    EntraConfigError,
    EntraDisabled,
    begin_authorization,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "clinic-spec"

ASSIGNED_REQUIREMENTS = (
    "R-009", "R-046", "R-062", "V4-001", "V4-002", "V4-003",
    "PAR-ADM-001", "PAR-ADM-010",
)
INVARIANTS = tuple(f"INV-{number:02d}" for number in range(1, 17))
GROK_FINDINGS = tuple(f"GRK-C{number:02d}" for number in range(1, 10))
GROK_TESTS = tuple(f"GRK-T-{number:02d}" for number in range(1, 10))
ORACLE_FIELDS = ("given", "when", "then")
SSO_ENV = (
    "ENABLE_SSO", "SSO_CLIENT_ID", "SSO_CLIENT_SECRET",
    "SSO_DISCOVERY_URL", "SSO_REDIRECT_URI", "SSO_PROVIDER_NAME",
)


def _load(relative: str) -> dict:
    with (SPEC / relative).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise TypeError(f"{relative}: top level must be a JSON object")
    return data


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["id"]: row for row in rows}


def test_active_profiles_are_local_first_with_entra_prepared_disabled():
    """Positive case: checked-in profiles and the demo fixture need no provider."""
    defaults = config.dev_defaults()
    identity = defaults["identity"]
    assert defaults == _load("config/dev.defaults.json")
    assert defaults["schema_version"] == 4
    assert defaults["profile"] == "local-web-parity-lab"
    assert identity["provider"] == "local"
    assert identity["mode"] == "local"
    assert identity["local_enabled"] is True
    assert identity["entra_enabled"] is False
    assert identity["keycloak_required"] is False
    assert identity["smtp_required"] is False
    assert identity["email_auto_link"] is False
    assert identity["self_registration"] is False
    assert identity["configuration"] == "config/auth.local.example.json"

    local = _load("config/auth.local.example.json")
    assert local["schema_version"] == 1
    assert local["mode"] == "local"
    assert local["local_enabled"] is True
    assert local["entra_enabled"] is False
    assert local["ldap_enabled"] is False
    assert local["keycloak_required"] is False
    assert local["smtp_required"] is False
    assert local["default_account_policy"] == "local_only"
    assert local["allow_implicit_password_fallback"] is False
    assert local["self_registration"] is False
    assert local["jit_provisioning"] is False
    assert local["email_auto_link"] is False
    assert local["activation"]["real_provider_approved"] is False
    assert EntraConfig.from_dict(local).enabled is False

    # Prepared hybrid file. The raw switch is on; the adapter stays off
    # until a real provider is approved. See the review note.
    hybrid = _load("config/auth.hybrid.example.json")
    assert hybrid["mode"] == "hybrid"
    assert hybrid["entra_enabled"] is True
    assert hybrid["local_enabled"] is True
    assert hybrid["keycloak_required"] is False
    assert hybrid["ldap_enabled"] is False
    assert hybrid["smtp_required"] is False
    assert hybrid["activation"]["real_provider_approved"] is False
    assert EntraConfig.from_dict(hybrid).enabled is False

    production = _load("config/production.template.json")
    assert production["schema_version"] == 4
    assert production["enabled"] is False
    assert production["identity"]["provider"] == "local"
    assert production["identity"]["mode"] == "local"
    assert production["identity"]["local_enabled"] is True
    assert production["identity"]["entra_enabled"] is False

    decision = next(row for row in _load("config/decisions.json")["decisions"] if row["id"] == "D09")
    assert "Local Speakr user management is initial runtime default" in decision["decision"]
    assert "no mandatory Keycloak/LDAP/SMTP" in decision["decision"]

    fixture = _load("tests/fixtures/demo.json")
    assert fixture["synthetic_only"] is True
    assert fixture["auth_profiles"] == {
        "default": "local",
        "entra_enabled": False,
        "separate_oidc_fixture_profile": "hybrid-test-only",
    }
    assert {user["login_policy"] for user in fixture["users"]} == {"local_only"}


def test_startup_sso_does_not_require_a_provider(monkeypatch):
    """Positive startup default: stock SSO stays disabled when no env is set."""
    for name in SSO_ENV:
        monkeypatch.delenv(name, raising=False)
    sso = get_sso_config()
    assert sso["enabled"] is False
    assert is_sso_enabled() is False


def test_unauthorized_and_stale_profiles_cannot_start_entra():
    """Unapproved, empty and old provider-required documents stay disabled.

    A second call is the retry: still EntraDisabled, and no authorization URL
    is built.
    """
    documents = (
        _load("config/auth.local.example.json"),
        _load("config/auth.hybrid.example.json"),
        {},
        {"schema_version": 1, "mode": "oidc", "provider": "keycloak", "required": True},
        {"mode": "hybrid", "entra_enabled": True},
    )
    for document in documents:
        built = EntraConfig.from_dict(document)
        assert built.enabled is False
        for _attempt in range(2):
            with pytest.raises(EntraDisabled) as raised:
                begin_authorization(built)
            assert raised.value.code == "entra_disabled"


def test_approved_incomplete_profile_fails_closed_without_enabling():
    """Failure: approval without a tenant does not create an enabled adapter."""
    incomplete = {
        "mode": "hybrid",
        "entra_enabled": True,
        "activation": {"real_provider_approved": True},
        "entra": {"tenant_id": None},
    }
    with pytest.raises(EntraConfigError) as raised:
        EntraConfig.from_dict(incomplete)
    assert raised.value.code == "tenant_id_invalid"


def test_missing_active_contract_fails_closed(monkeypatch, tmp_path):
    """Failure: clinic defaults do not start from an unknown directory."""
    monkeypatch.setenv("CLINIC_CONFIG_DIR", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        config.dev_defaults()


def test_job_retry_schedule_does_not_require_smtp_or_cloud_asr():
    defaults = config.dev_defaults()
    assert defaults["jobs"]["max_attempts"] == 3
    assert defaults["jobs"]["retry_seconds"] == [5, 30, 120]
    assert defaults["identity"]["smtp_required"] is False
    assert defaults["asr"]["cloud_fallback"] is False
    assert defaults["asr"]["realtime_cloud_fallback"] is False


def test_grok_v2_invariants_and_original_identifiers_are_present():
    contract = (SPEC / "contracts" / "v2-invariants-retained.md").read_text(encoding="utf-8")
    for invariant in INVARIANTS:
        assert f"| {invariant} |" in contract

    findings = _by_id(_load("supplements/grok-review/planning/grok-delta.json")["items"])
    supplemental_tests = _by_id(
        _load("supplements/grok-review/tests/review-additional-tests.json")["tests"]
    )
    assert set(findings) == set(GROK_FINDINGS)
    assert set(supplemental_tests) == set(GROK_TESTS)
    for test_id in GROK_TESTS:
        assert supplemental_tests[test_id]["status"] == "not_run"

    requirements = _by_id(_load("planning/requirements.json")["requirements"])
    for requirement_id in ASSIGNED_REQUIREMENTS:
        assert requirement_id in requirements
    assert requirements["R-062"]["tests"] == ["T-071", "T-098"]
    assert requirements["PAR-ADM-001"]["tests"] == ["BT-ADM-001"]
    assert requirements["PAR-ADM-010"]["tests"] == ["BT-ADM-010"]

    parity = {row["id"] for row in _load("planning/parity-matrix.json")["features"]}
    feature_parity = {
        row["id"] for row in _load("sources/dns-functional/specs/feature_parity.json")["features"]
    }
    par_ids = {requirement_id for requirement_id in requirements if requirement_id.startswith("PAR-")}
    assert par_ids == parity == feature_parity
    assert len(par_ids) == 101

    gaps = _load("planning/web-parity-gaps.json")
    assert len(gaps["gaps"]) == 13
    assert gaps["scope_waivers"] == []
    assert {gap["requirement_id"] for gap in gaps["gaps"]}.isdisjoint(
        {"PAR-ADM-001", "PAR-ADM-010"}
    )

    acceptance = _by_id(_load("tests/acceptance.json")["tests"])
    original = _by_id(_load("tests/dns-behavior-tests.original.json")["test_cases"])
    assert {test_id for test_id in acceptance if test_id.startswith("BT-")} == set(original)
    assert set(GROK_TESTS) <= set(acceptance)
    for test_id in ("T-098", "BT-ADM-001", "BT-ADM-010", *GROK_TESTS):
        assert acceptance[test_id]["status"] == "not_run"
        assert acceptance[test_id]["evidence"] in ([], None)

    for test_id, original_row in original.items():
        current = acceptance[test_id]
        for field in ORACLE_FIELDS:
            assert current[field] == original_row[field], test_id
        assert current["source_when_then_unchanged"] is True
        assert current["original_oracle_not_replaced"] is True
        assert current["source_unchanged_test_id"] == test_id

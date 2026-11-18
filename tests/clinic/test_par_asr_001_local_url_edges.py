"""
Tests for PAR-ASR-001: hostile and odd URL forms for is_local_model_url.

These tests prove URL classification only. They are not evidence of a real local engine.

Findings (behaviour differs from acceptance criteria expectations):
- Trailing dot on hostname (localhost.) is NOT stripped by urlsplit; returns False.
- Decimal integer IP (2130706433), hex (0x7f000001), octal (0177.0.0.1), shorthand (127.1)
  are NOT accepted by ipaddress.ip_address; return False.
- IPv6 mapped IPv4 loopback (::ffff:127.0.0.1) is NOT in any _LOCAL_NETWORKS; returns False.
- Scheme-less 'localhost:9000' becomes //localhost:9000 (scheme ''), hostname 'localhost'
  which is in default allow-list; returns True (acceptance criteria expected False).
- Non-string input (int) raises AttributeError on .strip(); acceptance criteria expected
  False without raising.
- CLINIC_LOCAL_MODEL_HOSTS env var REPLACES defaults, does not add to them.
"""

import os
import pytest

from src.clinic.config import is_local_model_url, local_model_hosts


class TestParAsr001UserInfoTricks:
    """User-info tricks must be rejected (contain @ which is forbidden)."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1@evil.example/v1",
            "http://localhost:80@evil.example/",
            "http://evil.example#@localhost/",
            "http://evil.example/?x=http://localhost/",
        ],
    )
    def test_userinfo_tricks_rejected(self, url):
        assert is_local_model_url(url) is False


class TestParAsr001LookalikeHosts:
    """Look-alike hosts must not be accepted as local."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost.evil.example/",
            "http://127.0.0.1.evil.example/",
            "http://evil-localhost/",
        ],
    )
    def test_lookalike_hosts_rejected(self, url):
        assert is_local_model_url(url) is False


class TestParAsr001CaseAndDots:
    """Case variations and trailing dots: assert real behaviour."""

    def test_uppercase_scheme_and_host_accepted(self):
        # urlsplit lowercases scheme and hostname; 'localhost' is in default allow-list
        assert is_local_model_url("HTTP://LOCALHOST:8000/v1") is True

    def test_trailing_dot_on_hostname_rejected(self):
        # urlsplit does NOT strip trailing dot; 'localhost.' != 'localhost' in allow-list
        assert is_local_model_url("http://localhost./v1") is False


class TestParAsr001NumericForms:
    """Numeric and unusual IP forms: assert real behaviour per form."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            # 2130706433 == 127.0.0.1 as 32-bit integer (ip_address rejects decimal int form)
            ("http://2130706433/", False),
            # 0x7f000001 == 127.0.0.1 in hex (ip_address rejects hex)
            ("http://0x7f000001/", False),
            # 0177.0.0.1 == octal 127.0.0.1 (ip_address rejects octal)
            ("http://0177.0.0.1/", False),
            # 127.1 == shorthand for 127.0.0.1 (ip_address rejects shorthand)
            ("http://127.1/", False),
            # IPv6 mapped IPv4 loopback (not in _LOCAL_NETWORKS)
            ("http://[::ffff:127.0.0.1]:9000/", False),
            # IPv6 loopback (in ::1/128)
            ("http://[::1]:9000/", True),
        ],
    )
    def test_numeric_forms(self, url, expected):
        assert is_local_model_url(url) is expected


class TestParAsr001SchemesAndShapes:
    """Non-http schemes, missing scheme, malformed inputs: assert real behaviour."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("ftp://localhost/", False),           # scheme not http/https
            ("file:///etc/passwd", False),         # scheme not http/https
            ("//localhost/v1", False),             # code prepends // -> ////localhost/v1, no hostname
            ("localhost:9000", True),              # becomes //localhost:9000, scheme '', localhost in allow-list
            ("http://local host/", False),         # space in URL (forbidden char)
            ("x" * 5000, False),                   # 5000 character URL (no scheme, hostname 'x...' not in allow-list)
            ("", False),                           # empty string
            ("   ", False),                        # whitespace
            ("\t\n", False),                       # control chars
        ],
    )
    def test_schemes_and_shapes(self, url, expected):
        assert is_local_model_url(url) is expected

    def test_none_returns_false(self):
        assert is_local_model_url(None) is False

    def test_int_raises_attribute_error(self):
        # Code does not guard against non-str; raises AttributeError on .strip()
        # Acceptance criteria expected False without raising; this is a finding.
        with pytest.raises(AttributeError):
            is_local_model_url(123)


class TestParAsr001EnvironmentOverride:
    """CLINIC_LOCAL_MODEL_HOSTS env var REPLACES defaults; does not add to them."""

    def test_custom_host_becomes_local_defaults_replaced(self, monkeypatch):
        monkeypatch.setenv("CLINIC_LOCAL_MODEL_HOSTS", "my-asr,localhost")
        # custom host
        assert is_local_model_url("http://my-asr:8000/v1") is True
        # explicit localhost in env var works
        assert is_local_model_url("http://localhost:8000/v1") is True
        # default hosts NOT in env var are NO LONGER local
        assert is_local_model_url("http://speaches:8000/v1") is False
        assert is_local_model_url("http://host.docker.internal:8000/v1") is False

    def test_unset_restores_default(self, monkeypatch):
        monkeypatch.setenv("CLINIC_LOCAL_MODEL_HOSTS", "custom-only")
        assert is_local_model_url("http://custom-only:8000/") is True
        assert is_local_model_url("http://localhost:8000/") is False

        monkeypatch.delenv("CLINIC_LOCAL_MODEL_HOSTS", raising=False)
        assert is_local_model_url("http://localhost:8000/") is True
        assert is_local_model_url("http://speaches:8000/") is True
        assert is_local_model_url("http://host.docker.internal:8000/") is True
        assert is_local_model_url("http://custom-only:8000/") is False

    def test_env_var_case_insensitive_and_stripped(self, monkeypatch):
        monkeypatch.setenv("CLINIC_LOCAL_MODEL_HOSTS", "  My-AsR ,  LOCALHOST  ")
        assert is_local_model_url("http://my-asr:8000/") is True
        assert is_local_model_url("http://localhost:8000/") is True

    def test_empty_env_var_means_no_hosts(self, monkeypatch):
        monkeypatch.setenv("CLINIC_LOCAL_MODEL_HOSTS", "")
        # empty string -> no hosts in allow-list, but loopback IPs still work
        assert is_local_model_url("http://127.0.0.1:8000/") is True
        assert is_local_model_url("http://localhost:8000/") is False

    def test_env_var_comma_only_means_no_hosts(self, monkeypatch):
        monkeypatch.setenv("CLINIC_LOCAL_MODEL_HOSTS", ",,")
        assert is_local_model_url("http://127.0.0.1:8000/") is True
        assert is_local_model_url("http://localhost:8000/") is False
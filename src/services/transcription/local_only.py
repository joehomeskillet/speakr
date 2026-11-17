"""
Local/private base URL validation for clinic mode.

Ensures that automatic transcription in clinic mode only connects to a
local or private ASR service (loopback, RFC1918/ULA, or a single-label
hostname such as a container/compose service name) and fails closed otherwise.
"""

import ipaddress
import os
import urllib.parse
from typing import Optional

from .exceptions import ConfigurationError


def is_local_base_url(url: Optional[str]) -> bool:
    """
    Decide whether a base URL is local/private.

    Accepts:
    - Loopback addresses (localhost, 127.0.0.0/8, ::1)
    - RFC 1918 IPv4 private addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - IPv4 link-local addresses (169.254.0.0/16)
    - IPv6 Unique Local Addresses (fc00::/7) and link-local (fe80::/10)
    - Single-label hostnames without dots (e.g. docker-compose service names like 'speaches')
    - Hostnames ending in .local, .internal, .lan, or .localhost

    Rejects:
    - Public domains (e.g. api.openai.com, api.mistral.ai)
    - Public IP addresses
    - Empty or missing URLs
    """
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False

    if "://" not in url:
        parsed = urllib.parse.urlparse(f"http://{url}")
    else:
        parsed = urllib.parse.urlparse(url)

    hostname = parsed.hostname
    if not hostname:
        return False

    hostname = hostname.strip().lower()
    if not hostname:
        return False

    if hostname == "localhost":
        return True

    try:
        ip = ipaddress.ip_address(hostname)
        return bool(ip.is_loopback or ip.is_private or ip.is_link_local)
    except ValueError:
        pass

    if "." not in hostname:
        return True

    if (
        hostname.endswith(".local")
        or hostname.endswith(".internal")
        or hostname.endswith(".lan")
        or hostname.endswith(".localhost")
    ):
        return True

    return False


def is_clinic_mode() -> bool:
    """Return True if CLINIC_MODE environment variable is enabled."""
    val = os.environ.get("CLINIC_MODE", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def validate_clinic_base_url(base_url: Optional[str]) -> None:
    """
    Validate that base_url is local/private when CLINIC_MODE is on.

    Raises:
        ConfigurationError: When CLINIC_MODE is on and base_url is not local.
    """
    if not is_clinic_mode():
        return

    if not base_url or not is_local_base_url(base_url):
        raise ConfigurationError(
            f"CLINIC_MODE is active: transcription base URL must point to a local or private service "
            f"(loopback, RFC1918, ULA, or single-label container name). Received: {base_url!r}"
        )

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.errors import APIError

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data.ec2.internal",
}
BLOCKED_SUFFIXES = {
    ".localhost",
    ".local",
    ".internal",
    ".home",
    ".lan",
    ".home.arpa",
}


@dataclass(frozen=True)
class ValidatedURL:
    original: str
    parsed: SplitResult
    scheme: str
    hostname: str
    port: int
    explicit_port: int | None

    @property
    def url(self) -> str:
        return self.original

    @property
    def robot_product(self) -> str:
        return "SearchRelayBot"


class URLValidationError(ValueError):
    pass


class SecurityPolicy:
    def __init__(
        self,
        *,
        blocked_domains: set[str] | None = None,
        allowed_domains: set[str] | None = None,
    ) -> None:
        self.blocked_domains = _normalized_domains(blocked_domains)
        self.allowed_domains = _normalized_domains(allowed_domains)

    def check_host(self, hostname: str) -> None:
        host = hostname.casefold().rstrip(".")
        if host in self.blocked_domains or _host_matches(host, self.blocked_domains):
            raise APIError(403, "Fetch blocked by security policy", "security_policy_blocked")
        if self.allowed_domains and not _host_matches(host, self.allowed_domains):
            raise APIError(403, "Fetch blocked by security policy", "security_policy_blocked")


def validate_url(raw_url: str) -> ValidatedURL:
    if not raw_url or len(raw_url) > 8192:
        raise URLValidationError("url must contain between 1 and 8192 characters")
    if any(ord(char) < 32 for char in raw_url):
        raise URLValidationError("url contains control characters")
    if "\\" in raw_url:
        raise URLValidationError("url must not contain backslashes")

    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise URLValidationError("url contains an invalid port") from exc

    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise URLValidationError("url must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise URLValidationError("url must not contain user information")
    if not parsed.hostname:
        raise URLValidationError("url must contain a host")

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise URLValidationError("url contains an invalid host") from exc

    if any(char.isspace() for char in hostname):
        raise URLValidationError("url contains an invalid host")
    if not hostname or len(hostname) > 253:
        raise URLValidationError("url contains an invalid host")
    if hostname in BLOCKED_HOSTNAMES or any(
        hostname.endswith(suffix) for suffix in BLOCKED_SUFFIXES
    ):
        raise APIError(403, "Fetch blocked by security policy", "security_policy_blocked")
    if "." not in hostname and not _is_ip_literal(hostname):
        raise URLValidationError("url host must be fully qualified")

    explicit_port = port
    effective_port = port or (443 if scheme == "https" else 80)
    if effective_port not in {80, 443}:
        raise URLValidationError("url port is not allowed")

    if _is_ip_literal(hostname):
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise URLValidationError("url contains an invalid IP address") from exc
        if not is_public_ip(address):
            raise APIError(403, "Fetch blocked by security policy", "security_policy_blocked")

    return ValidatedURL(
        original=raw_url,
        parsed=parsed,
        scheme=scheme,
        hostname=hostname,
        port=effective_port,
        explicit_port=explicit_port,
    )


async def resolve_public_addresses(hostname: str, timeout: float = 5.0) -> list[str]:
    if _is_ip_literal(hostname):
        address = ipaddress.ip_address(hostname)
        if not is_public_ip(address):
            raise APIError(403, "Fetch blocked by security policy", "security_policy_blocked")
        return [hostname]

    normalized = hostname.casefold().rstrip(".")
    if normalized in BLOCKED_HOSTNAMES or any(
        normalized.endswith(suffix) for suffix in BLOCKED_SUFFIXES
    ):
        raise APIError(403, "Fetch blocked by security policy", "security_policy_blocked")

    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                normalized,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ),
            timeout=timeout,
        )
    except (TimeoutError, socket.gaierror, OSError) as exc:
        raise APIError(404, "URL could not be resolved", "no_usable_content") from exc

    addresses = sorted({str(record[4][0]) for record in records})
    if not addresses:
        raise APIError(404, "URL could not be resolved", "no_usable_content")
    for address_text in addresses:
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise APIError(403, "Fetch blocked by security policy", "security_policy_blocked") from exc
        if not is_public_ip(address):
            raise APIError(403, "Fetch blocked by security policy", "security_policy_blocked")
    return addresses


def is_public_ip(address: ipaddress._BaseAddress) -> bool:
    if not isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return bool(address.is_global and not address.is_multicast and not address.is_unspecified)


def pin_hostname(target: ValidatedURL, address: str) -> str:
    """Return ``target`` rewritten to connect to the validated address."""

    parsed = urlsplit(target.original)
    literal = f"[{address}]" if ipaddress.ip_address(address).version == 6 else address
    netloc = literal
    if target.explicit_port is not None:
        netloc = f"{netloc}:{target.explicit_port}"
    return urlunsplit((target.scheme, netloc, parsed.path or "/", parsed.query, ""))


def host_header(target: ValidatedURL) -> str:
    """Return the ``Host`` header value for ``target``."""

    hostname = target.hostname
    if ":" in hostname:
        hostname = f"[{hostname}]"
    if target.explicit_port is not None:
        return f"{hostname}:{target.explicit_port}"
    return hostname


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _normalized_domains(domains: set[str] | None) -> set[str]:
    if not domains:
        return set()
    return {domain.casefold().rstrip(".") for domain in domains if domain}


def _host_matches(hostname: str, domains: set[str]) -> bool:
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)

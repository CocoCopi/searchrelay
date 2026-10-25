"""Authority tiers from a configurable YAML file.

The tier of a result domain is a transparent heuristic, not an audited trust
score. The list is operator-controlled: copy ``config/authority.yaml``, edit
it, and point ``SR_AUTHORITY_CONFIG_FILE`` at the copy.

Schema::

    version: 1
    tiers:
      4: [who.int, un.org]
      3: [nature.com]
      2: [example.org]
    tld_tiers:
      gov: 4
      edu: 3
      org: 2
      int: 2

Domains match the host exactly or as a parent domain (``news.nature.com``
matches ``nature.com``). ``tld_tiers`` assigns a tier to a last label such as
``gov`` or ``edu``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_TIERS: dict[int, tuple[str, ...]] = {
    4: (
        "who.int",
        "un.org",
        "europa.eu",
        "iso.org",
        "ietf.org",
        "nasa.gov",
        "nih.gov",
        "cdc.gov",
        "worldbank.org",
        "gov.uk",
    ),
    3: (
        "nature.com",
        "science.org",
        "acm.org",
        "ieee.org",
        "arxiv.org",
        "pubmed.ncbi.nlm.nih.gov",
        "reuters.com",
        "apnews.com",
        "bbc.co.uk",
        "nytimes.com",
        "wsj.com",
        "economist.com",
        "ft.com",
        "bloomberg.com",
        "stanford.edu",
        "mit.edu",
        "harvard.edu",
        "ox.ac.uk",
        "cam.ac.uk",
    ),
}
DEFAULT_TLD_TIERS: dict[str, int] = {
    "gov": 4,
    "edu": 3,
    "mil": 3,
    "org": 2,
    "int": 2,
}


@dataclass(frozen=True)
class AuthorityConfig:
    tiers: dict[int, tuple[str, ...]] = field(default_factory=dict)
    tld_tiers: dict[str, int] = field(default_factory=dict)

    def tier_for(self, host: str) -> int | None:
        """Return the configured tier for ``host`` or ``None`` when unknown."""

        if not host:
            return None
        normalized = host.casefold().rstrip(".")
        for tier, domains in sorted(self.tiers.items(), reverse=True):
            for domain in domains:
                if normalized == domain or normalized.endswith(f".{domain}"):
                    return tier
        labels = normalized.split(".")
        if len(labels) >= 2:
            return self.tld_tiers.get(labels[-1])
        return None


def load_authority_config(path: Path | None) -> AuthorityConfig:
    """Load the authority configuration, falling back to the built-in lists."""

    if path is None:
        return AuthorityConfig(dict(DEFAULT_TIERS), dict(DEFAULT_TLD_TIERS))
    expanded = Path(path).expanduser()
    if not expanded.exists():
        return AuthorityConfig(dict(DEFAULT_TIERS), dict(DEFAULT_TLD_TIERS))
    try:
        payload = yaml.safe_load(expanded.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(
            f"SearchRelay authority configuration error: unable to read {expanded}: {exc}"
        ) from exc
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise RuntimeError(
            "SearchRelay authority configuration error: the file must contain a mapping"
        )

    raw_tiers = payload.get("tiers", {})
    if not isinstance(raw_tiers, dict):
        raise RuntimeError(
            "SearchRelay authority configuration error: 'tiers' must be a mapping"
        )
    tiers: dict[int, tuple[str, ...]] = {}
    for tier, domains in raw_tiers.items():
        try:
            tier_value = int(tier)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "SearchRelay authority configuration error: tier keys must be integers"
            ) from exc
        if not 0 <= tier_value <= 4:
            raise RuntimeError(
                "SearchRelay authority configuration error: tiers must be between 0 and 4"
            )
        if isinstance(domains, str):
            domains = [domains]
        if not isinstance(domains, list):
            raise RuntimeError(
                "SearchRelay authority configuration error: tier values must be lists"
            )
        tiers[tier_value] = tuple(
            str(domain).casefold().rstrip(".") for domain in domains if str(domain).strip()
        )

    raw_tld_tiers = payload.get("tld_tiers", {})
    if not isinstance(raw_tld_tiers, dict):
        raise RuntimeError(
            "SearchRelay authority configuration error: 'tld_tiers' must be a mapping"
        )
    tld_tiers: dict[str, int] = {}
    for tld, tier in raw_tld_tiers.items():
        try:
            tld_tiers[str(tld).casefold().lstrip(".")] = int(tier)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "SearchRelay authority configuration error: tld_tiers values must be integers"
            ) from exc

    return AuthorityConfig(tiers=tiers, tld_tiers=tld_tiers)

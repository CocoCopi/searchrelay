from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.authority import AuthorityConfig
from app.rerank import normalize_logit

SPAM_PATTERNS = (
    re.compile(r"\b(?:casino|crypto airdrop|guaranteed profit|buy now)\b", re.I),
    re.compile(r"\b(?:cheap pills|free money|work from home and earn)\b", re.I),
)
GENERIC_COMMERCIAL_TLDS = ("com", "net", "info", "biz", "io", "dev", "co")



@dataclass(frozen=True)
class RankSignals:
    authority: int
    freshness: float
    quality: float
    relevance: float
    click_prior: float
    spam_penalty: float


class Ranker:
    def __init__(
        self,
        authority_config: AuthorityConfig | None = None,
        *,
        cross_encoder_weight: float = 0.0,
    ) -> None:
        self.authority_config = authority_config or AuthorityConfig()
        self.cross_encoder_weight = max(0.0, min(1.0, cross_encoder_weight))

    def authority(self, url: str) -> int:
        """Return the authority tier for ``url``.

        The tier comes from the operator-controlled YAML configuration
        (domain lists first, then TLD tiers) and falls back to shape
        heuristics for unknown domains.
        """

        host = _host(url)
        if not host:
            return -1
        tier = self.authority_config.tier_for(host)
        if tier is not None:
            if tier <= 0:
                return 0
            return tier
        if _looks_like_user_hosting(host) or _spam_domain(host):
            return 0
        if host.endswith(tuple(f".{tld}" for tld in GENERIC_COMMERCIAL_TLDS)):
            return 2
        return -1

    def signals(
        self,
        *,
        url: str,
        title: str,
        snippet: str,
        content: str,
        published_at: datetime | None,
        query_terms: tuple[str, ...],
        engine_score: float | None,
    ) -> RankSignals:
        authority = self.authority(url)
        freshness = _freshness(published_at)
        quality = _quality(url, title, snippet, content)
        relevance = _relevance(title, snippet, content, query_terms, engine_score)
        click_prior = _click_prior(engine_score, title, query_terms)
        spam_penalty = _spam_penalty(url, title, snippet, content)
        return RankSignals(
            authority=authority,
            freshness=freshness,
            quality=quality,
            relevance=relevance,
            click_prior=click_prior,
            spam_penalty=spam_penalty,
        )

    def rerank_score(
        self,
        signals: RankSignals,
        cross_encoder_score: float | None = None,
    ) -> float:
        """Return a normalized 0–3 score.

        Every weighted component is in the inclusive 0–1 range and the weights
        sum to 1.0. The weighted result is therefore in 0–1 before it is
        multiplied by 3. The spam penalty is expressed directly in final-score
        points so it remains meaningful on the public scale.

        When the optional cross-encoder reranker is enabled its logit is
        mapped to 0–1 with a sigmoid and blended into the final score.
        """

        score = self._heuristic_score(signals)
        if cross_encoder_score is None or self.cross_encoder_weight <= 0:
            return score
        cross_encoder_points = 3.0 * normalize_logit(cross_encoder_score)
        blended = (1.0 - self.cross_encoder_weight) * score + (
            self.cross_encoder_weight * cross_encoder_points
        )
        return round(max(0.0, min(3.0, blended)), 3)

    def _heuristic_score(self, signals: RankSignals) -> float:
        authority_component = max(signals.authority, 0) / 4 * 0.35
        weighted_score = (
            authority_component
            + signals.freshness * 0.20
            + signals.quality * 0.25
            + signals.relevance * 0.15
            + signals.click_prior * 0.05
        )
        score = weighted_score * 3.0 - signals.spam_penalty
        return round(max(0.0, min(3.0, score)), 3)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def _host_in(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)

def _freshness(published_at: datetime | None) -> float:
    if published_at is None:
        return 0.15
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - published_at).total_seconds() / 86400)
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.85
    if age_days <= 180:
        return 0.65
    if age_days <= 365:
        return 0.45
    if age_days <= 730:
        return 0.30
    return 0.20


def _quality(url: str, title: str, snippet: str, content: str) -> float:
    host = _host(url)
    labels = host.split(".") if host else []
    registrable = labels[-2] if len(labels) >= 2 else host
    quality = 0.55
    quality += min(len(content) / 8000, 0.25)
    quality += min(len(title) / 120, 0.08)
    quality += min(len(snippet) / 300, 0.07)
    if len(registrable) <= 30 and not re.search(r"\d{3,}", registrable):
        quality += 0.05
    if "-" in registrable:
        quality -= 0.08
    if not title:
        quality -= 0.12
    return max(0.0, min(1.0, quality))


def _relevance(
    title: str,
    snippet: str,
    content: str,
    query_terms: tuple[str, ...],
    engine_score: float | None,
) -> float:
    if not query_terms:
        relevance = 0.45
    else:
        title_text = title.casefold()
        body_text = f"{snippet} {content[:8000]}".casefold()
        hits = sum(1 for term in set(query_terms) if term in title_text or term in body_text)
        relevance = min(1.0, hits / max(2, min(len(set(query_terms)), 5)))
    if engine_score is not None and math.isfinite(engine_score):
        if 0 <= engine_score <= 1:
            relevance = max(relevance, engine_score)
        elif 1 < engine_score <= 100:
            relevance = max(relevance, min(1.0, engine_score / 20))
    return relevance


def _click_prior(engine_score: float | None, title: str, query_terms: tuple[str, ...]) -> float:
    prior = 0.35
    if engine_score is not None and math.isfinite(engine_score):
        prior += 0.25 if engine_score > 0 else 0
    if title and any(term in title.casefold() for term in query_terms):
        prior += 0.2
    return min(1.0, prior)


def _spam_penalty(url: str, title: str, snippet: str, content: str) -> float:
    text = f"{title} {snippet} {content[:3000]}"
    penalty = 0.0
    if any(pattern.search(text) for pattern in SPAM_PATTERNS):
        penalty += 0.35
    if _spam_domain(_host(url)):
        penalty += 0.45
    return penalty


def _looks_like_user_hosting(host: str) -> bool:
    return any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in {
            "blogspot.com",
            "wordpress.com",
            "wixsite.com",
            "weebly.com",
            "tumblr.com",
        }
    )


def _spam_domain(host: str) -> bool:
    if not host:
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return True
    label = labels[-2]
    return bool(re.search(r"\d{3,}", label)) or label.count("-") >= 3

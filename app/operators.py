from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone


class OperatorError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedQuery:
    original: str
    terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()
    excluded_phrases: tuple[str, ...] = ()
    positive_sites: tuple[str, ...] = ()
    negative_sites: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    after: date | None = None
    before: date | None = None

    @property
    def engine_query(self) -> str:
        parts: list[str] = []
        parts.extend(self.terms)
        parts.extend(f'"{phrase}"' for phrase in self.phrases)
        if len(self.positive_sites) == 1:
            parts.append(f"site:{self.positive_sites[0]}")
        elif self.positive_sites:
            sites = " OR ".join(f"site:{site}" for site in self.positive_sites)
            parts.append(f"({sites})")
        parts.extend(f"-site:{site}" for site in self.negative_sites)
        parts.extend(f"-{term}" for term in self.excluded_terms)
        parts.extend(f'-"{phrase}"' for phrase in self.excluded_phrases)
        return " ".join(parts).strip()

    @property
    def search_terms(self) -> tuple[str, ...]:
        values: list[str] = []
        for term in self.terms:
            values.extend(re.findall(r"\w+", term.casefold(), flags=re.UNICODE))
        for phrase in self.phrases:
            values.extend(re.findall(r"\w+", phrase.casefold(), flags=re.UNICODE))
        return tuple(values)


_TOKEN_RE = re.compile(r'-?"(?:\\.|[^"\\])*"|[^\s]+')
_SITE_GROUP_RE = re.compile(r"site:([A-Za-z0-9.-]+)", flags=re.IGNORECASE)
_SITE_SEQUENCE_RE = re.compile(
    r"site:[A-Za-z0-9.-]+(?:\s+OR\s+site:[A-Za-z0-9.-]+)+",
    flags=re.IGNORECASE,
)
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?$")


def parse_query(query: str) -> ParsedQuery:
    terms: list[str] = []
    phrases: list[str] = []
    excluded_terms: list[str] = []
    excluded_phrases: list[str] = []
    positive_sites: list[str] = []
    negative_sites: list[str] = []
    languages: list[str] = []
    after: date | None = None
    before: date | None = None

    working_query, site_sequence = _extract_site_sequences(query)
    positive_sites.extend(site for site in site_sequence if site not in positive_sites)

    for match in _TOKEN_RE.finditer(working_query):
        raw = match.group(0)
        quoted = len(raw) >= 2 and raw.startswith('"') and raw.endswith('"')
        value = raw[1:-1].strip() if quoted else raw
        if not value:
            continue

        negative = not quoted and value.startswith("-")
        operator_value = value[1:] if negative else value

        if quoted and operator_value.casefold().startswith("site:"):
            sites = _parse_quoted_site_group(operator_value)
            if sites:
                target = negative_sites if negative else positive_sites
                target.extend(site for site in sites if site not in target)
                continue
            raise OperatorError(f"Invalid site operator: {value}")

        key, separator, argument = operator_value.partition(":")
        key = key.casefold()

        if separator and key == "site" and argument:
            sites = _parse_site_argument(argument)
            if not sites:
                raise OperatorError(f"Invalid site operator: {value}")
            target = negative_sites if negative else positive_sites
            target.extend(site for site in sites if site not in target)
            continue

        if separator and key == "lang" and argument:
            for language in argument.split(","):
                language = language.strip()
                if not _LANGUAGE_RE.fullmatch(language):
                    raise OperatorError(f"Invalid language operator: {value}")
                if language not in languages:
                    languages.append(language)
            continue

        if separator and key in {"after", "before"} and argument:
            parsed_date = _parse_operator_date(argument, boundary=key)
            if key == "after":
                if after is not None and after != parsed_date:
                    raise OperatorError("Multiple after dates are not supported")
                after = parsed_date
            else:
                if before is not None and before != parsed_date:
                    raise OperatorError("Multiple before dates are not supported")
                before = parsed_date
            continue

        if negative and operator_value.startswith('"') and operator_value.endswith('"'):
            excluded_phrase = operator_value[1:-1].strip()
            if not excluded_phrase:
                raise OperatorError(f"Invalid excluded phrase: {value}")
            excluded_phrases.append(excluded_phrase)
        elif negative:
            excluded_terms.append(operator_value)
        elif quoted:
            phrases.append(value)
        else:
            terms.append(value)

    if after and before and after > before:
        raise OperatorError("after date must not be later than before date")

    return ParsedQuery(
        original=query,
        terms=tuple(terms),
        phrases=tuple(phrases),
        excluded_terms=tuple(excluded_terms),
        excluded_phrases=tuple(excluded_phrases),
        positive_sites=tuple(positive_sites),
        negative_sites=tuple(negative_sites),
        languages=tuple(languages),
        after=after,
        before=before,
    )


def _extract_site_sequences(query: str) -> tuple[str, list[str]]:
    sites: list[str] = []

    def replace(match: re.Match[str]) -> str:
        for part in re.split(r"\s+OR\s+", match.group(0), flags=re.IGNORECASE):
            site_match = _SITE_GROUP_RE.fullmatch(part.strip())
            if site_match:
                site = site_match.group(1).casefold().rstrip(".")
                if site and site not in sites:
                    sites.append(site)
        return " "

    return _SITE_SEQUENCE_RE.sub(replace, query), sites


def _parse_site_argument(argument: str) -> list[str]:
    if " OR " not in argument:
        site = argument.strip().casefold().rstrip(".")
        if not site or any(char.isspace() for char in site):
            return []
        return [site]

    sites: list[str] = []
    for part in re.split(r"\s+OR\s+", argument, flags=re.IGNORECASE):
        match = _SITE_GROUP_RE.fullmatch(part.strip())
        if not match:
            return []
        site = match.group(1).casefold().rstrip(".")
        if not site:
            return []
        sites.append(site)
    return sites


def _parse_quoted_site_group(argument: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"\s+OR\s+", argument, flags=re.IGNORECASE)]
    sites: list[str] = []
    for part in parts:
        match = _SITE_GROUP_RE.fullmatch(part)
        if not match:
            return []
        site = match.group(1).casefold().rstrip(".")
        if not site:
            return []
        sites.append(site)
    return sites


def _parse_operator_date(value: str, *, boundary: str) -> date:
    """Parse an inclusive date bound.

    ``after`` uses the first day of the requested period and ``before`` uses
    its last day. Exact dates therefore remain inclusive on both sides.
    """

    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        year = int(value)
        if year < 1 or year > 9999:
            raise OperatorError(f"Invalid date operator: {value}")
        if boundary == "after":
            return date(year, 1, 1)
        return date(year, 12, 31)
    if re.fullmatch(r"\d{4}-\d{2}", value):
        try:
            parsed = datetime.strptime(value, "%Y-%m").date()
        except ValueError as exc:
            raise OperatorError(f"Invalid date operator: {value}") from exc
        if boundary == "after":
            return parsed
        return parsed.replace(day=calendar.monthrange(parsed.year, parsed.month)[1])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise OperatorError(f"Invalid date operator: {value}") from exc
    raise OperatorError(f"Invalid date operator: {value}")


def date_is_in_range(value: datetime | None, after: date | None, before: date | None) -> bool:
    if after is None and before is None:
        return True
    # A date-filtered result must carry a verifiable publication date. Keeping
    # undated results would make the local filter weaker than the operator.
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    day = value.astimezone(timezone.utc).date()
    if after is not None and day < after:
        return False
    if before is not None and day > before:
        return False
    return True

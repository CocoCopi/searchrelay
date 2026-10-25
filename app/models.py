from __future__ import annotations

import ipaddress
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SearchRequest(APIModel):
    query: str = Field(min_length=1, max_length=128)
    count: Annotated[StrictInt, Field(ge=1, le=50)] = 10
    ori_question: str | None = Field(default=None, min_length=1, max_length=128)
    localization: str = Field(
        default="",
        description="Optional IPv4 or IPv6 address used to localize the search request.",
    )
    need_content: Annotated[StrictBool, Field()] = False

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be all whitespace")
        return value

    @field_validator("ori_question")
    @classmethod
    def question_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("ori_question must not be all whitespace")
        return value

    @field_validator("localization")
    @classmethod
    def localization_is_ip_address(cls, value: str) -> str:
        if not value:
            return value
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("localization must be a valid IPv4 or IPv6 address") from exc
        return value


class SearchResult(APIModel):
    rerank_score: float = Field(ge=0, le=3)
    title: str
    url: str
    snippet: str
    summary: str
    content: str
    site_name: str | None = None
    site_icon: str | None = Field(
        default=None,
        description="The result origin's conventional favicon URL.",
    )
    date: str | None = None
    authority: int = Field(ge=-1, le=4)


class SearchResponse(APIModel):
    results: list[SearchResult]


class FetchRequest(APIModel):
    url: str = Field(min_length=1, max_length=8192)


class FetchResponse(APIModel):
    title: str = ""
    content: str

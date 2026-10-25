from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import AccountResolver
from app.billing import BillingStore
from app.config import Settings
from app.engine import SearXNGBackend
from app.errors import APIError, BodyTooLarge, error_payload, install_error_handlers
from app.fetch import FetchService
from app.models import FetchRequest, FetchResponse, SearchRequest, SearchResponse
from app.pipeline import SearchPipeline
from app.rate_limit import SlidingWindowRateLimiter
from app.security import SecurityPolicy, URLValidationError, validate_url


ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "additionalProperties": False,
            "required": ["message", "type", "param", "code"],
            "properties": {
                "message": {"type": "string"},
                "type": {"type": "string"},
                "param": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "code": {"type": "string"},
            },
        }
    },
}


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": ERROR_SCHEMA}},
    }


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _error_response("Invalid request"),
    401: _error_response("Invalid API key"),
    402: _error_response("Insufficient account balance"),
    403: _error_response("Fetch blocked by security policy"),
    404: _error_response("No usable content"),
    408: _error_response("Response body timeout"),
    413: _error_response("Body too large"),
    429: _error_response("Rate limit exceeded"),
    500: _error_response("Internal error"),
    504: _error_response("Downstream timeout"),
}


def create_app(
    settings: Settings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        billing = BillingStore(
            runtime_settings.database_path,
            runtime_settings.price_micros_per_request,
        )
        await _run_sync(
            billing.initialize,
            runtime_settings.effective_initial_balances(),
        )
        app.state.billing = billing
        app.state.account_resolver = AccountResolver(runtime_settings.effective_api_keys())
        app.state.rate_limiter = SlidingWindowRateLimiter()

        owns_client = http_client is None
        if http_client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=runtime_settings.fetch_connect_timeout_seconds,
                    read=runtime_settings.fetch_timeout_seconds,
                    write=runtime_settings.fetch_timeout_seconds,
                    pool=runtime_settings.fetch_connect_timeout_seconds,
                ),
                follow_redirects=False,
                trust_env=False,
            )
        else:
            client = http_client
        app.state.http_client = client

        policy = SecurityPolicy(
            blocked_domains=runtime_settings.blocked_domains(),
            allowed_domains=runtime_settings.allowed_domains(),
        )
        app.state.security_policy = policy
        fetcher = FetchService(
            client=client,
            policy=policy,
            user_agent=runtime_settings.user_agent,
            max_bytes=runtime_settings.fetch_max_bytes,
            max_redirects=runtime_settings.fetch_max_redirects,
            respect_robots=runtime_settings.respect_robots,
        )
        app.state.fetcher = fetcher
        backend = SearXNGBackend(
            client=client,
            upstream_url=runtime_settings.upstream_url,
            timeout_seconds=runtime_settings.upstream_timeout_seconds,
        )
        app.state.search_pipeline = SearchPipeline(
            backend=backend,
            fetcher=fetcher,
            content_concurrency=runtime_settings.fetch_concurrency,
        )

        try:
            yield
        finally:
            if owns_client:
                await client.aclose()

    app = FastAPI(
        title="Search & Fetch API",
        version="1.0.0",
        description=(
            "Search and web-fetch tools for AI agents and retrieval-augmented generation. "
            "The public contract is compatible with the DeepSeek Search API shape."
        ),
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.http_client = http_client
    install_error_handlers(app)

    @app.middleware("http")
    async def enforce_body_limit(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise APIError(400, "Invalid Content-Length header", "invalid_request") from exc
                if declared_size > runtime_settings.max_request_body_bytes:
                    return JSONResponse(
                        status_code=413,
                        content=error_payload("Request body is too large", "body_too_large"),
                    )

            received = 0
            original_receive = request._receive

            async def limited_receive():
                nonlocal received
                message = await original_receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > runtime_settings.max_request_body_bytes:
                        raise BodyTooLarge()
                return message

            request._receive = limited_receive

        return await call_next(request)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/tools/search",
        response_model=SearchResponse,
        responses=ERROR_RESPONSES,
        summary="Search the web",
    )
    async def search(
        request: SearchRequest,
        http_request: Request,
        account_id: Annotated[str, Depends(require_account)],
    ) -> SearchResponse:
        _require_json_content_type(http_request)
        state = http_request.app.state
        if not state.rate_limiter.check_and_record(
            f"account:{account_id}:search",
            runtime_settings.search_qps,
        ):
            raise APIError(429, "Account search QPS limit exceeded", "qps_exceeded")
        await _ensure_balance(state.billing, account_id)

        results = await state.search_pipeline.search(request)
        if results and not state.billing.charge(account_id, "search"):
            raise APIError(402, "Account balance is insufficient", "insufficient_balance")
        return SearchResponse(results=results)

    @app.post(
        "/v1/tools/fetch",
        response_model=FetchResponse,
        responses=ERROR_RESPONSES,
        summary="Fetch and convert a web page to Markdown",
    )
    async def fetch(
        request: FetchRequest,
        http_request: Request,
        account_id: Annotated[str, Depends(require_account)],
    ) -> FetchResponse:
        _require_json_content_type(http_request)
        state = http_request.app.state
        if not state.rate_limiter.check_and_record(
            f"account:{account_id}:fetch",
            runtime_settings.fetch_qps,
        ):
            raise APIError(429, "Account fetch QPS limit exceeded", "qps_exceeded")

        try:
            target = validate_url(request.url)
        except URLValidationError as exc:
            raise APIError(400, f"Invalid URL: {exc}", "invalid_url") from exc
        if not state.rate_limiter.check_and_record(
            f"domain:{target.hostname}:fetch",
            runtime_settings.fetch_domain_qps,
        ):
            raise APIError(429, "Per-domain fetch rate limit exceeded", "domain_rate_limit_exceeded")
        await _ensure_balance(state.billing, account_id)

        page = await state.fetcher.fetch(request.url)
        if not state.billing.charge(account_id, "fetch"):
            raise APIError(402, "Account balance is insufficient", "insufficient_balance")
        return FetchResponse(title=page.title, content=page.content)

    return app


bearer_scheme = HTTPBearer(auto_error=False)


async def require_account(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ],
) -> str:
    authorization = (
        f"{credentials.scheme} {credentials.credentials}" if credentials is not None else None
    )
    return request.app.state.account_resolver.resolve(authorization)


def _require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if content_type != "application/json":
        raise APIError(400, "Content-Type must be application/json", "invalid_content_type")


async def _ensure_balance(billing: BillingStore, account_id: str) -> None:
    balance = await _run_sync(billing.get_balance, account_id)
    if balance <= 0:
        raise APIError(402, "Account balance is insufficient", "insufficient_balance")


async def _run_sync(function, *args):
    import asyncio

    return await asyncio.to_thread(function, *args)


app = create_app()

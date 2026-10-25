"""Optional JavaScript rendering.

Rendering is **experimental and disabled by default**. It requires the
optional ``render`` extra (``pip install 'searchrelay-api[render]'``) and a
locally installed browser (``playwright install chromium``).

Because a browser follows links and loads sub-resources on its own, the
renderer re-applies the public-network policy to every request it observes:
requests to private, loopback, link-local, or otherwise non-routable hosts
are aborted before they leave the process. The renderer is only used when
the plain HTTP extraction produced very little content.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from app.errors import APIError
from app.security import SecurityPolicy, resolve_public_addresses


class JavaScriptRenderer:
    """Render a page with a headless browser, enforcing the SSRF policy."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        user_agent: str = "SearchRelayBot/1.0",
        policy: SecurityPolicy | None = None,
        minimum_content_chars: int = 200,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.policy = policy
        self.minimum_content_chars = minimum_content_chars

    async def render(self, url: str) -> str:
        """Return the rendered HTML for ``url``.

        Raises an :class:`APIError` with code ``renderer_unavailable`` when
        Playwright is not installed, and ``security_policy_blocked`` when the
        target is not a public address.
        """

        playwright_module = _import_playwright()
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if not host:
            raise APIError(400, "Invalid URL", "invalid_url")
        if self.policy is not None:
            self.policy.check_host(host)
        await resolve_public_addresses(host)

        allowed_hosts: dict[str, bool] = {host: True}
        playwright = await playwright_module.async_playwright().start()
        try:
            browser = await playwright.chromium.launch(
                args=["--disable-dev-shm-usage", "--no-sandbox"]
            )
            context = await browser.new_context(
                user_agent=self.user_agent,
                java_script_enabled=True,
            )

            async def route_handler(route, request) -> None:
                request_host = (urlsplit(request.url).hostname or "").casefold()
                if not request_host:
                    await route.abort()
                    return
                if request_host not in allowed_hosts:
                    try:
                        await resolve_public_addresses(request_host)
                    except APIError:
                        allowed_hosts[request_host] = False
                        await route.abort()
                        return
                    allowed_hosts[request_host] = True
                if not allowed_hosts.get(request_host, False):
                    await route.abort()
                    return
                await route.continue_()

            await context.route("**/*", route_handler)
            page = await context.new_page()
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_seconds * 1000,
                )
                content = await page.content()
            finally:
                await page.close()
                await context.close()
                await browser.close()
        except APIError:
            raise
        except asyncio.TimeoutError as exc:
            raise APIError(
                504,
                "The JavaScript renderer timed out",
                "downstream_timeout",
            ) from exc
        except Exception as exc:
            raise APIError(
                500,
                "The JavaScript renderer failed",
                "renderer_failed",
            ) from exc
        finally:
            await playwright.stop()
        return content


def _import_playwright():
    try:
        from playwright import async_api  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise APIError(
            503,
            "JavaScript rendering is unavailable: install the optional "
            "'render' extra and run 'playwright install chromium'",
            "renderer_unavailable",
        ) from exc
    return async_api

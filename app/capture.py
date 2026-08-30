"""Browser-assisted capture of a LinkedIn session.

This is a **setup-time** tool. It drives a real browser once so we can harvest
the full client context — cookies *and* the request headers a cookie paste can
never give you (`x-li-track`, user agent, locale). The API itself never imports
this module and never launches a browser; see ``docs/auth.md``.

Playwright is an optional dependency: install it with

    uv sync --extra capture && playwright install chromium
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from app.auth import Account, Auth, Client, LiTrack

LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"

# Chrome major version -> the closest curl_cffi impersonation target, so the
# TLS fingerprint we replay with matches the browser that minted the session.
_IMPERSONATE_TARGETS = (150, 146, 145, 142, 136, 133, 131, 124, 120, 119, 116)


class CaptureError(Exception):
    """The capture could not be completed."""


def _impersonate_for(user_agent: str) -> str:
    """Pick the impersonation target closest to (but not above) the real UA."""
    match = re.search(r"Chrome/(\d+)", user_agent or "")
    if not match:
        return "chrome150"
    major = int(match.group(1))
    for candidate in _IMPERSONATE_TARGETS:
        if candidate <= major:
            return f"chrome{candidate}"
    return "chrome150"


def _li_track_from(header: str | None) -> LiTrack:
    """Parse the browser's own x-li-track blob, ignoring unknown fields."""
    if not header:
        return LiTrack()
    try:
        return LiTrack.model_validate(json.loads(header))
    except (json.JSONDecodeError, ValueError):
        return LiTrack()


def _account_from(me: dict) -> Account:
    """Pull identity out of a /voyager/api/me payload."""
    for entity in me.get("included", []):
        if entity.get("publicIdentifier"):
            return Account(
                public_id=entity["publicIdentifier"],
                member_urn=entity.get("objectUrn"),
            )
    return Account()


def capture(headless: bool = False, timeout_s: int = 300) -> Auth:
    """Open a browser, wait for a login, and harvest the session.

    Returns a fully populated :class:`Auth`. Raises :class:`CaptureError` if
    Playwright is not installed or the login never completes.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise CaptureError(
            "Playwright is not installed. Run:\n"
            "  uv sync --extra capture && playwright install chromium\n"
            "Or write auth.json by hand - see docs/auth.md"
        ) from exc

    captured_headers: dict[str, str] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        def on_request(request) -> None:
            # Keep the headers from a real Voyager call; those are the ones the
            # app has to reproduce.
            if "voyager/api" in request.url and not captured_headers:
                captured_headers.update(request.all_headers())

        page.on("request", on_request)

        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        if "/feed" not in page.url:
            print("Log in in the browser window (tick 'Keep me signed in').")
            try:
                page.wait_for_url("**/feed/**", timeout=timeout_s * 1000)
            except PWTimeout as exc:
                browser.close()
                raise CaptureError(
                    f"no login completed within {timeout_s}s"
                ) from exc

        # Browse once so the page's JS fires real Voyager calls, which is how we
        # obtain the request headers.
        page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(4000)

        try:
            me = page.request.get(
                "https://www.linkedin.com/voyager/api/me"
            ).json()
        except Exception:  # noqa: BLE001 - identity is informational only
            me = {}

        user_agent = page.evaluate("() => navigator.userAgent")
        cookies = {
            c["name"]: c["value"]
            for c in context.cookies()
            if "linkedin.com" in c.get("domain", "")
        }
        browser.close()

    if not cookies.get("li_at"):
        raise CaptureError("no li_at cookie found - was the login completed?")

    return Auth(
        captured_at=datetime.now(UTC),
        account=_account_from(me),
        client=Client(
            user_agent=user_agent,
            impersonate=_impersonate_for(user_agent),
            accept_language=captured_headers.get(
                "accept-language", "en-US,en;q=0.9"
            ),
            li_lang=captured_headers.get("x-li-lang", "en_US"),
            li_track=_li_track_from(captured_headers.get("x-li-track")),
        ),
        cookies=cookies,
    )

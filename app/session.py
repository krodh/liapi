"""The LinkedIn Voyager client.

A thin, hardened wrapper over a single curl_cffi session. It knows how to look
enough like the web client to survive a few calls, and how to recognise when
LinkedIn has killed the session. It knows nothing about profile parsing.
"""

from __future__ import annotations

import base64
import json
import random
import time
from urllib.parse import quote

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from app.auth import Auth
from app.config import Settings, get_settings

WWW = "https://www.linkedin.com"
API = f"{WWW}/voyager/api"

# Rest.li "decoration" selecting how much of the profile graph to inline. This
# one pulls positions, educations, skills etc. into `included` in one call.
# If profile responses ever come back sparse, re-capture this from a browser
# session — the version suffix moves when LinkedIn ships.
PROFILE_DECORATION = (
    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-101"
)

# Persisted-query id for the profile "section" components (skills, licenses,
# languages, ...). These hashes rotate when LinkedIn ships; if section fetches
# start failing, re-capture the id from a browser session.
PROFILE_COMPONENTS_QUERY_ID = (
    "voyagerIdentityDashProfileComponents.7af5d6f176f11583b382e37e5639e69e"
)

# Static protocol constants. Everything that varies per captured session -
# user agent, locale, x-li-track - comes from auth.json instead.
_STATIC_HEADERS = {
    "accept": "application/vnd.linkedin.normalized+json+2.1",
    "x-restli-protocol-version": "2.0.0",
    "referer": f"{WWW}/feed/",
    # curl_cffi's impersonation supplies these with *document navigation*
    # values; a Voyager call is an XHR, so they are overridden here.
    "priority": "u=1, i",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


class SessionError(Exception):
    """Base class for session failures."""


class SessionKilled(SessionError):
    """LinkedIn actively invalidated the session (`li_at=delete me` / redirect)."""


def _page_instance() -> str:
    """A fresh x-li-page-instance, in the shape the web client sends."""
    tracking_id = base64.b64encode(random.randbytes(16)).decode()
    return f"urn:li:page:d_flagship3_profile_view_base;{tracking_id}"


class LinkedInSession:
    """An authenticated Voyager session backed by one reused HTTP client."""

    def __init__(self, auth: Auth, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._auth = auth
        # Impersonate the browser the session was actually captured from, so the
        # TLS fingerprint is consistent with the cookies we are replaying.
        self._client = requests.Session(impersonate=auth.client.impersonate)
        self._client.headers["x-li-page-instance"] = _page_instance()
        self._client.headers["csrf-token"] = auth.csrf_token
        if auth.client.user_agent:
            self._client.headers["user-agent"] = auth.client.user_agent
        for name, value in auth.cookies.items():
            self._client.cookies.set(name, value, domain=".linkedin.com")

    @property
    def _api_headers(self) -> dict[str, str]:
        client = self._auth.client
        return {
            **_STATIC_HEADERS,
            "accept-language": client.accept_language,
            "x-li-lang": client.li_lang,
            "x-li-track": client.li_track.header(),
        }

    # -- internals ---------------------------------------------------------

    def _pause(self, burst: bool = False) -> None:
        # A browser fires a page's XHRs close together, then idles. Section
        # calls belong to one "page view", so they burst rather than crawl.
        low, high = (0.4, 1.2) if burst else (
            self._settings.min_delay, self._settings.max_delay)
        time.sleep(random.uniform(low, high))

    def _get(self, url: str, *, paced: bool = True, burst: bool = False,
             api: bool = True):
        """GET a URL. `api=False` leaves curl_cffi's document-navigation
        headers alone, which is what a real page load sends."""
        if paced:
            self._pause(burst)
        try:
            response = self._client.get(
                url,
                timeout=self._settings.timeout,
                allow_redirects=False,
                headers=self._api_headers if api else None,
            )
        except RequestException as exc:
            raise SessionError(f"request to {url} failed: {exc}") from exc

        if "li_at=delete me" in response.headers.get("set-cookie", ""):
            raise SessionKilled("LinkedIn deleted li_at; the session is dead")
        if 300 <= response.status_code < 400:
            raise SessionKilled("redirected to the login wall; session not accepted")
        return response

    # -- public API --------------------------------------------------------

    def warmup(self) -> None:
        """Load a real page before the first API call, like a browser would."""
        self._get(f"{WWW}/feed/", paced=False, api=False)

    def is_alive(self) -> bool:
        """Probe `/me`. Returns False instead of raising on a dead session."""
        try:
            return self._get(f"{API}/me").status_code == 200
        except SessionKilled:
            return False

    def profile(self, public_id: str) -> dict:
        """Fetch the raw dash-profile payload for a public id.

        Warms up, then issues the single request that returns the profile plus
        its positions, educations, skills and so on. Deliberately frugal — one
        call per profile.

        The legacy `/identity/profiles/{id}/profileView` endpoint this replaced
        was retired by LinkedIn and now answers HTTP 410.
        """
        self.warmup()
        response = self._get(
            f"{API}/identity/dash/profiles"
            f"?q=memberIdentity&memberIdentity={public_id}"
            f"&decorationId={PROFILE_DECORATION}"
        )
        if response.status_code != 200:
            raise SessionError(f"profile fetch returned HTTP {response.status_code}")
        return response.json()

    def profile_section(self, profile_urn: str, section: str) -> dict:
        """Fetch one detail section (skills, certifications, languages, ...).

        The main profile call inlines positions and educations but leaves these
        collections empty, so each needs its own GraphQL call — the same ones
        the web app makes when you open a profile's "show all" page.
        """
        variables = f"(profileUrn:{quote(profile_urn, safe='')},sectionType:{section})"
        response = self._get(
            f"{API}/graphql?variables={variables}&queryId={PROFILE_COMPONENTS_QUERY_ID}",
            burst=True,
        )
        if response.status_code != 200:
            raise SessionError(f"{section} fetch returned HTTP {response.status_code}")
        return response.json()

    def close(self) -> None:
        self._client.close()

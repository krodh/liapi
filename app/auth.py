"""The `auth.json` credential file: schema, assessment, load and save.

A LinkedIn session is more than a cookie jar. The session token is bound to the
client that minted it, so replaying it convincingly needs that client's identity
too — its user agent, locale, and the `x-li-track` telemetry blob carrying the
LinkedIn client version and the machine's timezone and screen size.

`auth.json` therefore stores *the whole client context*, not just cookies. See
``docs/auth.md`` for how to obtain each field by hand.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

# --- cookie tiers ---------------------------------------------------------
#
# These are empirical. A jar with only the session pair was killed after ~5
# requests; a complete browser jar served 125+ requests over hours without a
# rejection. See docs/reverse-engineering.md.

# Without these nothing authenticates at all.
REQUIRED = ("li_at", "JSESSIONID")

# The persistent "remember me" token, set only when "Keep me signed in" was
# ticked. Every durable session we measured carried it.
DURABILITY = ("li_rm",)

# Not individually essential, but a jar without them looks unlike a browser.
RECOMMENDED = ("bcookie", "bscookie", "lidc", "__cf_bm")


class LiTrack(BaseModel):
    """The `x-li-track` telemetry blob the web client sends on every call.

    Field names are camelCase because they are serialised verbatim into the
    header. Defaults are a plausible desktop client for hand-written files.
    """

    clientVersion: str = "1.13.46312"
    mpVersion: str = "1.13.46312"
    osName: str = "web"
    timezoneOffset: float = 0.0
    timezone: str = "UTC"
    deviceFormFactor: str = "DESKTOP"
    mpName: str = "voyager-web"
    displayDensity: float = 1.0
    displayWidth: int = 1920
    displayHeight: int = 1080

    def header(self) -> str:
        return self.model_dump_json()


class Client(BaseModel):
    """Identity of the browser the session was captured from."""

    user_agent: str | None = None
    # curl_cffi impersonation target; picked to match `user_agent`.
    impersonate: str = "chrome150"
    accept_language: str = "en-US,en;q=0.9"
    li_lang: str = "en_US"
    li_track: LiTrack = Field(default_factory=LiTrack)


class Account(BaseModel):
    """Which LinkedIn account this session belongs to. Informational."""

    public_id: str | None = None
    member_urn: str | None = None


class Auth(BaseModel):
    """The contents of `auth.json`."""

    version: int = 1
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    account: Account = Field(default_factory=Account)
    client: Client = Field(default_factory=Client)
    cookies: dict[str, str] = Field(default_factory=dict)

    # -- normalisation -----------------------------------------------------

    def model_post_init(self, _context: object) -> None:
        # LinkedIn stores JSESSIONID quoted, and the csrf-token header is that
        # value unquoted. Normalise on the way in so neither end has to guess.
        value = self.cookies.get("JSESSIONID")
        if value:
            self.cookies["JSESSIONID"] = '"' + value.strip('"') + '"'

    @property
    def csrf_token(self) -> str:
        return self.cookies.get("JSESSIONID", "").strip('"')

    @property
    def age_hours(self) -> float:
        captured = self.captured_at
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=UTC)
        return (datetime.now(UTC) - captured).total_seconds() / 3600

    # -- quality -----------------------------------------------------------

    def missing(self, names: tuple[str, ...]) -> list[str]:
        return [n for n in names if not self.cookies.get(n)]

    @property
    def usable(self) -> bool:
        """Has what it needs to authenticate at all."""
        return not self.missing(REQUIRED)

    @property
    def durable(self) -> bool:
        """Likely to survive sustained use rather than a handful of calls."""
        return self.usable and not self.missing(DURABILITY)


# --- persistence ----------------------------------------------------------


class AuthError(Exception):
    """auth.json is absent, unreadable, or malformed."""


def load(path: Path) -> Auth:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise AuthError(f"{path} not found - run `liapi capture`") from exc
    except json.JSONDecodeError as exc:
        raise AuthError(f"{path} is not valid JSON: {exc}") from exc

    # A bare cookie mapping is accepted as a convenience for hand-written files.
    if "cookies" not in raw:
        raw = {"cookies": raw}
    try:
        return Auth.model_validate(raw)
    except ValidationError as exc:
        raise AuthError(f"{path} does not match the expected shape: {exc}") from exc


def save(path: Path, auth: Auth) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Opened 0600 from creation so the credential is never world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(auth.model_dump_json(indent=2))

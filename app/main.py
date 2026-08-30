"""FastAPI application: the public HTTP surface."""

from __future__ import annotations

import re
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query
from fastapi import Path as FPath

from app import auth as auth_file
from app.auth import AuthError
from app.cache import ProfileCache
from app.config import get_settings
from app.models import Certification, Language, Profile, parse_profile, parse_section
from app.session import LinkedInSession, SessionError, SessionKilled

app = FastAPI(
    title="LinkedIn Profile API",
    version="0.1.0",
    summary="Structured profile data from LinkedIn's Voyager endpoints.",
)

# Profiles change rarely, and each fetch costs several paced upstream calls, so
# results are cached on disk — surviving restarts, unlike an in-memory cache.
_settings = get_settings()
_profiles = ProfileCache(_settings.cache_dir, _settings.cache_ttl)

# Accepts a full profile URL or a bare public id (vanity slug). The path is
# declared `:path` so a full URL, slashes and all, can be passed inline.
_SLUG = re.compile(r"(?:linkedin\.com/in/)?([A-Za-z0-9\-_%]+)/?$")


def _public_id(url_or_slug: str) -> str:
    candidate = unquote(url_or_slug).strip().rstrip("/")
    # Drop any query string or fragment a pasted profile URL may carry.
    candidate = candidate.split("?")[0].split("#")[0].rstrip("/")
    match = _SLUG.search(candidate)
    if not match:
        raise HTTPException(422, "Not a valid LinkedIn profile URL or public id.")
    return match.group(1)


@app.get("/health")
def health(check: bool = False) -> dict[str, str]:
    """Report service and session state.

    By default this does not call LinkedIn, so polling it can't burn the small
    per-session call budget. Pass ``?check=true`` to actively probe.
    """
    settings = get_settings()
    try:
        auth = auth_file.load(settings.auth_file)
    except AuthError:
        return {"status": "ok", "session": "absent", "hint": "run `liapi capture`"}
    if not auth.usable:
        return {"status": "ok", "session": "unusable", "hint": "run `liapi capture`"}
    if not check:
        return {
            "status": "ok",
            "session": "configured",
            "account": auth.account.public_id or "unknown",
            "captured_hours_ago": f"{auth.age_hours:.1f}",
        }
    alive = LinkedInSession(auth, settings).is_alive()
    return {"status": "ok", "session": "alive" if alive else "expired"}


@app.get("/profile/{identifier:path}", response_model=Profile)
def profile(
    identifier: str = FPath(
        ...,
        description="LinkedIn profile URL or public id (vanity slug).",
        examples=["williamhgates", "https://www.linkedin.com/in/williamhgates/"],
    ),
    sections: bool = Query(
        True,
        description=(
            "Also fetch skills, certifications and languages. Each is a separate "
            "upstream call, so disabling this makes the request noticeably faster."
        ),
    ),
    refresh: bool = Query(False, description="Bypass the cache for this request."),
) -> Profile:
    """Fetch a profile as structured JSON."""
    settings = get_settings()
    public_id = _public_id(identifier)

    if not refresh:
        hit = _profiles.get(public_id, want_sections=sections)
        if hit is not None:
            return Profile.model_validate(hit)

    try:
        session = LinkedInSession(auth_file.load(settings.auth_file), settings)
    except AuthError as exc:
        raise HTTPException(503, f"No usable LinkedIn session: {exc}") from exc
    try:
        result = parse_profile(session.profile(public_id))
        if sections and result.profile_urn:
            _add_sections(session, result)
    except SessionKilled as exc:
        raise HTTPException(503, f"LinkedIn session invalidated: {exc}") from exc
    except SessionError as exc:
        raise HTTPException(502, f"Upstream error: {exc}") from exc
    finally:
        session.close()

    _profiles.set(public_id, result.model_dump(), sections=sections)
    return result


def _add_sections(session: LinkedInSession, result: Profile) -> None:
    """Fill in the collections the main profile call leaves empty.

    Best-effort: a section that fails or is not visible to us leaves its field
    as an empty list rather than failing the whole request.
    """
    urn = result.profile_urn
    try:
        result.skills = [
            e.title for e in parse_section(session.profile_section(urn, "skills"))
        ]
        result.certifications = [
            # A certification renders its issuing authority as the subtitle.
            Certification(name=e.title, authority=e.subtitle)
            for e in parse_section(session.profile_section(urn, "certifications"))
        ]
        result.languages = [
            # A language renders its proficiency as the caption.
            Language(name=e.title, proficiency=e.caption)
            for e in parse_section(session.profile_section(urn, "languages"))
        ]
    except SessionError:
        # Keep whatever we already parsed; sections are supplementary.
        pass

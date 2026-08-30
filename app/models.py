"""The public response schema and the parser from LinkedIn's dash profile API.

These Pydantic models are the API's output contract: every field is always
present (null when unknown), dates are structured, and nothing leaks LinkedIn's
internal shapes.

The upstream payload is Rest.li's normalized form — ``{data, included: [...]}``
where every entity carries a ``$type`` and an ``entityUrn``, and references
between them are URN strings (often on ``*``-prefixed keys). Parsing is
therefore: index ``included`` by URN, then resolve references.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# --- output models --------------------------------------------------------


class MonthYear(BaseModel):
    month: int | None = None
    year: int | None = None


class Experience(BaseModel):
    title: str | None = None
    company: str | None = None
    company_logo: str | None = None
    employment_type: str | None = None
    location: str | None = None
    description: str | None = None
    start: MonthYear | None = None
    end: MonthYear | None = None
    is_current: bool = False


class Education(BaseModel):
    school: str | None = None
    school_logo: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    grade: str | None = None
    activities: str | None = None
    start: MonthYear | None = None
    end: MonthYear | None = None


class Certification(BaseModel):
    name: str | None = None
    authority: str | None = None
    license_number: str | None = None
    url: str | None = None


class Language(BaseModel):
    name: str | None = None
    proficiency: str | None = None


class Profile(BaseModel):
    public_id: str | None = None
    profile_urn: str | None = None
    member_urn: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    location: str | None = None
    country: str | None = None
    industry: str | None = None
    picture_url: str | None = None
    background_url: str | None = None
    experience: list[Experience] = []
    education: list[Education] = []
    skills: list[str] = []
    certifications: list[Certification] = []
    languages: list[Language] = []


# --- parsing helpers ------------------------------------------------------

# Entity types we pull out of `included`, keyed by the suffix of their $type.
_PROFILE = "identity.profile.Profile"
_POSITION = "identity.profile.Position"
_EDUCATION = "identity.profile.Education"
_SKILL = "identity.profile.Skill"
_CERTIFICATION = "identity.profile.Certification"
_LANGUAGE = "identity.profile.Language"


class _Index:
    """Lookup over the normalized `included` array."""

    def __init__(self, included: list[dict]) -> None:
        self._by_urn = {e["entityUrn"]: e for e in included if e.get("entityUrn")}
        self._all = included

    def by_urn(self, urn: str | None) -> dict:
        return self._by_urn.get(urn or "", {})

    def of_type(self, suffix: str) -> list[dict]:
        return [e for e in self._all if e.get("$type", "").endswith(suffix)]

    def first_of_type(self, suffix: str) -> dict:
        found = self.of_type(suffix)
        return found[0] if found else {}


def _date(value: dict | None) -> MonthYear | None:
    if not value:
        return None
    return MonthYear(month=value.get("month"), year=value.get("year"))


def _vector_image_url(vector: dict | None) -> str | None:
    """Build the largest available URL from a VectorImage."""
    if not vector:
        return None
    root = vector.get("rootUrl") or ""
    artifacts = vector.get("artifacts") or []
    if not artifacts:
        return root or None
    largest = max(artifacts, key=lambda a: a.get("width", 0))
    return root + largest.get("fileIdentifyingUrlPathSegment", "")


def _picture(container: dict | None) -> str | None:
    """Pull a VectorImage out of a profile/background picture wrapper.

    Which reference key is present varies by profile: a cropped/filtered photo
    comes back as `displayImageReference`, an uncropped one as
    `originalImageReference`.
    """
    container = container or {}
    for key in ("displayImageReference", "originalImageReference"):
        url = _vector_image_url((container.get(key) or {}).get("vectorImage"))
        if url:
            return url
    return None


def _company_logo(index: _Index, entity: dict) -> str | None:
    company = index.by_urn(entity.get("*company") or entity.get("companyUrn"))
    return _vector_image_url((company.get("logo") or {}).get("vectorImage"))


# --- section parsers ------------------------------------------------------


def _experience(index: _Index) -> list[Experience]:
    out = []
    for item in index.of_type(_POSITION):
        date_range = item.get("dateRange") or {}
        end = _date(date_range.get("end"))
        employment = index.by_urn(item.get("*employmentType"))
        out.append(
            Experience(
                title=item.get("title"),
                company=item.get("companyName"),
                company_logo=_company_logo(index, item),
                employment_type=employment.get("name"),
                location=item.get("locationName"),
                description=item.get("description"),
                start=_date(date_range.get("start")),
                end=end,
                # An open-ended date range is the only marker of a current role.
                is_current=end is None,
            )
        )
    return out


def _education(index: _Index) -> list[Education]:
    out = []
    for item in index.of_type(_EDUCATION):
        date_range = item.get("dateRange") or {}
        out.append(
            Education(
                school=item.get("schoolName"),
                school_logo=_company_logo(index, item),
                degree=item.get("degreeName"),
                field_of_study=item.get("fieldOfStudy"),
                grade=item.get("grade"),
                activities=item.get("activities"),
                start=_date(date_range.get("start")),
                end=_date(date_range.get("end")),
            )
        )
    return out


def _certifications(index: _Index) -> list[Certification]:
    return [
        Certification(
            name=item.get("name"),
            authority=item.get("authority"),
            license_number=item.get("licenseNumber"),
            url=item.get("url"),
        )
        for item in index.of_type(_CERTIFICATION)
    ]


def _languages(index: _Index) -> list[Language]:
    return [
        Language(name=item.get("name"), proficiency=item.get("proficiency"))
        for item in index.of_type(_LANGUAGE)
    ]


def _location(index: _Index, profile: dict) -> tuple[str | None, str | None]:
    geo = index.by_urn((profile.get("geoLocation") or {}).get("*geo"))
    country = index.by_urn(geo.get("*country") or geo.get("countryUrn"))
    return geo.get("defaultLocalizedName"), country.get("defaultLocalizedName")


def _text_of(node: Any) -> str | None:
    """Unwrap LinkedIn's nested {text: {text: "…"}} shapes."""
    if isinstance(node, dict):
        value = node.get("text")
        return value if isinstance(value, str) else _text_of(value)
    return None


def _entity_components(node: Any, found: list[dict]) -> list[dict]:
    """Collect every entityComponent anywhere in a component tree.

    Sections nest differently depending on how LinkedIn chooses to render them:
    a short list sits directly under `elements`, a long one is wrapped in a
    `pagedListComponent` (itself referenced from `included`), and skills arrive
    inside a `tabComponent`. Walking the whole tree covers all of them without
    hard-coding the layout, which LinkedIn changes freely.
    """
    if isinstance(node, dict):
        entity = node.get("entityComponent")
        if isinstance(entity, dict):
            found.append(entity)
        for value in node.values():
            _entity_components(value, found)
    elif isinstance(node, list):
        for value in node:
            _entity_components(value, found)
    return found


class SectionEntry(BaseModel):
    """One rendered row of a profile section.

    Which field carries the useful detail depends on the section: a
    certification puts its issuer in `subtitle`, a language puts its
    proficiency in `caption`.
    """

    title: str
    subtitle: str | None = None
    caption: str | None = None


def parse_section(raw: dict[str, Any]) -> list[SectionEntry]:
    """Extract the entries of a profile "section" payload.

    Sections come back as rendered UI components rather than plain entities, so
    readable content lives at `entityComponent.titleV2.text.text`. An empty
    section renders an `emptyStateComponent` instead and yields nothing.
    """
    # Referenced sub-components (e.g. pagedListComponent) live in `included`,
    # so both halves of the response have to be walked.
    entities = _entity_components(raw.get("data"), [])
    entities += _entity_components(raw.get("included"), [])

    out: list[SectionEntry] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for entity in entities:
        title = _text_of(entity.get("titleV2"))
        if not title:
            continue
        entry = SectionEntry(
            title=title.strip(),
            subtitle=_text_of(entity.get("subtitle")),
            caption=_text_of(entity.get("caption")),
        )
        # The same entity can appear via both data and included.
        key = (entry.title, entry.subtitle, entry.caption)
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


def parse_profile(raw: dict[str, Any]) -> Profile:
    """Map a raw dash-profile payload onto the Profile contract."""
    index = _Index(raw.get("included") or [])
    profile = index.first_of_type(_PROFILE)

    first, last = profile.get("firstName"), profile.get("lastName")
    full_name = " ".join(part for part in (first, last) if part) or None
    location, country = _location(index, profile)

    return Profile(
        public_id=profile.get("publicIdentifier"),
        profile_urn=profile.get("entityUrn"),
        member_urn=profile.get("objectUrn"),
        first_name=first,
        last_name=last,
        full_name=full_name,
        headline=profile.get("headline"),
        summary=profile.get("summary"),
        location=location,
        country=country,
        industry=index.by_urn(profile.get("industryUrn")).get("name"),
        picture_url=_picture(profile.get("profilePicture")),
        background_url=_picture(profile.get("backgroundPicture")),
        experience=_experience(index),
        education=_education(index),
        skills=[s.get("name") for s in index.of_type(_SKILL) if s.get("name")],
        certifications=_certifications(index),
        languages=_languages(index),
    )

"""Parser tests.

The main fixture is a real (captured) dash-profile payload, so these lock in
behaviour against LinkedIn's actual response shape rather than a guess at it.
"""

import json
from pathlib import Path

import pytest

from app.models import parse_profile, parse_section

FIXTURE = Path(__file__).parent / "fixtures" / "dash_profile.json"


@pytest.fixture(scope="module")
def profile():
    return parse_profile(json.loads(FIXTURE.read_text()))


def test_identity_fields(profile):
    assert profile.public_id == "raj-kishore-4a7521216"
    assert (profile.first_name, profile.last_name) == ("Raj", "Kishore")
    assert profile.full_name == "Raj Kishore"
    assert profile.profile_urn.startswith("urn:li:fsd_profile:")
    assert profile.member_urn.startswith("urn:li:member:")


def test_location_resolves_through_geo_urns(profile):
    assert profile.location == "Moradabad, Uttar Pradesh, India"
    assert profile.country == "India"


def test_picture_prefers_display_photo_and_largest_artifact(profile):
    # The display photo is what LinkedIn actually shows (cropped/filtered);
    # within it we take the widest artifact (389 here, not 100/200).
    assert profile.picture_url.startswith("https://")
    assert "displayphoto" in profile.picture_url
    assert "400_400" in profile.picture_url


def test_experience(profile):
    role = profile.experience[0]
    assert role.title == "Founder"
    assert role.company == "Stealth"
    assert role.employment_type == "Full-time"
    assert role.company_logo.startswith("https://")
    assert role.start.model_dump() == {"month": 4, "year": 2025}
    # An open-ended date range means the role is current.
    assert role.end is None and role.is_current is True


def test_education(profile):
    school = profile.education[0]
    assert school.school == "Sharda University"
    assert school.degree == "Bachelor of Technology - BTech"
    assert school.field_of_study == "Computer Science"
    assert school.start.year == 2020
    assert school.school_logo.startswith("https://")


def test_absent_sections_are_empty_not_missing(profile):
    # This account has none of these; they must still be present as [].
    assert profile.skills == []
    assert profile.certifications == []
    assert profile.languages == []


def test_empty_payload_yields_empty_profile():
    empty = parse_profile({})
    assert empty.first_name is None
    assert empty.experience == [] and empty.skills == []


# --- section (GraphQL components) parsing ---------------------------------

def _section(elements):
    return {"data": {"data": {
        "identityDashProfileComponentsBySectionType": {"elements": elements}}}}


def test_section_extracts_title_subtitle_and_caption():
    raw = _section([
        {"components": {"entityComponent": {
            "titleV2": {"text": {"text": "English"}},
            "subtitle": None,
            "caption": {"text": "Native or bilingual proficiency"}}}},
    ])
    entry = parse_section(raw)[0]
    assert entry.title == "English"
    assert entry.caption == "Native or bilingual proficiency"


def test_section_finds_entries_nested_in_a_paged_list():
    # Longer sections are wrapped in a pagedListComponent that LinkedIn returns
    # via `included`, not inline under `elements`.
    raw = {
        "data": {"data": {"identityDashProfileComponentsBySectionType": {
            "elements": [{"components": {
                "*pagedListComponent": "urn:li:fsd_profilePagedList:x"}}]}}},
        "included": [{
            "$type": "com.linkedin.voyager.dash.identity.profile.tetris.PagedListComponent",
            "entityUrn": "urn:li:fsd_profilePagedList:x",
            "components": {"elements": [
                {"components": {"entityComponent": {
                    "titleV2": {"text": {"text": "CKAD"}},
                    "subtitle": {"text": "The Linux Foundation"}}}},
            ]},
        }],
    }
    entry = parse_section(raw)[0]
    assert (entry.title, entry.subtitle) == ("CKAD", "The Linux Foundation")


def test_section_deduplicates_entries_seen_twice():
    entity = {"components": {"entityComponent": {
        "titleV2": {"text": {"text": "Python"}}}}}
    raw = _section([entity, entity])
    assert len(parse_section(raw)) == 1


def test_section_skips_empty_state_component():
    # What LinkedIn actually returns for a profile with no skills listed.
    raw = _section([{"components": {
        "emptyStateComponent": {"text": "Nothing to see for now"},
        "entityComponent": None}}])
    assert parse_section(raw) == []

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.cache import ProfileCache

PROFILE = {"public_id": "someone", "full_name": "Some One"}


@pytest.fixture
def cache(tmp_path):
    return ProfileCache(tmp_path / "profiles", ttl=3600)


def test_round_trip(cache):
    cache.set("someone", PROFILE, sections=True)
    assert cache.get("someone", want_sections=True) == PROFILE


def test_file_is_named_for_the_profile_and_records_a_timestamp(cache):
    cache.set("someone", PROFILE, sections=True)
    path = cache.path_for("someone")
    assert path.name == "someone.json"
    entry = json.loads(path.read_text())
    assert entry["public_id"] == "someone"
    assert entry["sections"] is True
    datetime.fromisoformat(entry["cached_at"])  # parses


def test_miss_when_absent(cache):
    assert cache.get("nobody", want_sections=False) is None


def test_entry_expires(tmp_path):
    cache = ProfileCache(tmp_path, ttl=3600)
    cache.set("someone", PROFILE, sections=True)
    stale = datetime.now(UTC) - timedelta(hours=2)
    path = cache.path_for("someone")
    entry = json.loads(path.read_text())
    entry["cached_at"] = stale.isoformat()
    path.write_text(json.dumps(entry))
    assert cache.get("someone", want_sections=True) is None


def test_entry_without_sections_cannot_answer_a_request_wanting_them(cache):
    cache.set("someone", PROFILE, sections=False)
    assert cache.get("someone", want_sections=True) is None
    # The reverse is fine: a fuller record still answers a lighter request.
    cache.set("other", PROFILE, sections=True)
    assert cache.get("other", want_sections=False) == PROFILE


def test_corrupt_file_is_a_miss_not_an_error(cache):
    cache.set("someone", PROFILE, sections=True)
    cache.path_for("someone").write_text("{not json")
    assert cache.get("someone", want_sections=True) is None


def test_public_id_cannot_escape_the_cache_directory(cache):
    cache.set("../../etc/passwd", PROFILE, sections=True)
    written = list(cache._dir.glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == cache._dir

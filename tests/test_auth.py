import json
import stat

import pytest

from app import auth as auth_file
from app.auth import DURABILITY, REQUIRED, Auth, AuthError

FULL = {
    "li_at": "abc", "JSESSIONID": "ajax:1", "li_rm": "rm",
    "bcookie": "b", "bscookie": "bs", "lidc": "l", "__cf_bm": "cf",
}


def test_jsessionid_normalised_and_drives_csrf():
    auth = Auth(cookies={"li_at": "a", "JSESSIONID": "ajax:9"})
    assert auth.cookies["JSESSIONID"] == '"ajax:9"'
    assert auth.csrf_token == "ajax:9"


def test_full_jar_is_usable_and_durable():
    assert Auth(cookies=FULL).durable


def test_session_pair_alone_is_usable_but_not_durable():
    # The exact shape that died after ~5 requests in testing.
    auth = Auth(cookies={"li_at": "a", "JSESSIONID": "ajax:1"})
    assert auth.usable and not auth.durable
    assert auth.missing(DURABILITY) == ["li_rm"]


def test_missing_session_pair_is_unusable():
    auth = Auth(cookies={"bcookie": "b"})
    assert not auth.usable
    assert set(auth.missing(REQUIRED)) == {"li_at", "JSESSIONID"}


def test_round_trip_preserves_client_context(settings):
    original = Auth(cookies=FULL)
    original.client.user_agent = "Mozilla/5.0 …"
    original.account.public_id = "someone"
    auth_file.save(settings.auth_file, original)

    loaded = auth_file.load(settings.auth_file)
    assert loaded.cookies == original.cookies
    assert loaded.client.user_agent == "Mozilla/5.0 …"
    assert loaded.account.public_id == "someone"
    assert stat.S_IMODE(settings.auth_file.stat().st_mode) == 0o600


def test_bare_cookie_mapping_is_accepted(settings):
    # Convenience for hand-written files: a plain jar with no wrapper.
    settings.auth_file.write_text(json.dumps(FULL))
    assert auth_file.load(settings.auth_file).cookies["li_at"] == "abc"


def test_unreadable_file_raises_authenticationerror(settings):
    with pytest.raises(AuthError):
        auth_file.load(settings.auth_file)
    settings.auth_file.write_text("{not json")
    with pytest.raises(AuthError):
        auth_file.load(settings.auth_file)

import pytest
from fastapi.testclient import TestClient

from app.main import _public_id, app

client = TestClient(app)


@pytest.mark.parametrize("value,expected", [
    ("https://www.linkedin.com/in/ada-lovelace/", "ada-lovelace"),
    ("linkedin.com/in/ada-lovelace", "ada-lovelace"),
    ("ada-lovelace", "ada-lovelace"),
    ("https://www.linkedin.com/in/ada-lovelace?trk=nav", "ada-lovelace"),
    ("https%3A%2F%2Fwww.linkedin.com%2Fin%2Fada-lovelace", "ada-lovelace"),
])
def test_public_id_extraction(value, expected):
    assert _public_id(value) == expected


def _no_session(monkeypatch, tmp_path):
    from app.config import Settings
    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(_env_file=None, auth_file=tmp_path / "none.json"),
    )


def test_health_without_session(monkeypatch, tmp_path):
    _no_session(monkeypatch, tmp_path)
    body = client.get("/health").json()
    assert body["session"] == "absent"
    assert "capture" in body["hint"]


def test_health_reports_account_when_configured(monkeypatch, tmp_path):
    from app import auth as auth_file
    from app.auth import Auth
    from app.config import Settings

    path = tmp_path / "auth.json"
    auth = Auth(cookies={"li_at": "a", "JSESSIONID": "ajax:1"})
    auth.account.public_id = "someone"
    auth_file.save(path, auth)
    monkeypatch.setattr(
        "app.main.get_settings", lambda: Settings(_env_file=None, auth_file=path)
    )

    body = client.get("/health").json()
    assert body["session"] == "configured"
    assert body["account"] == "someone"


def test_profile_without_session_returns_503(monkeypatch, tmp_path):
    _no_session(monkeypatch, tmp_path)
    assert client.get("/profile/ada").status_code == 503

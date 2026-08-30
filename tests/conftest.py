import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path):
    """Settings isolated from any real .env, with a temp auth file."""
    return Settings(_env_file=None, auth_file=tmp_path / "auth.json")

"""Application settings, loaded from the environment or a local `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Directory holding pyproject.toml — the `app` package's parent.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The credential file: cookies plus the client context they were captured
    # with. Created by `liapi capture`, or written by hand (see docs/auth.md).
    auth_file: Path = Path("auth.json")

    # Bounds (seconds) of the random pause between upstream calls. Human-like
    # pacing; keeps the session from tripping LinkedIn's rate-based kill.
    min_delay: float = 2.0
    max_delay: float = 5.0
    timeout: float = 20.0


    def model_post_init(self, _context: object) -> None:
        # The default lives beside pyproject.toml, so the CLI behaves the same
        # from any directory. An explicit AUTH_FILE is left alone and resolves
        # against the working directory, as a caller would expect.
        if "auth_file" not in self.model_fields_set and not self.auth_file.is_absolute():
            self.auth_file = PROJECT_ROOT / self.auth_file


@lru_cache
def get_settings() -> Settings:
    return Settings()

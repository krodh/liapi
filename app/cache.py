"""On-disk cache for fetched profiles.

One JSON file per profile, at ``cache/profiles/<username>.json``. Fetching a
profile costs several deliberately paced upstream calls, so repeat requests are
served from disk — which also survives a restart, unlike an in-memory cache.

Each file records when it was written and whether the detail sections (skills,
certifications, languages) were included, so a request that needs them is not
satisfied by an entry fetched without them.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Cache filenames come from user input, so they are constrained to characters
# that cannot escape the directory or surprise the filesystem.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class ProfileCache:
    """A directory of cached profiles, keyed by public id."""

    def __init__(self, directory: Path, ttl: float) -> None:
        self._dir = directory
        self._ttl = ttl

    def path_for(self, public_id: str) -> Path:
        return self._dir / f"{_SAFE_NAME.sub('_', public_id)}.json"

    def get(self, public_id: str, *, want_sections: bool) -> dict[str, Any] | None:
        """Return a cached profile, or None if absent, stale or insufficient."""
        path = self.path_for(public_id)
        try:
            entry = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        cached_at = entry.get("cached_at")
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(cached_at)).total_seconds()
        except (TypeError, ValueError):
            return None
        if age > self._ttl:
            return None

        # An entry fetched without sections cannot answer a request that wants
        # them; the reverse is fine, since a fuller record is still correct.
        if want_sections and not entry.get("sections"):
            return None
        return entry.get("profile")

    def set(
        self, public_id: str, profile: dict[str, Any], *, sections: bool
    ) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "public_id": public_id,
            "cached_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "sections": sections,
            "profile": profile,
        }
        # Write via a temporary file so a crash mid-write cannot leave a
        # half-written entry that later reads would treat as corrupt.
        path = self.path_for(public_id)
        with tempfile.NamedTemporaryFile(
            "w", dir=self._dir, delete=False, encoding="utf-8"
        ) as handle:
            json.dump(payload, handle, indent=2)
            temporary = Path(handle.name)
        temporary.replace(path)

    def clear(self) -> None:
        for path in self._dir.glob("*.json"):
            path.unlink(missing_ok=True)

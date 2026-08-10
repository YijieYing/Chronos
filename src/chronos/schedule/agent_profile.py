"""Cached, human-editable personal context for the Chronos Agent."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AgentProfileSnapshot:
    content: str
    content_hash: str


class AgentProfileCache:
    """Read a profile only when its filesystem fingerprint changes."""

    def __init__(self, path: str | Path | None, max_chars: int = 16_000) -> None:
        if max_chars <= 0:
            raise ValueError("profile_max_chars must be positive")
        self._path = Path(path) if path else None
        self._max_chars = max_chars
        self._fingerprint: tuple[int, int] | None = None
        self._snapshot = AgentProfileSnapshot("", sha256(b"").hexdigest())

    def get(self) -> AgentProfileSnapshot:
        if self._path is None or not self._path.is_file():
            return self._snapshot
        stat = self._path.stat()
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        if fingerprint == self._fingerprint:
            return self._snapshot
        content = self._path.read_text(encoding="utf-8").strip()
        if len(content) > self._max_chars:
            raise ValueError(
                f"Agent profile is {len(content)} characters; limit is {self._max_chars}"
            )
        digest = sha256(content.encode("utf-8")).hexdigest()
        if digest != self._snapshot.content_hash:
            self._snapshot = AgentProfileSnapshot(content, digest)
        self._fingerprint = fingerprint
        return self._snapshot

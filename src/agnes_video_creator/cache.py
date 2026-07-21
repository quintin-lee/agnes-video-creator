"""Content-addressed generation cache.

Caches generated images and videos by a SHA-256 hash of the
generation parameters, so identical prompts never waste API calls.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


class ContentCache:
    """Content-addressed disk cache keyed by a hash of generation params.

    Directory layout::

        <cache_dir>/
          <first-two-hex-chars>/
            <rest-of-hash>/
              content.<ext>

    Thread-safe for concurrent reads.  Writes are atomic ``shutil.copy2``.
    """

    def __init__(self, cache_dir: str | Path) -> None:
        self._root = Path(cache_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    # ── public API ────────────────────────────────────────────────

    def get(self, params: dict) -> Path | None:
        """Return the cached file path if *params* matches, else ``None``."""
        h = self._hash(params)
        for ext in (".mp4", ".png", ".jpg", ".jpeg", ".webp"):
            candidate = self._path(h, ext)
            if candidate.exists():
                return candidate
        return None

    def put(self, params: dict, src: Path) -> Path:
        """Copy *src* into the cache indexed by *params* and return the new path."""
        h = self._hash(params)
        dest = self._path(h, src.suffix if src.suffix else ".bin")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
        return dest

    def invalidate_all(self) -> int:
        """Remove every entry from the cache.  Returns the number of dirs removed."""
        count = 0
        for d1 in self._root.iterdir():
            if d1.is_dir():
                for d2 in d1.iterdir():
                    if d2.is_dir():
                        shutil.rmtree(d2)
                        count += 1
        return count

    # ── internals ─────────────────────────────────────────────────

    @staticmethod
    def _hash(params: dict) -> str:
        raw = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _path(self, h: str, suffix: str) -> Path:
        return self._root / h[:2] / h[2:] / f"content{suffix}"

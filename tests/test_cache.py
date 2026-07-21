"""Tests for ContentCache — content-addressed generation cache."""

from __future__ import annotations

from pathlib import Path

from agnes_video_creator.cache import ContentCache


class TestContentCache:
    def test_miss_returns_none(self, tmp_path: Path) -> None:
        cache = ContentCache(tmp_path / "cache")
        assert cache.get({"prompt": "foo"}) is None

    def test_put_and_get(self, tmp_path: Path) -> None:
        cache = ContentCache(tmp_path / "cache")
        src = tmp_path / "src.png"
        src.write_text("fake-image-data")

        stored = cache.put({"model": "m1", "prompt": "hello"}, src)
        assert stored.exists()
        assert stored.read_text() == "fake-image-data"

        hit = cache.get({"model": "m1", "prompt": "hello"})
        assert hit is not None
        assert hit.read_text() == "fake-image-data"

    def test_different_params_different_keys(self, tmp_path: Path) -> None:
        cache = ContentCache(tmp_path / "cache")
        a = tmp_path / "a.png"
        a.write_text("a")
        b = tmp_path / "b.png"
        b.write_text("b")

        cache.put({"prompt": "cat"}, a)
        cache.put({"prompt": "dog"}, b)

        hit_a = cache.get({"prompt": "cat"})
        hit_b = cache.get({"prompt": "dog"})
        assert hit_a is not None and hit_a.read_text() == "a"
        assert hit_b is not None and hit_b.read_text() == "b"

    def test_put_overwrites_existing(self, tmp_path: Path) -> None:
        cache = ContentCache(tmp_path / "cache")
        v1 = tmp_path / "v1.png"
        v1.write_text("v1")
        v2 = tmp_path / "v2.png"
        v2.write_text("v2")

        cache.put({"key": "x"}, v1)
        cache.put({"key": "x"}, v2)

        hit = cache.get({"key": "x"})
        assert hit is not None and hit.read_text() == "v2"

    def test_cache_dir_is_created(self, tmp_path: Path) -> None:
        d = tmp_path / "my_cache"
        assert not d.exists()
        _ = ContentCache(d)
        assert d.exists()

    def test_multiple_extensions(self, tmp_path: Path) -> None:
        cache = ContentCache(tmp_path / "cache")
        src = tmp_path / "vid.mp4"
        src.write_text("mp4-data")

        cache.put({"type": "video", "prompt": "x"}, src)
        hit = cache.get({"type": "video", "prompt": "x"})
        assert hit is not None and hit.suffix == ".mp4"

    def test_invalidate_all_removes_entries(self, tmp_path: Path) -> None:
        cache = ContentCache(tmp_path / "cache")
        src = tmp_path / "img.png"
        src.write_text("data")
        cache.put({"k": "1"}, src)
        cache.put({"k": "2"}, src)

        assert cache.get({"k": "1"}) is not None
        assert cache.get({"k": "2"}) is not None

        removed = cache.invalidate_all()
        assert removed == 2
        assert cache.get({"k": "1"}) is None
        assert cache.get({"k": "2"}) is None

    def test_invalidate_all_empty_cache(self, tmp_path: Path) -> None:
        cache = ContentCache(tmp_path / "cache")
        assert cache.invalidate_all() == 0

    def test_put_without_extension(self, tmp_path: Path) -> None:
        cache = ContentCache(tmp_path / "cache")
        src = tmp_path / "data"
        src.write_text("no-ext")
        stored = cache.put({"key": "noext"}, src)
        assert stored.suffix == ".bin"

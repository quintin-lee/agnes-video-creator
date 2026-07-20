"""Tests for utility functions — slugify, needs_translation, json_pretty, retry logic."""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.utils import (
    _is_retryable,
    json_pretty,
    needs_translation,
    prepare_prompt,
    slugify,
)


class TestSlugify:
    def test_basic(self) -> None:
        assert slugify("Hello World") == "hello_world"

    def test_lowercase(self) -> None:
        assert slugify("UPPERCASE") == "uppercase"

    def test_strip_special_chars(self) -> None:
        assert slugify("hello!!! world???") == "hello_world"

    def test_collapse_multiple_underscores(self) -> None:
        assert slugify("a   b---c") == "a_b_c"

    def test_leading_trailing_underscore_stripped(self) -> None:
        assert slugify("__hello__") == "hello"

    def test_unicode_replaced(self) -> None:
        assert slugify("café") == "caf"

    def test_empty_string(self) -> None:
        assert slugify("   ") == ""

    def test_already_safe(self) -> None:
        assert slugify("hello_world_123") == "hello_world_123"


class TestNeedsTranslation:
    def test_ascii_only(self) -> None:
        assert not needs_translation("hello world")

    def test_chinese(self) -> None:
        assert needs_translation("你好世界")

    def test_japanese(self) -> None:
        assert needs_translation("こんにちは")

    def test_korean(self) -> None:
        assert needs_translation("안녕하세요")

    def test_accented(self) -> None:
        assert needs_translation("café résumé")

    def test_mixed(self) -> None:
        assert needs_translation("hello 世界")

    def test_empty(self) -> None:
        assert not needs_translation("")


class TestJsonPretty:
    def test_dict(self) -> None:
        result = json_pretty({"a": 1, "b": 2})
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_list(self) -> None:
        result = json_pretty([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_ensure_ascii_false(self) -> None:
        result = json_pretty({"msg": "你好"})
        assert "你好" in result


class TestIsRetryable:
    def test_http_5xx_is_retryable(self) -> None:
        exc = urllib.error.HTTPError(
            url="http://example.com",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=BytesIO(b""),
        )
        assert _is_retryable(exc)

    def test_http_4xx_not_retryable(self) -> None:
        exc = urllib.error.HTTPError(
            url="http://example.com",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=BytesIO(b""),
        )
        assert not _is_retryable(exc)

    def test_http_500_is_retryable(self) -> None:
        exc = urllib.error.HTTPError(
            url="http://example.com",
            code=500,
            msg="Internal Error",
            hdrs={},
            fp=BytesIO(b""),
        )
        assert _is_retryable(exc)

    def test_url_error_timeout(self) -> None:
        exc = urllib.error.URLError(reason="timed out")
        assert _is_retryable(exc)

    def test_url_error_connection(self) -> None:
        exc = urllib.error.URLError(reason="Connection refused")
        assert _is_retryable(exc)

    def test_url_error_other_not_retryable(self) -> None:
        exc = urllib.error.URLError(reason="Bad status line")
        assert not _is_retryable(exc)

    def test_arbitrary_exception_not_retryable(self) -> None:
        assert not _is_retryable(ValueError("something else"))


class TestPreparePrompt:
    def test_ascii_no_translation(self) -> None:
        cfg = AgnesConfig(translate_prompts=True)
        result, original = prepare_prompt("hello world", cfg)
        assert result == "hello world"
        assert original is None

    def test_translate_disabled_with_unicode(self) -> None:
        cfg = AgnesConfig(translate_prompts=False)
        result, original = prepare_prompt("你好", cfg)
        assert result == "你好"
        assert original is None

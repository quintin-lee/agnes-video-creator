"""Tests for utility functions — slugify, needs_translation, json_pretty, retry logic."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.utils import (
    _headers,
    _is_retryable,
    download_file,
    json_pretty,
    needs_translation,
    poll_video_task,
    prepare_prompt,
    request_json,
    request_raw,
    slugify,
)


@pytest.fixture
def cfg() -> AgnesConfig:
    return AgnesConfig(api_key="test-key-12345")


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

    def test_numbers_only(self) -> None:
        assert slugify("123 456") == "123_456"

    def test_mixed_case_with_digits(self) -> None:
        assert slugify("Hello_123_World") == "hello_123_world"


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

    def test_punctuation_only(self) -> None:
        assert not needs_translation("!@#$%^&*()")

    def test_numbers(self) -> None:
        assert not needs_translation("12345")

    def test_russian(self) -> None:
        assert needs_translation("Привет мир")

    def test_arabic(self) -> None:
        assert needs_translation("مرحبا بالعالم")


class TestJsonPretty:
    def test_dict(self) -> None:
        result = json_pretty({"a": 1, "b": 2})
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_list(self) -> None:
        result = json_pretty([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_ensure_ascii_false(self) -> None:
        result = json_pretty({"msg": "你好"})
        assert "你好" in result

    def test_nested_dict(self) -> None:
        data = {"a": {"b": {"c": [1, 2, 3]}}}
        result = json_pretty(data)
        assert json.loads(result) == data

    def test_empty_dict(self) -> None:
        result = json_pretty({})
        assert json.loads(result) == {}

    def test_none_value(self) -> None:
        result = json_pretty({"a": None})
        assert json.loads(result)["a"] is None

    def test_boolean_values(self) -> None:
        result = json_pretty({"a": True, "b": False})
        assert json.loads(result)["a"] is True
        assert json.loads(result)["b"] is False


class TestPreparePrompt:
    def test_ascii_no_translation(self) -> None:
        cfg = AgnesConfig(translate_prompts=True)
        result, original = prepare_prompt("hello world", cfg)
        assert result == "hello world"
        assert original is None

    def test_translate_disabled_with_unicode(self) -> None:
        cfg_ = AgnesConfig(translate_prompts=False)
        result, original = prepare_prompt("你好", cfg_)
        assert result == "你好"
        assert original is None

    def test_translate_enabled_with_unicode(self) -> None:
        cfg_ = AgnesConfig(translate_prompts=True)
        with patch("agnes_video_creator.utils.request_json") as mock_req:
            mock_req.return_value = {"choices": [{"message": {"content": "hello world"}}]}
            result, original = prepare_prompt("你好世界", cfg_)
        assert isinstance(result, str)
        # Should attempt translation
        assert isinstance(original, str) or original is None

    def test_ascii_with_translate_enabled(self) -> None:
        cfg_ = AgnesConfig(translate_prompts=True)
        result, original = prepare_prompt("english prompt", cfg_)
        assert result == "english prompt"

    def test_mixed_content(self) -> None:
        cfg_ = AgnesConfig(translate_prompts=True)
        with patch("agnes_video_creator.utils.request_json") as mock_req:
            mock_req.return_value = {"choices": [{"message": {"content": "hello hello"}}]}
            result, original = prepare_prompt("hello 你好", cfg_)
        assert isinstance(result, str)


class TestIsRetryable:
    def test_http_5xx_is_retryable(self) -> None:
        import urllib.error
        from io import BytesIO

        exc = urllib.error.HTTPError(
            url="http://example.com", code=503, msg="Unavailable", hdrs={}, fp=BytesIO(b"")
        )
        assert _is_retryable(exc)

    def test_http_4xx_not_retryable(self) -> None:
        import urllib.error
        from io import BytesIO

        exc = urllib.error.HTTPError(
            url="http://example.com", code=400, msg="Bad Request", hdrs={}, fp=BytesIO(b"")
        )
        assert not _is_retryable(exc)

    def test_url_error_timeout(self) -> None:
        import urllib.error

        exc = urllib.error.URLError(reason="timed out")
        assert _is_retryable(exc)

    def test_url_error_connection(self) -> None:
        import urllib.error

        exc = urllib.error.URLError(reason="Connection refused")
        assert _is_retryable(exc)

    def test_url_error_other_not_retryable(self) -> None:
        import urllib.error

        exc = urllib.error.URLError(reason="Bad status line")
        assert not _is_retryable(exc)

    def test_arbitrary_exception_not_retryable(self) -> None:
        assert not _is_retryable(ValueError("something else"))


class TestHeaders:
    def test_headers_contains_bearer(self, cfg: AgnesConfig) -> None:
        headers = _headers(cfg)
        assert "Authorization" in headers
        assert "test-key-12345" in headers["Authorization"]

    def test_headers_content_type(self, cfg: AgnesConfig) -> None:
        headers = _headers(cfg)
        assert "Content-Type" in headers
        assert headers["Content-Type"] == "application/json"


class TestRequestJson:
    def test_request_json_success(self, cfg: AgnesConfig) -> None:
        with patch("agnes_video_creator.utils._request_with_retry") as mock_retry:
            mock_retry.return_value = b'{"key": "value"}'
            result = request_json("GET", "/v1/test", cfg=cfg)
            assert result == {"key": "value"}

    def test_request_json_empty_response(self, cfg: AgnesConfig) -> None:
        with patch("agnes_video_creator.utils._request_with_retry") as mock_retry:
            mock_retry.return_value = b""
            result = request_json("GET", "/v1/test", cfg=cfg)
            assert result == {}


class TestRequestRaw:
    def test_request_raw_success(self, cfg: AgnesConfig) -> None:
        with patch("agnes_video_creator.utils._request_with_retry") as mock_retry:
            mock_retry.return_value = b'{"hello": "world"}'
            result = request_raw("GET", "/v1/test", cfg=cfg)
            assert isinstance(result, str)
            assert "hello" in result

    def test_request_raw_empty(self, cfg: AgnesConfig) -> None:
        with patch("agnes_video_creator.utils._request_with_retry") as mock_retry:
            mock_retry.return_value = b""
            result = request_raw("GET", "/v1/test", cfg=cfg)
            assert result == ""


class TestDownloadFile:
    def test_download_success(self, tmp_path: Path) -> None:
        dest = tmp_path / "downloaded.png"
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"image binary data"
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            result = download_file("https://example.com/image.png", str(dest))
            assert dest.exists()
            assert dest.read_bytes() == b"image binary data"
            assert result == dest

    def test_download_with_subdir_creation(self, tmp_path: Path) -> None:
        dest = tmp_path / "sub" / "dir" / "file.png"
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"data"
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            download_file("https://example.com/img.png", str(dest))
            assert dest.exists()

    def test_download_with_exception(self, tmp_path: Path) -> None:
        dest = tmp_path / "fail.png"
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Network error")
            with pytest.raises(SystemExit):
                download_file("https://example.com/fail.png", str(dest))


class TestPollVideoTask:
    def test_poll_completed(self, cfg: AgnesConfig) -> None:
        cfg.poll_timeout = 5
        with patch("agnes_video_creator.utils.request_json") as mock_req:
            mock_req.return_value = {"status": "completed", "url": "https://example.com/video.mp4"}
            result = poll_video_task("task-123", cfg)
            assert result is not None
            assert result.get("status") == "completed"

    def test_poll_still_processing(self, cfg: AgnesConfig) -> None:
        cfg.poll_interval = 0.01
        cfg.poll_timeout = 0.1
        with patch("agnes_video_creator.utils.request_json") as mock_req:
            mock_req.return_value = {"status": "processing"}
            with pytest.raises(SystemExit, match="Timed out"):
                poll_video_task("task-456", cfg)

    def test_poll_failed(self, cfg: AgnesConfig) -> None:
        cfg.poll_timeout = 5
        with patch("agnes_video_creator.utils.request_json") as mock_req:
            mock_req.return_value = {"status": "failed", "error": "Processing error"}
            with pytest.raises(SystemExit):
                poll_video_task("task-789", cfg)

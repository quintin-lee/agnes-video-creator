"""Tests for character portrait generation — prompt injection, URL extraction."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agnes_video_creator.models import Character, Script, Scene
from agnes_video_creator.portraits import (
    _extract_portrait_url,
    generate_character_portraits,
    inject_portraits_into_prompt,
)


@pytest.fixture
def script() -> Script:
    return Script(
        title="Test",
        description="A test",
        total_duration=5.0,
        scenes=[Scene(id=1, narration="Scene 1", visual_prompt="a cat")],
        characters=[
            Character(name="John", role="protagonist", appearance="A hero"),
            Character(name="Jane", role="antagonist", appearance="A villain"),
        ],
    )


class TestInjectPortraitsIntoPrompt:
    def test_with_appearances(self) -> None:
        char = Character(name="John", role="protagonist", appearance="Hero", portrait_url="https://example.com/j.png")
        result = inject_portraits_into_prompt("John walks in.", ["John"], [char])
        assert isinstance(result, str)
        assert "John" in result

    def test_empty_appearances(self) -> None:
        assert inject_portraits_into_prompt("Text.", [], []) == "Text."

    def test_multiple_chars(self) -> None:
        chars = [
            Character(name="John", role="protagonist", appearance="Hero", portrait_url="https://ex.com/j.png"),
            Character(name="Jane", role="antagonist", appearance="Villain", portrait_url="https://ex.com/jn.png"),
        ]
        result = inject_portraits_into_prompt("John and Jane.", ["John", "Jane"], chars)
        assert isinstance(result, str)


class TestExtractPortraitUrl:
    def test_from_data_list(self) -> None:
        data = {"data": [{"url": "https://example.com/p.png"}]}
        assert _extract_portrait_url(data) == "https://example.com/p.png"

    def test_from_url_field(self) -> None:
        data = {"url": "https://example.com/p.png"}
        assert _extract_portrait_url(data) == "https://example.com/p.png"

    def test_no_url(self) -> None:
        assert _extract_portrait_url({}) is None

    def test_non_http_url(self) -> None:
        data = {"url": "data:image/png;base64,abc"}
        assert _extract_portrait_url(data) is None


class TestGenerateCharacterPortraits:
    def test_generate(self, script: Script) -> None:
        from agnes_video_creator.config import AgnesConfig
        cfg = AgnesConfig(api_key="test-key")
        with (
            patch("agnes_video_creator.portraits.request_json") as mock_req,
            patch("agnes_video_creator.portraits.download_file") as mock_dl,
            patch("agnes_video_creator.portraits.analyze_face") as mock_face,
        ):
            mock_req.return_value = {"data": [{"url": "https://ex.com/p.png"}]}
            mock_dl.return_value = Path("/tmp/portrait.png")
            mock_face.return_value = None
            result = generate_character_portraits(script, cfg=cfg, verbose=False)
            assert result is script

    def test_missing_key(self, script: Script) -> None:
        from agnes_video_creator.config import AgnesConfig
        cfg = AgnesConfig(api_key="")
        with pytest.raises(SystemExit):
            generate_character_portraits(script, cfg=cfg, verbose=False)

    def test_api_error(self, script: Script) -> None:
        from agnes_video_creator.config import AgnesConfig
        cfg = AgnesConfig(api_key="test-key")
        with patch("agnes_video_creator.portraits.request_json") as mock_req:
            mock_req.side_effect = SystemExit("API Error")
            with pytest.raises(SystemExit):
                generate_character_portraits(script, cfg=cfg, verbose=False)

"""Shared fixtures for agnes-video-creator tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agnes_video_creator.models import Character, Scene, Script

# ── Helpers ──────────────────────────────────────────────────────────────


def make_script(**overrides: Any) -> Script:
    """Build a minimal Script with sensible defaults."""
    scenes = [
        Scene(
            id=1,
            narration="第一幕开场",
            visual_prompt="A red fox in a snowy forest, cinematic lighting",
            duration_seconds=5.0,
            camera="缓慢推近",
            style="cinematic",
            character_appearances=["红狐"],
            dialogues=[{"character": "红狐", "line": "你好"}],
        ),
        Scene(
            id=2,
            narration="第二幕发展",
            visual_prompt="The fox meets a rabbit, golden hour",
            duration_seconds=7.0,
            camera="跟拍",
            style="cinematic",
            character_appearances=["红狐", "兔子"],
            dialogues=[{"character": "兔子", "line": "你是谁"}],
        ),
    ]
    return Script(
        title="测试视频",
        description="一个简单的测试故事",
        total_duration=12.0,
        scenes=scenes,
        style_guide="温暖自然风格",
        mood="愉快",
        target_audience="儿童",
        output_dir="/tmp/test_output",
        characters=[
            Character(
                name="红狐",
                appearance="red fox with bright orange fur",
                role="主角",
                personality="机智勇敢",
                age="young adult",
            ),
            Character(
                name="兔子",
                appearance="white rabbit with pink eyes",
                role="配角",
                personality="胆小害羞",
                age="teen",
            ),
        ],
        episode=1,
    )


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_script() -> Script:
    return make_script()


@pytest.fixture
def sample_script_dict() -> dict[str, Any]:
    return make_script().to_dict()


@pytest.fixture
def temp_output(tmp_path: Path) -> Path:
    return tmp_path / "test_output"

"""Tests for storyboard HTML generation — SVG, image resolution, camera badges."""
from __future__ import annotations

from pathlib import Path

from agnes_video_creator.models import Character, Scene, Script
from agnes_video_creator.storyboard import (
    _camera_badge,
    _escape,
    _resolve_image_src,
    generate_storyboard_html,
)


def _make_script(
    scenes: list[dict] | None = None,
    title: str = "Test",
    description: str = "A test script",
) -> Script:
    scene_objs = []
    for s in (scenes or []):
        scene_objs.append(
            Scene(
                id=s.get("id", "s1"),
                narration=s.get("title", ""),
                visual_prompt=s.get("visual_prompt", "a scene"),
                image_url=s.get("image_url", ""),
                image_path=s.get("image_path", ""),
                camera=s.get("camera", "static"),
                style=s.get("style", "cinematic"),
                duration_seconds=s.get("duration_seconds", 5.0),
                character_appearances=s.get("character_appearances", []),
                dialogues=s.get("dialogues", []),
            )
        )
    return Script(
        title=title,
        description=description,
        total_duration=5.0,
        scenes=scene_objs,
        characters=[Character(name="Test", role="protagonist")],
    )


class TestEscape:
    def test_html(self) -> None:
        assert "&lt;" in _escape("<hello>")
        assert "&gt;" in _escape("<hello>")

    def test_ampersand(self) -> None:
        assert "&amp;" in _escape("AT&T")

    def test_quotes(self) -> None:
        assert "&quot;" in _escape('He said "hello"')

    def test_plain(self) -> None:
        assert _escape("Hello") == "Hello"

    def test_empty(self) -> None:
        assert _escape("") == ""


class TestResolveImageSrc:
    def test_absolute_url(self) -> None:
        assert _resolve_image_src("https://example.com/i.png") == "https://example.com/i.png"

    def test_empty(self) -> None:
        assert _resolve_image_src("") == ""

    def test_none(self) -> None:
        assert _resolve_image_src(None) == ""

    def test_data_uri(self) -> None:
        assert _resolve_image_src("data:image/png;base64,abc") == "data:image/png;base64,abc"

    def test_relative(self) -> None:
        result = _resolve_image_src("images/scene1.png")
        assert result.startswith("file://") or "/" in result


class TestCameraBadge:
    def test_basic(self) -> None:
        assert "medium" in _camera_badge("medium").lower()

    def test_empty(self) -> None:
        assert isinstance(_camera_badge(""), str)

    def test_zoom(self) -> None:
        assert "zoom" in _camera_badge("zoom-in").lower()

    def test_pan(self) -> None:
        assert "pan" in _camera_badge("pan-left").lower()


class TestGenerateStoryboardHtml:
    def test_empty_scenes(self, tmp_path: Path) -> None:
        script = _make_script()
        out = generate_storyboard_html(script, tmp_path / "out.html")
        assert out.exists()

    def test_with_scene(self, tmp_path: Path) -> None:
        script = _make_script(
            [{"id": "1", "title": "Opening", "image_url": "https://ex.com/i.png"}]
        )
        out = generate_storyboard_html(script, tmp_path / "out.html")
        content = out.read_text()
        assert "Opening" in content

    def test_multiple_scenes(self, tmp_path: Path) -> None:
        script = _make_script([
            {"id": "1", "title": "Scene 1"},
            {"id": "2", "title": "Scene 2"},
        ])
        out = generate_storyboard_html(script, tmp_path / "out.html")
        content = out.read_text()
        assert "Scene 1" in content
        assert "Scene 2" in content

    def test_dialogues(self, tmp_path: Path) -> None:
        script = _make_script(
            [{
                "id": "1",
                "title": "Talk",
                "dialogues": [{"character": "Alice", "line": "Hello!"}],
            }]
        )
        out = generate_storyboard_html(script, tmp_path / "out.html")
        assert "Hello!" in out.read_text()

    def test_characters_listed(self, tmp_path: Path) -> None:
        script = _make_script([{"id": "1"}])
        out = generate_storyboard_html(script, tmp_path / "out.html")
        assert "Test" in out.read_text()

"""Tests for the Project — dataclass, episode state machine, helper parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agnes_video_creator.project import EpisodeInfo, Project, find_project


class TestProjectDataclass:
    """Project is a plain dataclass — test instantiation and field defaults."""

    def test_minimal_init(self) -> None:
        p = Project(name="test", root="/tmp/test_project")
        assert p.name == "test"
        assert p.root == "/tmp/test_project"
        assert p.episodes == []
        assert p.add_audio is True
        assert p.add_subtitles is True
        assert p.video_mode == "image-to-video"
        assert p.parallel is False

    def test_init_with_optional_fields(self) -> None:
        p = Project(
            name="drama",
            root="/tmp/drama",
            novel_path="/tmp/drama/novel.txt",
            style_guide="cyberpunk",
            mood="dark",
            target_audience="adults",
            add_audio=False,
            parallel=True,
            video_mode="text-to-video",
        )
        assert p.novel_path == "/tmp/drama/novel.txt"
        assert p.style_guide == "cyberpunk"
        assert p.mood == "dark"
        assert p.target_audience == "adults"
        assert p.add_audio is False
        assert p.parallel is True
        assert p.video_mode == "text-to-video"

    def test_with_episodes(self) -> None:
        episodes = [
            EpisodeInfo(number=1, title="Chapter 1", status="script_ready"),
            EpisodeInfo(number=2, title="Chapter 2"),
        ]
        p = Project(name="multi", root="/tmp/multi", episodes=episodes)
        assert len(p.episodes) == 2
        assert p.episodes[0].title == "Chapter 1"

    def test_characters(self) -> None:
        chars = [{"name": "John", "role": "protagonist"}]
        p = Project(name="chars", root="/tmp/chars", characters=chars)
        assert p.characters == chars

    def test_created_at_default(self) -> None:
        p = Project(name="t", root="/tmp/t")
        assert p.created_at == ""


class TestEpisodeInfo:
    """EpisodeInfo state machine."""

    def test_default_status(self) -> None:
        ep = EpisodeInfo(number=1)
        assert ep.status == "pending"

    def test_advance_to_script_ready(self) -> None:
        ep = EpisodeInfo(number=1)
        ep.advance()
        assert ep.status == "script_ready"

    def test_advance_through_all_states(self) -> None:
        ep = EpisodeInfo(number=1)
        states = [ep.status]
        for _ in range(4):
            ep.advance()
            states.append(ep.status)
        assert states == ["pending", "script_ready", "images_ready", "videos_ready", "assembled"]

    def test_advance_beyond_assembled(self) -> None:
        ep = EpisodeInfo(number=1, status="assembled")
        ep.advance()
        assert ep.status == "assembled"  # stays at terminal state

    def test_advance_from_unknown_state(self) -> None:
        ep = EpisodeInfo(number=1, status="unknown")
        ep.advance()
        assert ep.status == "unknown"  # unknown state stays put

    def test_custom_fields(self) -> None:
        ep = EpisodeInfo(
            number=3,
            title="Battle Scene",
            status="images_ready",
            script_path="scripts/ep03.json",
            image_dir="images/ep03/",
            video_dir="videos/ep03/",
            output_path="output/ep03.mp4",
        )
        assert ep.title == "Battle Scene"
        assert ep.script_path == "scripts/ep03.json"
        assert ep.image_dir == "images/ep03/"
        assert ep.output_path == "output/ep03.mp4"

    def test_default_paths(self) -> None:
        ep = EpisodeInfo(number=2)
        assert ep.script_path == ""
        assert ep.image_dir == ""
        assert ep.video_dir == ""
        assert ep.output_path == ""


class TestFindProject:
    def test_find_project_root(self, tmp_path: Path) -> None:
        d = tmp_path / "my_project"
        d.mkdir()
        (d / "project.json").touch()
        found = find_project(d)
        assert found is not None
        assert found.name == "project.json" or "project" in str(found)

    def test_find_project_no_project(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        result = find_project(d)
        assert result is None

    def test_episode_state_constants(self) -> None:
        from agnes_video_creator.project import EPISODE_STATES, _NEXT_STATE

        assert "pending" in EPISODE_STATES
        assert "script_ready" in EPISODE_STATES
        assert "assembled" in EPISODE_STATES
        assert _NEXT_STATE["pending"] == "script_ready"
        assert _NEXT_STATE["assembled"] is None

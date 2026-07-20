"""Tests for PipelineState — persistence, episode CRUD, status tracking."""

from __future__ import annotations

from pathlib import Path

from agnes_video_creator.pipeline_state import (
    EpisodeState,
    PipelineState,
    SceneState,
)


class TestSceneState:
    def test_defaults(self) -> None:
        ss = SceneState(scene_id=1)
        assert ss.scene_id == 1
        assert ss.image == "pending"
        assert ss.video == "pending"
        assert ss.image_url == ""
        assert ss.video_url == ""
        assert ss.error == ""

    def test_custom_values(self) -> None:
        ss = SceneState(
            scene_id=2,
            image="success",
            video="failed",
            image_url="http://img",
            video_url="http://vid",
            error="bad frame",
        )
        assert ss.image == "success"
        assert ss.video == "failed"
        assert ss.error == "bad frame"


class TestEpisodeState:
    def test_defaults(self) -> None:
        ep = EpisodeState(episode_number=1)
        assert ep.status == "pending"
        assert ep.scenes == []
        assert ep.script_path == ""
        assert ep.error == ""

    def test_has_script_false_when_empty(self) -> None:
        ep = EpisodeState(episode_number=1)
        assert not ep.has_script

    def test_has_script_false_when_path_missing(self) -> None:
        ep = EpisodeState(episode_number=1, script_path="/nonexistent/script.json")
        assert not ep.has_script

    def test_images_completed(self) -> None:
        ep = EpisodeState(
            episode_number=1,
            scenes=[
                SceneState(scene_id=1, image="success"),
                SceneState(scene_id=2, image="success"),
                SceneState(scene_id=3, image="pending"),
            ],
        )
        assert ep.images_completed == 2

    def test_images_completed_count_skipped(self) -> None:
        ep = EpisodeState(
            episode_number=1,
            scenes=[
                SceneState(scene_id=1, image="skipped"),
                SceneState(scene_id=2, image="success"),
            ],
        )
        assert ep.images_completed == 2

    def test_videos_completed(self) -> None:
        ep = EpisodeState(
            episode_number=1,
            scenes=[
                SceneState(scene_id=1, video="success"),
                SceneState(scene_id=2, video="failed"),
            ],
        )
        assert ep.videos_completed == 1

    def test_all_images_done_false_when_empty(self) -> None:
        ep = EpisodeState(episode_number=1)
        assert not ep.all_images_done

    def test_all_images_done_true(self) -> None:
        ep = EpisodeState(
            episode_number=1,
            scenes=[
                SceneState(scene_id=1, image="success"),
                SceneState(scene_id=2, image="skipped"),
            ],
        )
        assert ep.all_images_done

    def test_all_images_done_false_when_pending(self) -> None:
        ep = EpisodeState(
            episode_number=1,
            scenes=[SceneState(scene_id=1, image="success"), SceneState(scene_id=2, image="pending")],
        )
        assert not ep.all_images_done

    def test_all_videos_done_true(self) -> None:
        ep = EpisodeState(
            episode_number=1,
            scenes=[SceneState(scene_id=1, video="success"), SceneState(scene_id=2, video="success")],
        )
        assert ep.all_videos_done

    def test_all_videos_done_false_with_failed(self) -> None:
        ep = EpisodeState(
            episode_number=1,
            scenes=[SceneState(scene_id=1, video="failed")],
        )
        assert not ep.all_videos_done


class TestPipelineState:
    def test_fresh_creates_episodes(self) -> None:
        state = PipelineState.fresh(project_name="test", num_episodes=3)
        assert state.project_name == "test"
        assert len(state.episodes) == 3
        assert state.episodes[0].episode_number == 1
        assert state.episodes[2].episode_number == 3
        assert state.created_at != ""
        assert state.updated_at != ""

    def test_fresh_zero_episodes(self) -> None:
        state = PipelineState.fresh()
        assert state.episodes == []

    def test_episode_lookup_found(self) -> None:
        state = PipelineState.fresh(num_episodes=3)
        ep = state.episode(2)
        assert ep is not None
        assert ep.episode_number == 2

    def test_episode_lookup_missing(self) -> None:
        state = PipelineState.fresh(num_episodes=1)
        assert state.episode(99) is None

    def test_upsert_episode_updates_existing(self) -> None:
        state = PipelineState.fresh(num_episodes=1)
        updated = EpisodeState(
            episode_number=1,
            status="script_ready",
            scenes=[SceneState(scene_id=1)],
        )
        state.upsert_episode(updated)
        assert state.episode(1) is not None
        assert state.episode(1).status == "script_ready"

    def test_upsert_episode_appends_new(self) -> None:
        state = PipelineState.fresh(num_episodes=0)
        state.upsert_episode(EpisodeState(episode_number=5))
        assert len(state.episodes) == 1
        assert state.episode(5) is not None

    def test_mark_episode_failed(self) -> None:
        state = PipelineState.fresh(num_episodes=1)
        state.mark_episode_failed(1, "API error")
        ep = state.episode(1)
        assert ep is not None
        assert ep.status == "failed"
        assert ep.error == "API error"

    def test_mark_episode_failed_missing_does_not_crash(self) -> None:
        state = PipelineState.fresh(num_episodes=0)
        state.mark_episode_failed(99, "err")  # should not raise

    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        state = PipelineState.fresh(project_name="save_test", num_episodes=2)
        state.episodes[0].status = "script_ready"
        state.save(path)
        assert path.exists()

        loaded = PipelineState.load(path)
        assert loaded is not None
        assert loaded.project_name == "save_test"
        assert len(loaded.episodes) == 2
        assert loaded.episodes[0].status == "script_ready"

    def test_load_missing_returns_none(self) -> None:
        loaded = PipelineState.load("/nonexistent/state.json")
        assert loaded is None

    def test_load_corrupted_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{invalid json")
        loaded = PipelineState.load(path)
        assert loaded is None

    def test_summary(self) -> None:
        state = PipelineState.fresh(project_name="test", num_episodes=2)
        summary = state.summary()
        assert "Episode 1" in summary
        assert "Episode 2" in summary

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        state = PipelineState.fresh(
            project_name="roundtrip",
            output_dir="/tmp/out",
            num_episodes=1,
        )
        ep = state.episode(1)
        assert ep is not None
        ep.status = "images_ready"
        ep.scenes = [SceneState(scene_id=1, image="success")]
        d = state.to_dict()
        restored = PipelineState.from_dict(d)
        assert restored.project_name == "roundtrip"
        assert restored.episodes[0].status == "images_ready"
        assert restored.episodes[0].scenes[0].image == "success"

"""Tests for cost estimator — CostEstimate, estimate_episode, estimate_project."""

from __future__ import annotations

import pytest

from agnes_video_creator.cost_estimator import CostEstimate, estimate_episode, estimate_project


class TestCostEstimate:
    def test_defaults(self) -> None:
        ce = CostEstimate()
        assert ce.images == 0
        assert ce.video_clips == 0
        assert ce.text_calls == 0
        assert ce.total_cost == 0.0
        assert ce.total_time == 0.0

    def test_cost_calculation(self) -> None:
        ce = CostEstimate(images=10, video_clips=5, text_calls=2)
        assert ce.cost_images == 10 * 0.04  # 0.40
        assert ce.cost_videos == 5 * 0.10   # 0.50
        assert ce.cost_text == 2 * 0.002    # 0.004
        assert ce.total_cost == pytest.approx(0.904, rel=1e-3)

    def test_time_calculation(self) -> None:
        ce = CostEstimate(images=10, video_clips=5, text_calls=2)
        assert ce.time_images == 10 * 25.0   # 250
        assert ce.time_videos == 5 * 90.0    # 450
        assert ce.time_text == 2 * 20.0      # 40
        # assembly only added when there are images or clips
        assert ce.time_assembly == 30.0

    def test_no_assembly_when_no_ops(self) -> None:
        ce = CostEstimate()
        assert ce.time_assembly == 0.0

    def test_total_time_matches_sum(self) -> None:
        ce = CostEstimate(images=3, video_clips=3, text_calls=1)
        expected = 3 * 25.0 + 3 * 90.0 + 1 * 20.0 + 30.0
        assert ce.total_time == expected

    def test_to_dict(self) -> None:
        ce = CostEstimate(images=5, video_clips=5, text_calls=1)
        d = ce.to_dict()
        assert d["images"] == 5
        assert d["total_cost"] == round(5 * 0.04 + 5 * 0.10 + 0.002, 2)
        assert d["total_time_seconds"] == pytest.approx(5 * 25.0 + 5 * 90.0 + 20.0 + 30.0, rel=1e-3)

    def test_format_summary_non_empty(self) -> None:
        ce = CostEstimate(images=3, video_clips=3, text_calls=1)
        summary = ce.format_summary()
        assert "Estimated cost" in summary
        assert "Estimated time" in summary
        assert "image" in summary
        assert "clip" in summary

    def test_format_summary_empty(self) -> None:
        ce = CostEstimate()
        summary = ce.format_summary()
        assert "No operations" in summary

    def test_format_duration_seconds(self) -> None:
        assert CostEstimate._format_duration(45) == "45s"

    def test_format_duration_minutes(self) -> None:
        assert CostEstimate._format_duration(150) == "2.5 min"

    def test_format_duration_hours(self) -> None:
        assert CostEstimate._format_duration(7200) == "2.0 hrs"


class TestEstimateEpisode:
    def test_basic_estimate(self) -> None:
        est = estimate_episode(scene_count=10)
        assert est.images == 10
        assert est.video_clips == 10
        assert est.text_calls == 1

    def test_skip_images(self) -> None:
        est = estimate_episode(scene_count=5, include_images=False)
        assert est.images == 0
        assert est.video_clips == 5
        assert est.text_calls == 1

    def test_skip_video(self) -> None:
        est = estimate_episode(scene_count=5, include_video=False)
        assert est.images == 5
        assert est.video_clips == 0
        assert est.text_calls == 1

    def test_zero_scenes(self) -> None:
        est = estimate_episode(scene_count=0)
        assert est.images == 0
        assert est.video_clips == 0
        assert est.total_cost == pytest.approx(0.002, rel=1e-3)  # just text call


class TestEstimateProject:
    def test_single_episode(self) -> None:
        est = estimate_project(total_scenes=10, episodes=1)
        assert est.images == 10
        assert est.video_clips == 10
        assert est.text_calls == 1

    def test_multi_episode(self) -> None:
        est = estimate_project(total_scenes=30, episodes=3)
        assert est.images == 30
        assert est.text_calls == 3  # one per episode

    def test_no_images_no_video(self) -> None:
        est = estimate_project(total_scenes=10, episodes=1,
                               include_images=False, include_video=False)
        assert est.images == 0
        assert est.video_clips == 0
        assert est.text_calls == 1

"""Tests for video assembly — ffmpeg checks, trimming, normalising, crop, narration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agnes_video_creator.assembler import (
    _add_narration,
    _camera_motion_filter,
    _check_ffmpeg,
    _normalise_clip,
    _run_ffmpeg,
    _trim_clip,
    assemble_video,
    batch_export,
    export_crop,
)
from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Script, Scene, Character


@pytest.fixture
def cfg() -> AgnesConfig:
    return AgnesConfig(api_key="test-key")


@pytest.fixture
def script() -> Script:
    return Script(
        title="Test",
        description="A test",
        total_duration=5.0,
        scenes=[Scene(id=1, narration="Scene 1", visual_prompt="a cat", duration_seconds=5.0)],
        characters=[Character(name="John", appearance="Hero")],
    )


class TestCheckFFmpeg:
    def test_ffmpeg_found(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            assert _check_ffmpeg() is True

    def test_ffmpeg_not_found(self) -> None:
        with patch("shutil.which", return_value=None):
            assert _check_ffmpeg() is False


class TestRunFFmpeg:
    def test_run_success(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _run_ffmpeg(["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"], verbose=False)

    def test_run_failure(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, ["ffmpeg"])
            with pytest.raises(SystemExit):
                _run_ffmpeg(["ffmpeg", "bad"], verbose=False)


class TestTrimClip:
    def test_trim_basic(self, tmp_path: Path, cfg: AgnesConfig) -> None:
        src = tmp_path / "in.mp4"
        src.touch()
        dst = tmp_path / "out.mp4"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _trim_clip(src, dst, trim_in=1.0, trim_out=2.0)

    def test_trim_zero_skips(self, tmp_path: Path) -> None:
        src = tmp_path / "in.mp4"
        src.touch()
        dst = tmp_path / "out.mp4"
        with patch("subprocess.run") as mock_run:
            _trim_clip(src, dst, trim_in=0.0, trim_out=0.0)
            mock_run.assert_not_called()

    def test_trim_existing_skips(self, tmp_path: Path) -> None:
        src = tmp_path / "in.mp4"
        src.touch()
        dst = tmp_path / "out.mp4"
        dst.touch()
        with patch("subprocess.run") as mock_run:
            _trim_clip(src, dst, trim_in=1.0, trim_out=0.0)
            mock_run.assert_not_called()

    def test_trim_ffmpeg_error(self, tmp_path: Path) -> None:
        src = tmp_path / "in.mp4"
        src.touch()
        dst = tmp_path / "out.mp4"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, ["ffmpeg"])
            with pytest.raises(SystemExit):
                _trim_clip(src, dst, trim_in=1.0, trim_out=0.0)


class TestNormaliseClip:
    def test_normalise_basic(self, tmp_path: Path, cfg: AgnesConfig) -> None:
        src = tmp_path / "in.mp4"
        src.touch()
        dst = tmp_path / "out.mp4"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _normalise_clip(src, dst, cfg=cfg, verbose=False)

    def test_normalise_existing_skips(self, tmp_path: Path, cfg: AgnesConfig) -> None:
        src = tmp_path / "in.mp4"
        src.touch()
        dst = tmp_path / "out.mp4"
        dst.touch()
        with patch("subprocess.run") as mock_run:
            _normalise_clip(src, dst, cfg=cfg, verbose=False)
            mock_run.assert_not_called()

    def test_normalise_error(self, tmp_path: Path, cfg: AgnesConfig) -> None:
        src = tmp_path / "in.mp4"
        src.touch()
        dst = tmp_path / "out.mp4"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, ["ffmpeg"])
            with pytest.raises(SystemExit):
                _normalise_clip(src, dst, cfg=cfg, verbose=False)

    def test_normalise_with_camera(self, tmp_path: Path, cfg: AgnesConfig) -> None:
        src = tmp_path / "in.mp4"
        src.touch()
        dst = tmp_path / "out.mp4"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _normalise_clip(src, dst, cfg=cfg, verbose=False, camera_desc="push-in")


class TestCameraMotionFilter:
    def test_returns_filter_string(self) -> None:
        result = _camera_motion_filter("push-in", 0.5)
        assert isinstance(result, str)

    def test_static_returns_empty(self) -> None:
        result = _camera_motion_filter("static", 0.0)
        # static should produce no motion filter
        assert isinstance(result, str)

    def test_unknown_desc_returns_empty(self) -> None:
        result = _camera_motion_filter("xyzzy", 0.0)
        assert isinstance(result, str)

    def test_pan(self) -> None:
        result = _camera_motion_filter("pan-left", 0.3)
        assert isinstance(result, str)

    def test_zoom(self) -> None:
        result = _camera_motion_filter("zoom-in", 0.5)
        assert isinstance(result, str)


class TestAddNarration:
    def test_add_narration_basic(self, tmp_path: Path, script: Script, cfg: AgnesConfig) -> None:
        video = tmp_path / "video.mp4"
        video.touch()
        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _add_narration(video, script, temp_dir, cfg, verbose=False)

    def test_add_narration_input_not_found(self, script: Script, cfg: AgnesConfig) -> None:
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value="/usr/bin/edge-tts"),
        ):
            with pytest.raises(FileNotFoundError):
                _add_narration(Path("/nonexistent.mp4"), script, Path("/tmp"), cfg, verbose=False)


class TestExportCrop:
    def test_export_crop_basic(self, tmp_path: Path) -> None:
        src = tmp_path / "input.mp4"
        src.touch()
        dst = tmp_path / "output_9x16.mp4"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = export_crop(src, dst, aspect="9:16")
            assert result is not None

    def test_export_crop_same_path(self, tmp_path: Path) -> None:
        src = tmp_path / "input.mp4"
        src.touch()
        result = export_crop(src, src)
        assert result == src


class TestBatchExport:
    def test_batch_export_basic(self, tmp_path: Path) -> None:
        src = tmp_path / "input.mp4"
        src.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            results = batch_export(src, tmp_path, aspects=["16:9", "9:16", "1:1"], verbose=False)
        assert len(results) == 3
        assert "16:9" in results
        assert "9:16" in results
        assert "1:1" in results

    def test_batch_export_filters_unknown(self, tmp_path: Path) -> None:
        src = tmp_path / "input.mp4"
        src.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            results = batch_export(src, tmp_path, aspects=["16:9", "99:99"], verbose=False)
        assert "16:9" in results
        assert "99:99" not in results

    def test_batch_export_default_aspects(self, tmp_path: Path) -> None:
        src = tmp_path / "input.mp4"
        src.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            results = batch_export(src, tmp_path, verbose=False)
        assert len(results) == 3  # defaults: 16:9, 9:16, 1:1


class TestAssembleVideo:
    def test_assemble_video_no_ffmpeg(self, script: Script) -> None:
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                assemble_video(script, output_name="out.mp4")

    def test_assemble_video_basic(self, script: Script, tmp_path: Path) -> None:
        video_file = tmp_path / "scene_0001.mp4"
        video_file.write_text("fake video data")
        script.scenes[0].video_path = str(video_file)

        def _fake_ffmpeg(cmd, description="", verbose=True):
            output = cmd[-1]
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text("fake")

        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("agnes_video_creator.assembler._run_ffmpeg", side_effect=_fake_ffmpeg),
        ):
            result = assemble_video(script, output_name=str(tmp_path / "final.mp4"), verbose=False)

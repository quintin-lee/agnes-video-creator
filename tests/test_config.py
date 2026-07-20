"""Tests for AgnesConfig — defaults, from_env, has_api_key, paths."""

from __future__ import annotations

import os
from pathlib import Path

from agnes_video_creator.config import AgnesConfig


class TestAgnesConfigDefaults:
    def test_default_api_key_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.delenv("AGNES_API_TOKEN", raising=False)
        monkeypatch.delenv("APIHUB_AGNES_API_KEY", raising=False)
        cfg = AgnesConfig()
        assert cfg.api_key == ""

    def test_default_base_url(self) -> None:
        cfg = AgnesConfig()
        assert cfg.base_url == "https://apihub.agnes-ai.com"

    def test_default_text_model(self) -> None:
        cfg = AgnesConfig()
        assert cfg.text_model == "agnes-2.0-flash"

    def test_default_image_model(self) -> None:
        cfg = AgnesConfig()
        assert cfg.image_model == "agnes-image-2.1-flash"

    def test_default_video_model(self) -> None:
        cfg = AgnesConfig()
        assert cfg.video_model == "agnes-video-v2.0"

    def test_default_output_dir(self) -> None:
        cfg = AgnesConfig()
        assert cfg.output_dir == "agnes_video_output"

    def test_default_temp_dir_is_under_output(self) -> None:
        cfg = AgnesConfig()
        assert cfg.temp_dir == os.path.join("agnes_video_output", ".tmp")

    def test_default_translate_true(self) -> None:
        cfg = AgnesConfig()
        assert cfg.translate_prompts

    def test_default_add_audio_true(self) -> None:
        cfg = AgnesConfig()
        assert cfg.add_audio

    def test_default_add_subtitles_true(self) -> None:
        cfg = AgnesConfig()
        assert cfg.add_subtitles

    def test_has_api_key_false_when_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.delenv("AGNES_API_TOKEN", raising=False)
        monkeypatch.delenv("APIHUB_AGNES_API_KEY", raising=False)
        cfg = AgnesConfig()
        assert not cfg.has_api_key

    def test_has_api_key_true_when_set(self) -> None:
        cfg = AgnesConfig(api_key="sk-123")
        assert cfg.has_api_key

    def test_resolved_output_is_absolute(self) -> None:
        cfg = AgnesConfig(output_dir="tmp_out")
        assert cfg.resolved_output.is_absolute()

    def test_resolved_temp_is_absolute(self) -> None:
        cfg = AgnesConfig(output_dir="tmp_out")
        assert cfg.resolved_temp.is_absolute()
        assert str(cfg.resolved_temp).endswith(".tmp")

    def test_ensure_dirs_creates_directories(self, tmp_path: Path) -> None:
        out = tmp_path / "output"
        cfg = AgnesConfig(output_dir=str(out))
        cfg.ensure_dirs()
        assert out.exists()
        assert (out / ".tmp").exists()

    def test_ensure_dirs_idempotent(self, tmp_path: Path) -> None:
        out = tmp_path / "output"
        cfg = AgnesConfig(output_dir=str(out))
        cfg.ensure_dirs()
        cfg.ensure_dirs()  # second call should not error
        assert out.exists()

    def test_constructor_does_not_read_env(self, monkeypatch) -> None:
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.delenv("AGNES_API_TOKEN", raising=False)
        monkeypatch.delenv("APIHUB_AGNES_API_KEY", raising=False)
        cfg = AgnesConfig()
        assert cfg.api_key == ""


class TestAgnesConfigFromEnv:
    def test_from_env_reads_api_key(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_API_KEY", "env-key-123")
        cfg = AgnesConfig.from_env()
        assert cfg.api_key == "env-key-123"

    def test_from_env_reads_agnes_api_token(self, monkeypatch) -> None:
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.setenv("AGNES_API_TOKEN", "token-456")
        cfg = AgnesConfig.from_env()
        assert cfg.api_key == "token-456"

    def test_from_env_reads_apihub_key(self, monkeypatch) -> None:
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.delenv("AGNES_API_TOKEN", raising=False)
        monkeypatch.setenv("APIHUB_AGNES_API_KEY", "hub-key-789")
        cfg = AgnesConfig.from_env()
        assert cfg.api_key == "hub-key-789"

    def test_from_env_image_size(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_IMAGE_SIZE", "4K")
        cfg = AgnesConfig.from_env()
        assert cfg.image_size == "4K"

    def test_from_env_image_ratio(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_IMAGE_RATIO", "9:16")
        cfg = AgnesConfig.from_env()
        assert cfg.image_ratio == "9:16"

    def test_from_env_video_dimensions(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_VIDEO_WIDTH", "1920")
        monkeypatch.setenv("AGNES_VIDEO_HEIGHT", "1080")
        cfg = AgnesConfig.from_env()
        assert cfg.video_width == 1920
        assert cfg.video_height == 1080

    def test_from_env_output_dir(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_OUTPUT_DIR", "/custom/path")
        cfg = AgnesConfig.from_env()
        assert cfg.output_dir == "/custom/path"

    def test_from_env_translate_disabled(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_TRANSLATE", "0")
        cfg = AgnesConfig.from_env()
        assert not cfg.translate_prompts

    def test_from_env_audio_disabled(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_AUDIO", "0")
        cfg = AgnesConfig.from_env()
        assert not cfg.add_audio

    def test_from_env_subtitles_disabled(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_SUBTITLES", "0")
        cfg = AgnesConfig.from_env()
        assert not cfg.add_subtitles

    def test_from_env_bgm_path(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_BGM_PATH", "/music/bgm.mp3")
        cfg = AgnesConfig.from_env()
        assert cfg.bgm_path == "/music/bgm.mp3"

    def test_from_env_tts_voice(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_TTS_VOICE", "zh-CN-YunxiNeural")
        cfg = AgnesConfig.from_env()
        assert cfg.tts_voice == "zh-CN-YunxiNeural"

    def test_from_env_subtitle_custom(self, monkeypatch) -> None:
        monkeypatch.setenv("AGNES_SUBTITLE_SIZE", "36")
        monkeypatch.setenv("AGNES_SUBTITLE_COLOR", "yellow")
        monkeypatch.setenv("AGNES_SUBTITLE_POSITION", "top")
        cfg = AgnesConfig.from_env()
        assert cfg.subtitle_size == 36
        assert cfg.subtitle_color == "yellow"
        assert cfg.subtitle_position == "top"

    def test_from_env_no_api_key_still_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.delenv("AGNES_API_TOKEN", raising=False)
        monkeypatch.delenv("APIHUB_AGNES_API_KEY", raising=False)
        cfg = AgnesConfig.from_env()
        assert cfg.api_key == ""

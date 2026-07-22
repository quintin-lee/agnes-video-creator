"""Configuration — API keys, defaults, and paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgnesConfig:
    """Central configuration for the Agnes video creator."""

    # ── API ──────────────────────────────────────────────────────────
    api_key: str = ""
    base_url: str = "https://apihub.agnes-ai.com"

    text_model: str = "agnes-2.0-flash"
    image_model: str = "agnes-image-2.1-flash"
    video_model: str = "agnes-video-v2.0"

    # ── Default generation parameters ────────────────────────────────
    text_max_tokens: int = 4096
    text_temperature: float = 0.3

    image_size: str = "2K"
    image_ratio: str = "16:9"

    video_width: int = 1152
    video_height: int = 768
    video_num_frames: int = 121  # ~5 s @ 24 fps
    video_frame_rate: float = 24
    video_num_inference_steps: int | None = None
    video_seed: int | None = None
    video_negative_prompt: str = ""

    # ── Retry & timeout ──────────────────────────────────────────────
    request_retries: int = 5  # max retries for failed API calls
    request_base_delay: float = 4.0  # initial backoff seconds (doubles each retry)
    request_max_delay: float = 120.0  # cap on backoff per retry
    request_timeout: int = 3600  # seconds per HTTP request (text/image/video creation)
    poll_interval: float = 10.0  # seconds between poll requests
    poll_timeout: float = 1800.0  # max seconds to wait for video completion

    # ── Output ───────────────────────────────────────────────────────
    output_dir: str = "agnes_video_output"
    temp_dir: str = ""

    # ── Assembly ─────────────────────────────────────────────────────
    transition: str = "fade"  # fade / dissolve / wipe / slide / random / none
    transition_duration: float = 0.5  # seconds
    target_fps: int = 24
    add_audio: bool = True
    add_subtitles: bool = True
    audio_lang: str = "zh"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    subtitle_font: str = ""  # system font path for subtitles (empty = auto-detect CJK)
    subtitle_size: int = 28  # font size for burned-in subtitles
    subtitle_color: str = "white"  # font color name or hex for subtitles
    subtitle_position: str = "bottom"  # bottom / top / middle
    save_subtitle_files: bool = True  # save standalone .srt sidecar alongside video
    subtitle_languages: str = ""  # comma-separated: "en,ja,ko" — translates subtitles via API
    bgm_path: str = ""  # path to background music file (empty = no BGM)
    bgm_volume: float = 0.08  # BGM gain relative to narration (0.08 ≈ -22dB)
    bgm_fade_in: float = 2.0  # fade-in duration for BGM (seconds)
    bgm_fade_out: float = 3.0  # fade-out duration for BGM (seconds)
    bgm_ducking: bool = True  # sidechain-compress BGM when narration plays
    bgm_duck_threshold: float = -25.0  # dB threshold for ducking
    title_card: bool = True  # add opening title card
    end_credits: bool = True  # add end credits card
    add_metadata: bool = True  # embed generation metadata in output file
    add_chapters: bool = True  # add YouTube-compatible chapter markers
    add_thumbnail: bool = True  # extract thumbnail image from final video
    sfx_dir: str = ""  # path to directory with scene SFX audio files (empty = no SFX)
    auto_sfx: bool = True  # auto-fill scene SFX via NLP keyword matching + LLM fallback
    cache_enabled: bool = True  # enable content-addressed generation cache
    cache_dir: str = ""  # override cache directory (default: ~/.agnes-video/cache)
    watermark_path: str = ""  # path to watermark/logo overlay image (empty = no watermark)
    watermark_position: str = "bottom-right"  # top-left / top-right / bottom-left / bottom-right
    watermark_opacity: float = 0.7  # 0.0 (transparent) to 1.0 (opaque)
    watermark_scale: float = 0.1  # scale relative to video width (0.1 = 10%)
    post_super_res: int = 1  # super-resolution scale factor (1=off, 2=2x upscale)
    post_interpolate: int = 0  # frame interpolation target fps (0=off, e.g. 60)
    post_stabilize: bool = False  # enable video stabilisation
    post_color_grade: str = ""  # ffmpeg colorbalance preset string (empty=off)

    # ── Reference video analysis ──────────────────────────────────────
    ref_num_frames: int = 3  # frames to extract for style analysis
    ref_frame_size: str = "512:-1"  # ffmpeg scale for extracted frames

    # ── Non-English prompt behaviour ──────────────────────────────────
    translate_prompts: bool = True

    def __post_init__(self) -> None:
        # Resolve API key from environment if not provided
        if not self.api_key:
            for name in ("AGNES_API_KEY", "AGNES_API_TOKEN", "APIHUB_AGNES_API_KEY"):
                val = os.environ.get(name)
                if val:
                    self.api_key = val
                    break
        # Default temp_dir alongside output_dir
        if not self.temp_dir:
            self.temp_dir = os.path.join(self.output_dir, ".tmp")

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def resolved_output(self) -> Path:
        return Path(self.output_dir).resolve()

    @property
    def resolved_temp(self) -> Path:
        return Path(self.temp_dir).resolve()

    @property
    def resolved_cache(self) -> Path:
        override = self.cache_dir
        if override:
            return Path(override).resolve()
        return Path.home() / ".agnes-video" / "cache"

    def ensure_dirs(self) -> None:
        self.resolved_output.mkdir(parents=True, exist_ok=True)
        self.resolved_temp.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> AgnesConfig:
        """Build config from environment variables with sensible defaults."""
        return cls(
            image_size=os.environ.get("AGNES_IMAGE_SIZE", "2K"),
            image_ratio=os.environ.get("AGNES_IMAGE_RATIO", "16:9"),
            video_width=int(os.environ.get("AGNES_VIDEO_WIDTH", "1152")),
            video_height=int(os.environ.get("AGNES_VIDEO_HEIGHT", "768")),
            output_dir=os.environ.get("AGNES_OUTPUT_DIR", "agnes_video_output"),
            translate_prompts=os.environ.get("AGNES_TRANSLATE", "1") != "0",
            add_audio=os.environ.get("AGNES_AUDIO", "1") != "0",
            add_subtitles=os.environ.get("AGNES_SUBTITLES", "1") != "0",
            bgm_path=os.environ.get("AGNES_BGM_PATH", ""),
            bgm_ducking=os.environ.get("AGNES_BGM_DUCKING", "1") != "0",
            bgm_duck_threshold=float(os.environ.get("AGNES_BGM_DUCK_THRESHOLD", "-25.0")),
            audio_lang=os.environ.get("AGNES_AUDIO_LANG", "zh"),
            tts_voice=os.environ.get("AGNES_TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            subtitle_font=os.environ.get("AGNES_SUBTITLE_FONT", ""),
            subtitle_size=int(os.environ.get("AGNES_SUBTITLE_SIZE", "28")),
            subtitle_color=os.environ.get("AGNES_SUBTITLE_COLOR", "white"),
            subtitle_position=os.environ.get("AGNES_SUBTITLE_POSITION", "bottom"),
            save_subtitle_files=os.environ.get("AGNES_SAVE_SUBS", "1") != "0",
            subtitle_languages=os.environ.get("AGNES_SUB_LANGS", ""),
            add_metadata=os.environ.get("AGNES_METADATA", "1") != "0",
            add_chapters=os.environ.get("AGNES_CHAPTERS", "1") != "0",
            add_thumbnail=os.environ.get("AGNES_THUMBNAIL", "1") != "0",
            sfx_dir=os.environ.get("AGNES_SFX_DIR", ""),
            auto_sfx=os.environ.get("AGNES_AUTO_SFX", "1") != "0",
            cache_enabled=os.environ.get("AGNES_CACHE", "1") != "0",
            cache_dir=os.environ.get("AGNES_CACHE_DIR", ""),
            watermark_path=os.environ.get("AGNES_WATERMARK_PATH", ""),
            watermark_position=os.environ.get("AGNES_WATERMARK_POS", "bottom-right"),
            watermark_opacity=float(os.environ.get("AGNES_WATERMARK_OPACITY", "0.7")),
            watermark_scale=float(os.environ.get("AGNES_WATERMARK_SCALE", "0.1")),
            post_super_res=int(os.environ.get("AGNES_POST_SUPER_RES", "1")),
            post_interpolate=int(os.environ.get("AGNES_POST_INTERPOLATE", "0")),
            post_stabilize=os.environ.get("AGNES_POST_STABILIZE", "0") != "0",
            post_color_grade=os.environ.get("AGNES_POST_COLOR_GRADE", ""),
        )

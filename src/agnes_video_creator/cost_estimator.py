"""Pipeline cost and time estimator for Agnes AI API calls."""

from __future__ import annotations

from dataclasses import dataclass

# ── Per-unit pricing (USD, approximate) ─────────────────────────────
# These are rough estimates and may change. Update them to match
# current Agnes AI pricing.
PRICE_PER_IMAGE: float = 0.04  # agnes-image-2.1-flash
PRICE_PER_VIDEO_CLIP: float = 0.10  # agnes-video-v2.0
PRICE_PER_TEXT_CALL: float = 0.002  # agnes-2.0-flash (~15K tokens)

# ── Per-unit timing (seconds, approximate) ─────────────────────────
TIME_PER_NOVEL_ANALYSIS: float = 30.0  # analyzing a novel for episode splitting
TIME_PER_SCRIPT: float = 20.0  # generating a script
TIME_PER_IMAGE: float = 25.0  # generating one image
TIME_PER_VIDEO_CLIP: float = 90.0  # generating one video clip (with polling)
TIME_PER_ASSEMBLY: float = 30.0  # ffmpeg assembly for one episode


@dataclass
class CostEstimate:
    """Estimated cost and time for a pipeline run."""

    images: int = 0
    video_clips: int = 0
    text_calls: int = 0

    cost_images: float = 0.0
    cost_videos: float = 0.0
    cost_text: float = 0.0

    time_images: float = 0.0
    time_videos: float = 0.0
    time_text: float = 0.0
    time_assembly: float = 0.0

    def __post_init__(self) -> None:
        self.cost_images = self.images * PRICE_PER_IMAGE
        self.cost_videos = self.video_clips * PRICE_PER_VIDEO_CLIP
        self.cost_text = self.text_calls * PRICE_PER_TEXT_CALL
        self.time_images = self.images * TIME_PER_IMAGE
        self.time_videos = self.video_clips * TIME_PER_VIDEO_CLIP
        self.time_text = self.text_calls * TIME_PER_SCRIPT
        self.time_assembly = TIME_PER_ASSEMBLY if (self.images or self.video_clips) else 0.0

    @property
    def total_cost(self) -> float:
        return self.cost_images + self.cost_videos + self.cost_text

    @property
    def total_time(self) -> float:
        return self.time_images + self.time_videos + self.time_text + self.time_assembly

    def format_summary(self) -> str:
        """Return a human-readable summary string."""
        parts = []
        if self.images:
            parts.append(
                f"{self.images} image(s) ~ ${self.cost_images:.2f} ~ {self.time_images:.0f}s"
            )
        if self.video_clips:
            parts.append(
                f"{self.video_clips} clip(s) ~ ${self.cost_videos:.2f} ~ {self.time_videos:.0f}s"
            )
        if self.text_calls:
            parts.append(
                f"{self.text_calls} call(s) ~ ${self.cost_text:.2f} ~ {self.time_text:.0f}s"
            )
        lines = "\n      ".join(parts) if parts else "  No operations to estimate."
        return (
            f"  Estimated cost: ${self.total_cost:.2f} USD\n"
            f"  Estimated time: ~{self._format_duration(self.total_time)}\n"
            f"      {lines}"
        )

    def to_dict(self) -> dict:
        return {
            "images": self.images,
            "video_clips": self.video_clips,
            "text_calls": self.text_calls,
            "cost_images": round(self.cost_images, 2),
            "cost_videos": round(self.cost_videos, 2),
            "cost_text": round(self.cost_text, 2),
            "total_cost": round(self.total_cost, 2),
            "total_time_seconds": self.total_time,
        }

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        if seconds < 3600:
            return f"{seconds / 60:.1f} min"
        return f"{seconds / 3600:.1f} hrs"


def estimate_episode(
    scene_count: int, include_images: bool = True, include_video: bool = True
) -> CostEstimate:
    """Estimate cost/time for a single episode based on scene count."""
    return CostEstimate(
        images=scene_count if include_images else 0,
        video_clips=scene_count if include_video else 0,
        text_calls=1,
    )


def estimate_project(
    total_scenes: int, episodes: int = 1, include_images: bool = True, include_video: bool = True
) -> CostEstimate:
    """Estimate cost/time for a full project."""
    return CostEstimate(
        images=total_scenes if include_images else 0,
        video_clips=total_scenes if include_video else 0,
        text_calls=episodes,  # one text call per episode for script gen
    )

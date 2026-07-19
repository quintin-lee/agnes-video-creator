"""Data models for the video creation pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Scene:
    """A single scene in the video storyboard."""

    id: int
    narration: str
    visual_prompt: str
    duration_seconds: float = 5.0
    camera: str = "static"
    style: str = "cinematic"
    image_url: str = ""
    image_path: str = ""
    video_url: str = ""
    video_path: str = ""

    @property
    def is_image_ready(self) -> bool:
        return bool(self.image_url or (self.image_path and Path(self.image_path).exists()))

    @property
    def is_video_ready(self) -> bool:
        return bool(self.video_url or (self.video_path and Path(self.video_path).exists()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Scene:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Script:
    """A complete video script / storyboard."""

    title: str
    description: str
    total_duration: float
    scenes: list[Scene] = field(default_factory=list)
    style_guide: str = ""
    mood: str = ""
    target_audience: str = ""
    output_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "total_duration": self.total_duration,
            "scenes": [s.to_dict() for s in self.scenes],
            "style_guide": self.style_guide,
            "mood": self.mood,
            "target_audience": self.target_audience,
            "output_dir": self.output_dir,
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> Script:
        data = json.loads(Path(path).read_text())
        scenes = [Scene.from_dict(s) for s in data.pop("scenes", [])]
        return cls(scenes=scenes, **data)

    @staticmethod
    def generate_system_prompt() -> str:
        return """You are a professional short-video scriptwriter. Given a topic, produce a detailed storyboard.

Output **only** valid JSON with this exact structure — no markdown fences, no commentary:

{
  "title": "Video title",
  "description": "One-sentence summary",
  "total_duration": 15.0,
  "style_guide": "Visual style guide for the whole video",
  "mood": "Overall mood/tone",
  "target_audience": "Who this is for",
  "scenes": [
    {
      "id": 1,
      "narration": "Voice-over text for this scene, 1-2 sentences",
      "visual_prompt": "Detailed image/video generation prompt: subject, action, environment, lighting, camera, style, quality",
      "duration_seconds": 5.0,
      "camera": "Camera movement — static, slow pan left, tracking shot, push-in, pull-out, etc.",
      "style": "Visual style consistent with style_guide"
    }
  ]
}

Rules:
- Total video should be 15-60 seconds across all scenes.
- Each scene 3-10 seconds. Shorter scenes for fast cuts, longer for establishing shots.
- visual_prompt must be a detailed English prompt suitable for image-to-video generation (subject, action, environment, lighting, camera motion, style).
- narration should be concise — 1-2 sentences per scene.
- The JSON must be parseable as-is with json.loads()."""

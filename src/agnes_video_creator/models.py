"""Data models for the video creation pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FaceFeatures:
    """Structured facial features extracted from a character portrait.

    Used for prompt-level face locking: instead of vague "appearance"
    descriptions, these concrete features are injected into every scene
    prompt so the model produces consistent faces.
    """

    face_shape: str = ""  # oval, round, square, heart, diamond, long
    eye_shape: str = ""  # round, almond, hooded, monolid, downturned
    eye_color: str = ""  # dark brown, light brown, blue, green, grey, hazel
    eyebrow: str = ""  # straight, arched, thick, thin, bushy
    nose: str = ""  # straight, aquiline, button, wide, narrow, pointed
    mouth_lips: str = ""  # full, thin, wide, small, cupid bow
    jaw_chin: str = ""  # strong jaw, pointed chin, round chin, soft jaw
    skin_tone: str = ""  # fair, light, medium, tan, olive, brown, dark
    skin_texture: str = ""  # smooth, freckled, weathered, clear
    hair_style: str = ""  # short, long, curly, straight, wavy, ponytail, bun, bald
    hair_color: str = ""  # black, brown, blonde, red, grey, white, dyed
    age_range: str = ""  # child, teen, young adult, middle-aged, elderly
    gender_presentation: str = ""  # masculine, feminine, androgynous
    distinctive_features: list[str] = field(default_factory=list)
    # e.g. ["scar on left cheek", "mole above lip", "glasses"]

    def to_prompt_snippet(self) -> str:
        """Convert to a concrete face description for prompt injection.

        The result is a dense English phrase that the image/video model
        can follow precisely, e.g.:
          "Face: oval, almond eyes dark brown, straight nose, full lips,
           strong jaw. Skin: medium, smooth. Hair: long black straight.
           Age: young adult."
        """
        parts = []
        face_desc = ", ".join(
            filter(None, [
                self.face_shape,
                f"{self.eye_shape} eyes {self.eye_color}" if self.eye_shape else "",
                f"{self.eyebrow} eyebrows" if self.eyebrow else "",
                f"{self.nose} nose" if self.nose else "",
                f"{self.mouth_lips} lips" if self.mouth_lips else "",
                f"{self.jaw_chin}" if self.jaw_chin else "",
            ])
        )
        if face_desc:
            parts.append(f"Face: {face_desc}")

        skin_desc = ", ".join(
            filter(None, [self.skin_tone, self.skin_texture])
        )
        if skin_desc:
            parts.append(f"S: {skin_desc}")

        hair_desc = ", ".join(
            filter(None, [self.hair_style, self.hair_color])
        )
        if hair_desc:
            parts.append(f"Hair: {hair_desc}")

        if self.age_range:
            parts.append(f"Age: {self.age_range}")

        if self.gender_presentation:
            parts.append(f"Gender: {self.gender_presentation}")

        if self.distinctive_features:
            parts.append("Features: " + "; ".join(self.distinctive_features))

        return ". ".join(parts)

    def is_populated(self) -> bool:
        """True if at least one meaningful feature was extracted."""
        return bool(self.face_shape or self.eye_shape or self.skin_tone or self.hair_style)


@dataclass
class Character:
    """A character in the video story."""

    name: str
    appearance: str = ""  # visual description (injected into visual_prompts)
    voice: str = ""  # edge-tts voice name, empty = use default
    role: str = ""  # e.g. "主角", "反派", "配角"
    portrait_path: str = ""  # local path to reference portrait image
    portrait_url: str = ""  # URL of generated portrait
    seed: int = 0  # consistent seed for image generation (0 = random)
    face_features: FaceFeatures | None = None  # face locking data


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
    character_appearances: list[str] = field(default_factory=list)
    dialogues: list[dict] = field(default_factory=list)
    # Each dialogue: {"character": "name", "line": "spoken text"}
    sfx: str = ""
    # Sound effect description for this scene, e.g. "wind howling, footsteps on gravel"

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
    characters: list[Character] = field(default_factory=list)
    episode: int = 0  # episode number for novel-based workflows
    visual_updates: dict[str, str] = field(default_factory=dict)
    # Continuity updates reported by the LLM after generating this script:
    # {"new_environment": "描述", "outfit:林黛玉": "新服装描述", "prop:尚方宝剑": "描述"}

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
            "characters": [asdict(c) for c in self.characters],
            "episode": self.episode,
            "visual_updates": dict(self.visual_updates),
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> Script:
        data = json.loads(Path(path).read_text())
        scenes = [Scene.from_dict(s) for s in data.pop("scenes", [])]
        chars = [Character(**c) for c in data.pop("characters", [])]
        return cls(scenes=scenes, characters=chars, **data)

    def inject_characters(
        self, visual_prompt: str, scene_characters: list[str]
    ) -> str:
        """Prepend character appearance descriptions to a visual_prompt."""
        if not scene_characters:
            return visual_prompt
        char_map = {c.name: c.appearance for c in self.characters if c.appearance}
        descs = [
            f"{name}: {char_map[name]}"
            for name in scene_characters
            if name in char_map
        ]
        if not descs:
            return visual_prompt
        return f"Characters: {'; '.join(descs)}. {visual_prompt}"

    @staticmethod
    def generate_system_prompt(
        character_info: str = "",
        need_continuity_updates: bool = False,
    ) -> str:
        """Generate the system prompt for script generation.

        Parameters
        ----------
        character_info : str
            If non-empty, injected into the prompt so the model generates
            character-aware scenes with character_appearances per scene.
        need_continuity_updates : bool
            If True, adds a visual_updates field so the model reports
            cross-episode continuity changes.
        """
        char_section = ""
        dialogue_field = ""
        narration_hint = ""
        continuity_field = ""
        continuity_rule = ""
        if character_info:
            char_section = f"""
Known characters:
{character_info}

For each scene, include a "character_appearances" field listing which characters appear."""
            dialogue_field = """
      "dialogues": [
        {"character": "角色名", "line": "spoken dialogue text in Chinese"},
        {"character": "另一个角色", "line": "their reply in Chinese"}
      ],"""
            narration_hint = ", or leave empty if dialogues cover it"
        if need_continuity_updates:
            continuity_field = """
  "visual_updates": {
    "key": "value"
  },
  /* Report visual changes from this episode as key-value pairs:
       "environment:name" -> "detailed visual description of new environment"
       "outfit:林黛玉"    -> "new outfit description for character"
       "prop:name"       -> "prop description"
     Only include items that are NEW or CHANGED this episode.
     Omit this field if nothing changed. */"""
            continuity_rule = """
- When the user provides "Previous episode continuity", respect the existing visual registry and character states. Only describe NEW environments, changed outfits, or newly introduced props in "visual_updates". Reuse existing environment/prop/outfit descriptions in scene prompts instead of inventing new ones.
"""
        return f"""You are a professional short-video scriptwriter. Given a topic, produce a detailed storyboard.

Output **only** valid JSON with this exact structure — no markdown fences, no commentary:

{{
  "title": "Video title (in Chinese)",
  "description": "One-sentence summary (in Chinese)",
  "total_duration": 15.0,
  "style_guide": "Visual style guide (in Chinese)",
  "mood": "Overall mood/tone (in Chinese)",
  "target_audience": "Who this is for (in Chinese)",{char_section}{continuity_field}
  "scenes": [
    {{
      "id": 1,
      "narration": "Voice-over text in Chinese, 1-2 sentences{narration_hint}",
      "visual_prompt": "Detailed English image/video generation prompt: subject, action, environment, lighting, camera, style, quality",{dialogue_field}
      "duration_seconds": 5.0,
      "camera": "Camera movement (in Chinese)",
      "style": "Visual style (in Chinese)"
    }}
  ]
}}

Rules:
- Total video should be 15-60 seconds across all scenes.
- Each scene 3-10 seconds. Shorter scenes for fast cuts, longer for establishing shots.
- **narration** MUST be in Chinese — 1-2 sentences per scene. If characters have dialogues, narration provides context.
- **title, description, style_guide, mood, target_audience, camera** MUST be in Chinese.
- **camera** field describes camera motion per scene, e.g. "缓慢推近" (slow zoom in), "向右平移" (pan right), "跟拍" (tracking), "手持晃动" (handheld), "航拍俯视" (aerial), "特写推近" (close-up dolly). Be specific.
- **visual_prompt** MUST be a detailed English prompt suitable for image-to-video generation (subject, action, environment, lighting, camera motion, style). When characters appear, describe them in the visual_prompt as directed by their appearance.
- When characters are provided, include **dialogues** for character interactions — each line spoken by a character in Chinese.
- The JSON must be parseable as-is with json.loads().{continuity_rule}"""

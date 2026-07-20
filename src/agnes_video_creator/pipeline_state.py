"""Pipeline state persistence — track generation progress across steps for resume support.

Each project (or single-script run) saves a pipeline_state.json alongside
the output directory.  After each step the state is updated so that
interrupted runs can resume without re-doing completed work.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


SceneStatus = Literal["pending", "success", "failed", "skipped"]
EpisodeStatus = Literal[
    "pending", "analyzing", "script_ready",
    "images_ready", "videos_ready", "assembled", "failed",
]


@dataclass
class SceneState:
    """Per-scene generation state within an episode."""

    scene_id: int
    image: SceneStatus = "pending"
    video: SceneStatus = "pending"
    image_url: str = ""
    video_url: str = ""
    error: str = ""


@dataclass
class EpisodeState:
    """Generation state for one episode."""

    episode_number: int
    status: EpisodeStatus = "pending"
    script_path: str = ""
    scenes: list[SceneState] = field(default_factory=list)
    continuity: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def has_script(self) -> bool:
        return bool(self.script_path) and Path(self.script_path).exists()

    @property
    def images_completed(self) -> int:
        return sum(1 for s in self.scenes if s.image in ("success", "skipped"))

    @property
    def videos_completed(self) -> int:
        return sum(1 for s in self.scenes if s.video in ("success", "skipped"))

    @property
    def all_images_done(self) -> bool:
        return bool(self.scenes) and all(
            s.image in ("success", "skipped") for s in self.scenes
        )

    @property
    def all_videos_done(self) -> bool:
        return bool(self.scenes) and all(
            s.video in ("success", "skipped") for s in self.scenes
        )


@dataclass
class PipelineState:
    """Full pipeline state for a single-script or multi-episode project."""

    project_name: str = ""
    output_dir: str = ""
    episodes: list[EpisodeState] = field(default_factory=list)
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    # ── Save / load ────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        path = Path(path)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        )

    @classmethod
    def load(cls, path: str | Path) -> PipelineState | None:
        path = Path(path)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "output_dir": self.output_dir,
            "episodes": [asdict(e) for e in self.episodes],
            "config_snapshot": dict(self.config_snapshot),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PipelineState:
        episodes = []
        for e in d.get("episodes", []):
            scenes = [SceneState(**s) for s in e.pop("scenes", [])]
            episodes.append(EpisodeState(scenes=scenes, **e))
        return cls(
            project_name=d.get("project_name", ""),
            output_dir=d.get("output_dir", ""),
            episodes=episodes,
            config_snapshot=d.get("config_snapshot", {}),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    # ── Helpers ────────────────────────────────────────────────────

    @classmethod
    def fresh(
        cls,
        project_name: str = "",
        output_dir: str = "",
        num_episodes: int = 0,
    ) -> PipelineState:
        """Create a fresh pipeline state for *num_episodes* episodes."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            project_name=project_name,
            output_dir=output_dir,
            episodes=[
                EpisodeState(episode_number=i + 1)
                for i in range(num_episodes)
            ],
            created_at=now,
            updated_at=now,
        )

    def episode(self, number: int) -> EpisodeState | None:
        for e in self.episodes:
            if e.episode_number == number:
                return e
        return None

    def upsert_episode(self, state: EpisodeState) -> None:
        for i, e in enumerate(self.episodes):
            if e.episode_number == state.episode_number:
                self.episodes[i] = state
                return
        self.episodes.append(state)

    def mark_episode_failed(self, number: int, error: str) -> None:
        ep = self.episode(number)
        if ep is not None:
            ep.status = "failed"
            ep.error = error

    def summary(self) -> str:
        lines: list[str] = []
        for ep in self.episodes:
            im = ep.images_completed
            vm = ep.videos_completed
            total = len(ep.scenes)
            lines.append(
                f"  Episode {ep.episode_number}: {ep.status} "
                f"({im}/{total} images, {vm}/{total} videos)"
                + (f" — {ep.error}" if ep.error else "")
            )
        return "\n".join(lines)

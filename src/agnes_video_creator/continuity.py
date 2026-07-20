"""Cross-episode continuity state for novel-to-short-drama pipeline.

Tracks character states, visual registry (environments/props/outfits),
and plot threads across episodes so each episode builds on the previous one
rather than starting fresh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CharacterContinuity:
    """Per-character state carried between episodes.

    Updated after each episode by the LLM based on what happened.
    """

    outfit: str = ""
    location: str = ""
    emotional_state: str = ""
    notes: str = ""  # free-form narrative notes ("injured right arm", "angry at B")

    def is_populated(self) -> bool:
        return bool(self.outfit or self.location or self.emotional_state or self.notes)

    def to_prompt_snippet(self) -> str:
        parts = []
        if self.outfit:
            parts.append(f"  当前服装: {self.outfit}")
        if self.location:
            parts.append(f"  当前位置: {self.location}")
        if self.emotional_state:
            parts.append(f"  情绪状态: {self.emotional_state}")
        if self.notes:
            parts.append(f"  备注: {self.notes}")
        return "\n".join(parts)


@dataclass
class VisualRegistry:
    """Reusable visual descriptions tracked across episodes.

    Each entry is a (name → detailed visual description) mapping that
    scene prompts should reference for consistency.
    """

    environments: dict[str, str] = field(default_factory=dict)
    props: dict[str, str] = field(default_factory=dict)
    outfits: dict[str, str] = field(default_factory=dict)

    def to_prompt_snippet(self) -> str:
        lines: list[str] = []
        if self.environments:
            lines.append("  场景场所:")
            for name, desc in self.environments.items():
                lines.append(f"    - {name}: {desc}")
        if self.props:
            lines.append("  道具物品:")
            for name, desc in self.props.items():
                lines.append(f"    - {name}: {desc}")
        if self.outfits:
            lines.append("  角色服装:")
            for name, desc in self.outfits.items():
                lines.append(f"    - {name}: {desc}")
        return "\n".join(lines)

    def is_populated(self) -> bool:
        return bool(self.environments or self.props or self.outfits)


@dataclass
class ContinuityState:
    """Full cross-episode continuity context.

    Created at the start of the novel pipeline and updated after each
    episode.  Injected into the script generation prompt for the next
    episode so the LLM knows what happened and how things look.
    """

    episode: int = 0
    characters: dict[str, CharacterContinuity] = field(default_factory=dict)
    visual: VisualRegistry = field(default_factory=VisualRegistry)
    plot_threads: list[str] = field(default_factory=list)
    prev_summary: str = ""

    def ensure_character(self, name: str) -> CharacterContinuity:
        if name not in self.characters:
            self.characters[name] = CharacterContinuity()
        return self.characters[name]

    def to_prompt_snippet(self) -> str:
        """Format the full continuity state for injection into a script-generation prompt."""
        lines: list[str] = []
        if self.prev_summary:
            lines.append(f"前情提要:\n{self.prev_summary}")
        if self.plot_threads:
            lines.append("当前活跃剧情线索:")
            for t in self.plot_threads:
                lines.append(f"  - {t}")
        if self.visual.is_populated():
            lines.append("已知视觉参考 (新场景中请复用这些描述):")
            lines.append(self.visual.to_prompt_snippet())
        char_entries = [(n, c) for n, c in self.characters.items() if c.is_populated()]
        if char_entries:
            lines.append("角色当前状态:")
            for name, cc in char_entries:
                snippet = cc.to_prompt_snippet()
                if snippet:
                    lines.append(f"  {name}:")
                    lines.append(snippet)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode,
            "characters": {k: v.__dict__ for k, v in self.characters.items()},
            "visual": {
                "environments": dict(self.visual.environments),
                "props": dict(self.visual.props),
                "outfits": dict(self.visual.outfits),
            },
            "plot_threads": list(self.plot_threads),
            "prev_summary": self.prev_summary,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContinuityState:
        obj = cls(
            episode=d.get("episode", 0),
            plot_threads=list(d.get("plot_threads", [])),
            prev_summary=d.get("prev_summary", ""),
        )
        for name, cc_data in d.get("characters", {}).items():
            obj.characters[name] = CharacterContinuity(**cc_data)
        vis = d.get("visual", {})
        obj.visual.environments = dict(vis.get("environments", {}))
        obj.visual.props = dict(vis.get("props", {}))
        obj.visual.outfits = dict(vis.get("outfits", {}))
        return obj

    def apply_visual_updates(self, updates: dict[str, str]) -> None:
        """Apply Script.visual_updates to this continuity state."""
        for key, value in updates.items():
            if key == "summary":
                self.prev_summary = value
            elif key.startswith("environment:"):
                self.visual.environments[key[len("environment:"):]] = value
            elif key.startswith("env:"):
                self.visual.environments[key[len("env:"):]] = value
            elif key.startswith("outfit:"):
                self.visual.outfits[key[len("outfit:"):]] = value
            elif key.startswith("prop:"):
                self.visual.props[key[len("prop:"):]] = value
            elif key.startswith("plot:"):
                thread = key[len("plot:"):]
                if value and thread not in self.plot_threads:
                    self.plot_threads.append(f"{thread}: {value}")
            elif key.startswith("emotion:"):
                char_name = key[len("emotion:"):]
                self.ensure_character(char_name).emotional_state = value
            elif key.startswith("location:"):
                char_name = key[len("location:"):]
                self.ensure_character(char_name).location = value
            elif key.startswith("notes:"):
                char_name = key[len("notes:"):]
                self.ensure_character(char_name).notes = value
            elif ":" in key:
                prefix, rest = key.split(":", 1)
                self.visual.environments[key] = value

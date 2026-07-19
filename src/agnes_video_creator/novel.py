"""Novel-to-video pipeline — import long text, extract characters, split into episodes.

Pipeline:
  1. Read novel text from file.
  2. Call Agnes 2.0 Flash to extract characters + episode breakdown.
  3. For each episode, generate a full Script via the existing script_generator.
  4. Save per-episode Script JSONs in the output directory.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Character, Script
from agnes_video_creator.script_generator import generate_script
from agnes_video_creator.utils import request_json


# ── Character + episode extraction ─────────────────────────────────────


_ANALYSIS_PROMPT = """You are a professional novel-to-screenplay analyst.

Given a novel excerpt, analyze it and output **only** valid JSON with no markdown fences:

{
  "title": "Story title (in Chinese)",
  "characters": [
    {
      "name": "角色名",
      "appearance": "Detailed visual appearance description in English for AI image generation (age, hair, clothing, distinguishing features, build)",
      "role": "主角/反派/配角/龙套",
      "voice": ""
    }
  ],
  "episodes": [
    {
      "number": 1,
      "title": "Episode title (in Chinese)",
      "summary": "What happens in this episode (in Chinese)",
      "scene_count": 3,
      "character_focus": ["角色A", "角色B"]
    }
  ],
  "remaining_text": "Any text that wasn't part of the analyzed episodes, or empty string if all covered"
}

Rules:
- Extract 3-6 key characters with rich English appearance descriptions for image generation.
- Suggest 1-4 episodes depending on content length.
- Each episode should be 3-6 scenes (~15-30 seconds of video).
- Return pure JSON — no explanation, no markdown.
"""


def analyze_novel(
    text: str,
    cfg: AgnesConfig,
    *,
    verbose: bool = True,
) -> tuple[str, list[Character], list[dict[str, Any]], str]:
    """Analyze novel text → title, characters, episode list, remaining text."""
    payload = {
        "model": cfg.text_model,
        "messages": [
            {"role": "system", "content": _ANALYSIS_PROMPT},
            {"role": "user", "content": f"Novel text:\n\n{text[:8000]}"},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    raw = request_json("POST", "/v1/chat/completions", payload, cfg=cfg)
    content = _extract(raw)
    if not content:
        raise SystemExit("Novel analysis returned empty response.")

    data = _parse_json(content)
    title = data.get("title", "Untitled")
    chars = [Character(**c) for c in data.get("characters", [])]
    episodes = data.get("episodes", [])
    remaining = data.get("remaining_text", "")

    if verbose:
        print(f"\n  Story: {title}", file=sys.stderr)
        print(f"  Characters ({len(chars)}): {', '.join(c.name for c in chars)}", file=sys.stderr)
        for ep in episodes:
            print(f"    Episode {ep['number']}: {ep.get('title', '')} "
                  f"({ep.get('scene_count', '?')} scenes)", file=sys.stderr)
        if remaining:
            print(f"  Remaining text: {len(remaining)} chars", file=sys.stderr)

    return title, chars, episodes, remaining


# ── Episode script generation ──────────────────────────────────────────


def generate_episode_script(
    title: str,
    episode: dict[str, Any],
    characters: list[Character],
    novel_text: str,
    cfg: AgnesConfig,
    *,
    verbose: bool = True,
) -> Script:
    """Generate a full Script for one episode using the existing pipeline."""
    char_info = "\n".join(
        f"- {c.name}: {c.appearance or '(no description)'} ({c.role})"
        for c in characters
    )

    # Use generate_script with character_info for consistency
    topic = (
        f"Novel: {title}\n"
        f"Episode {episode['number']}: {episode.get('title', '')}\n"
        f"Summary: {episode.get('summary', '')}\n"
        f"Characters appearing: {', '.join(episode.get('character_focus', []))}\n\n"
        f"Novel excerpt:\n{novel_text[:2000]}"
    )

    style_hint = episode.get("style_hint", "cinematic short drama, Chinese style")

    script = generate_script(
        topic,
        cfg=cfg,
        style_hint=style_hint,
        target_duration=30.0,
        character_info=char_info,
        verbose=verbose,
    )

    # Attach character data and episode number to the script
    script.title = f"{title} 第{episode['number']}集"
    script.characters = characters
    script.episode = episode["number"]

    return script


# ── Full novel pipeline ────────────────────────────────────────────────


def novel_to_episodes(
    text: str,
    cfg: AgnesConfig,
    *,
    max_episodes: int = 4,
    verbose: bool = True,
) -> list[Script]:
    """Full pipeline: analyze novel, generate one script per episode."""
    title, characters, episodes, remaining = analyze_novel(text, cfg, verbose=verbose)

    scripts: list[Script] = []
    for i, ep in enumerate(episodes[:max_episodes]):
        if verbose:
            print(f"\n--- Episode {ep['number']}: {ep.get('title', '')} ---",
                  file=sys.stderr)
        script = generate_episode_script(
            title, ep, characters, text, cfg, verbose=verbose,
        )
        scripts.append(script)

    return scripts


# ── Internal helpers ───────────────────────────────────────────────────


def _extract(data: dict[str, Any]) -> str | None:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
        elif "```" in cleaned:
            cleaned = cleaned[: cleaned.rindex("```")].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start: end + 1])
        raise

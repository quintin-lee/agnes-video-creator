"""Script generation — uses Agnes 2.0 Flash to create a structured storyboard."""

from __future__ import annotations

import json
import sys
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Scene, Script
from agnes_video_creator.utils import request_json


def generate_script(
    topic: str,
    *,
    cfg: AgnesConfig | None = None,
    style_hint: str = "",
    target_duration: float = 15.0,
    character_info: str = "",
    continuity_info: str = "",
    verbose: bool = True,
) -> Script:
    """Generate a complete video script from a topic description.

    Parameters
    ----------
    character_info : str
        If non-empty, the system prompt includes character descriptions
        and per-scene character_appearances tracking.
    continuity_info : str
        Cross-episode continuity context (character state, visual
        registry, plot threads, previous summary) injected into the
        user prompt so the LLM builds on prior episodes.
    """
    if cfg is None:
        cfg = AgnesConfig.from_env()

    if not cfg.has_api_key:
        raise SystemExit("AGNES_API_KEY not set. Export it or pass --api-key.")

    # Build the user prompt
    user_prompt = f"Topic: {topic}\nTarget duration: {target_duration} seconds\n"
    if style_hint:
        user_prompt += f"Style hint: {style_hint}\n"
    if continuity_info:
        user_prompt += f"\nPrevious episode continuity:\n{continuity_info}\n"

    if verbose:
        print(f"  Generating script for: {topic}", file=sys.stderr)
        if style_hint:
            print(f"  Style: {style_hint}", file=sys.stderr)

    payload = {
        "model": cfg.text_model,
        "messages": [
            {
                "role": "system",
                "content": Script.generate_system_prompt(
                    character_info=character_info,
                    need_continuity_updates=bool(continuity_info),
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": cfg.text_temperature,
        "max_tokens": cfg.text_max_tokens,
    }

    raw = request_json("POST", "/v1/chat/completions", payload, cfg=cfg)
    content = _extract_content(raw)
    if not content:
        _dump_failure(raw)
        raise SystemExit("Script generation returned empty content. Check API key and try again.")

    parsed = _parse_script_json(content, topic)
    parsed.output_dir = cfg.output_dir

    if verbose:
        scene_count = len(parsed.scenes)
        print(
            f"  ✓ Script generated: {parsed.title} "
            f"({scene_count} scenes, ~{parsed.total_duration}s total)",
            file=sys.stderr,
        )

    return parsed


# ── Internal helpers ───────────────────────────────────────────────────


def _extract_content(data: dict[str, Any]) -> str | None:
    """Extract message content from an OpenAI-compatible response."""
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def _dump_failure(data: dict[str, Any]) -> None:
    print(
        "Script generation: unexpected response —",
        json.dumps(data, ensure_ascii=False, indent=2),
        file=sys.stderr,
    )


def _repair_json(text: str) -> str:
    """Fix common JSON issues in LLM output so json.loads can parse it.

    Repairs:
      - Literal newlines/tabs inside JSON strings → escaped \\n, \\t
      - Otherwise preserves text structure.
    """
    chars: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if esc:
            chars.append(ch)
            esc = False
            continue
        if ch == "\\":
            chars.append(ch)
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            chars.append(ch)
            continue
        if in_str and ch == "\n":
            chars.append("\\n")
            continue
        if in_str and ch == "\r":
            chars.append("\\r")
            continue
        if in_str and ch == "\t":
            chars.append("\\t")
            continue
        chars.append(ch)
    return "".join(chars)


def _parse_script_json(raw: str, fallback_title: str) -> Script:
    """Parse the model's JSON output into a Script, with lenient extraction."""
    # Strip markdown fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Remove opening fence
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        # Remove closing fence
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
        elif "```" in cleaned:
            cleaned = cleaned[: cleaned.rindex("```")].strip()

    try:
        data: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = _repair_json(cleaned)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as exc:
            # Last resort: try to find a JSON-like block in repaired text
            brace_start = repaired.find("{")
            brace_end = repaired.rfind("}")
            if brace_start != -1 and brace_end > brace_start:
                try:
                    data = json.loads(repaired[brace_start : brace_end + 1])
                except json.JSONDecodeError:
                    raise SystemExit(
                        f"Failed to parse script JSON from model output.\n"
                        f"JSON error: {exc}\n---output---\n{raw}\n---"
                    ) from exc
            else:
                raise SystemExit(
                    f"Failed to parse script JSON from model output.\n"
                    f"JSON error: {exc}\n---output---\n{raw}\n---"
                ) from exc

    scenes_raw = data.pop("scenes", [])
    scenes = [
        Scene(
            id=s.get("id", i + 1),
            narration=s.get("narration", ""),
            visual_prompt=s.get("visual_prompt", ""),
            duration_seconds=float(s.get("duration_seconds", 5)),
            camera=s.get("camera", "static"),
            style=s.get("style", "cinematic"),
            character_appearances=s.get("character_appearances", []),
            dialogues=s.get("dialogues", []),
        )
        for i, s in enumerate(scenes_raw)
    ]

    return Script(
        title=data.get("title") or fallback_title,
        description=data.get("description", ""),
        total_duration=float(data.get("total_duration", 15)),
        scenes=scenes,
        style_guide=data.get("style_guide", ""),
        mood=data.get("mood", ""),
        target_audience=data.get("target_audience", ""),
    )

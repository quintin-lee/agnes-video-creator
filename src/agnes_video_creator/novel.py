"""Novel-to-video pipeline — import long text, extract characters, split into episodes.

Pipeline:
  1. Read novel text from file.
  2. Split into intelligently-sized chunks with summaries (chunk_novel).
  3. Call Agnes 2.0 Flash to extract characters + episode breakdown.
  4. For each episode, generate a full Script via the existing script_generator,
     using the full relevant chunk + continuity state from prior episodes.
  5. Extract dialogue lines from the chunk so the script uses novel-original lines.
  6. Save per-episode Script JSONs in the output directory.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.continuity import ContinuityState
from agnes_video_creator.models import Character, Script
from agnes_video_creator.script_generator import generate_script
from agnes_video_creator.utils import request_json


# ── Novel chunking ─────────────────────────────────────────────────────


@dataclass
class NovelChunk:
    """A segment of novel text with metadata for one episode."""

    index: int
    text: str
    title: str = ""
    summary: str = ""
    characters: list[str] = field(default_factory=list)


# Chapter heading patterns tried in order
_CHAPTER_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百千\d]+[回章节集])"
    r"|(?:Chapter\s+\d+)"
    r"|(?:CHAPTER\s+\d+)"
    r"|(?:#+\s*\S+)",
    re.MULTILINE,
)


def chunk_novel(
    text: str,
    *,
    max_chunk_len: int = 4000,
    verbose: bool = True,
) -> list[NovelChunk]:
    """Split novel text into intelligently-sized chunks for episode generation.

    Strategy:
    1. Try chapter markers first ("第X回", "Chapter X", "### heading").
    2. Merge very small adjacent chunks; split oversized ones by paragraph.
    3. Generate a title and summary for each chunk.
    """
    raw_chunks = _split_by_chapters(text)

    # Merge adjacent tiny chunks; split oversized ones
    merged: list[NovelChunk] = []
    buf = ""
    buf_idx = 0
    for i, (heading, body) in enumerate(raw_chunks):
        segment = f"{heading}\n\n{body}" if heading else body
        if buf and len(buf) + len(segment) > max_chunk_len:
            merged.append(NovelChunk(index=buf_idx, text=buf))
            buf_idx += 1
            buf = segment
        elif buf:
            buf += "\n\n" + segment
        else:
            buf = segment

        # If this segment alone is oversized, flush and split
        while len(buf) > max_chunk_len:
            merged.append(NovelChunk(index=buf_idx, text=buf[:max_chunk_len]))
            buf_idx += 1
            buf = buf[max_chunk_len:]

    if buf:
        merged.append(NovelChunk(index=buf_idx, text=buf))

    # Assign titles and summaries
    for chunk in merged:
        chunk.title = _infer_chunk_title(chunk.text, raw_chunks)
        chunk.summary = chunk.text[:200].replace("\n", " ").strip()
        if len(chunk.text) > 200:
            chunk.summary += "…"

    if verbose:
        total = sum(len(c.text) for c in merged)
        print(
            f"  Novel split into {len(merged)} chunk(s) "
            f"({total:,} total chars, ~{total // max(1, len(merged)):,} avg)",
            file=sys.stderr,
        )
        for c in merged:
            print(f"    [{c.index}] {c.title or '(untitled)'}: {len(c.text):,} chars",
                  file=sys.stderr)

    return merged


def _split_by_chapters(text: str) -> list[tuple[str, str]]:
    """Split text at chapter boundaries. Returns [(heading, body), ...]."""
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        return [("", text)]

    chunks: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        heading = m.group().strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        chunks.append((heading, body))
    return chunks


def _infer_chunk_title(text: str, raw_chunks: list[tuple[str, str]]) -> str:
    """Extract a human-readable title for a chunk."""
    # Check if this chunk starts with a chapter heading
    for heading, body in raw_chunks:
        if heading and body and body[: min(len(text), 200)] in text:
            return heading
    # Fallback: use first meaningful line
    for line in text.split("\n")[:5]:
        stripped = line.strip()
        if stripped and len(stripped) > 4 and len(stripped) < 100:
            return stripped
    return ""


# ── Dialogue extraction ────────────────────────────────────────────────

# Chinese dialogue patterns:
#   "A说："..."   "A道："..."   "A对B说："..."
# Speech verbs shared by both dialogue patterns.
_DIALOGUE_VERBS = (
    "说道|问道|答道|叫道|喊道|骂道|叹道|笑道|哭道|叹道|回道|"
    "回答道|解释道|补充道|打断道|提醒道|抢白道|自言自语道|接口道|"
    "劝道|低声道|大声道|柔声道|厉声道|沉声道|颤声道|"
    "笑说|哭着说|解说|"
    "说|道|问|答|叫|喊|骂|叹|笑|念|哭|嚷|回|喝|吟|劝"
)

# Pattern 1: 「A笑道：」"dialogue" — character name + speech verb + colon + quoted text
_DIALOGUE_RE = re.compile(
    rf'([\u4e00-\u9fff]{{2,4}}?)'
    rf'(?:{_DIALOGUE_VERBS})'
    rf'[：:][\u201c"]([^\u201c"\u201d]+)[\u201d"]'
)
# Pattern 2: 「dialogue」A笑道。 (angle-quoted speech followed by speaker + verb)
_ANGLE_DIALOGUE_RE = re.compile(
    rf'\u300c([^\u300d]+)\u300d'
    rf'([\u4e00-\u9fff]{{2,4}}?)'
    rf'(?:{_DIALOGUE_VERBS})'
)


def extract_dialogues(
    text: str,
    characters: list[Character],
) -> list[dict[str, str]]:
    """Extract dialogue lines from novel text that belong to known characters.

    Returns a list of {"character": name, "line": spoken_text} dicts
    preserving the order they appear in the source text.
    """
    char_names = {c.name for c in characters}
    results: list[dict[str, str]] = []

    for m in _DIALOGUE_RE.finditer(text):
        name = m.group(1).strip()
        line = m.group(2).strip()
        if name in char_names and line:
            results.append({"character": name, "line": line})

    for m in _ANGLE_DIALOGUE_RE.finditer(text):
        line = m.group(1).strip()
        name = m.group(2).strip()
        if name in char_names and line:
            results.append({"character": name, "line": line})

    return results


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
    continuity_state: ContinuityState | None = None,
    episode_number: int = 1,
    total_episodes: int = 1,
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
        f"Episode {episode_number} of {total_episodes}: {episode.get('title', '')}\n"
        f"Summary: {episode.get('summary', '')}\n"
        f"Characters appearing: {', '.join(episode.get('character_focus', []))}\n\n"
        f"Novel excerpt:\n{novel_text[:2000]}"
    )

    # Build continuity info for this episode
    continuity_info = ""
    if continuity_state is not None:
        continuity_state.episode = episode_number
        snippet = continuity_state.to_prompt_snippet()
        if snippet:
            continuity_info = (
                f"这是第 {episode_number}/{total_episodes} 集。"
                f"请基于下文生成本集脚本，并保持与前集的一致性。\n\n"
                f"{snippet}"
            )

    style_hint = episode.get("style_hint", "cinematic short drama, Chinese style")

    script = generate_script(
        topic,
        cfg=cfg,
        style_hint=style_hint,
        target_duration=30.0,
        character_info=char_info,
        continuity_info=continuity_info,
        verbose=verbose,
    )

    # Attach character data and episode number to the script
    script.title = f"{title} 第{episode_number}集"
    script.characters = characters
    script.episode = episode_number

    return script


# ── Full novel pipeline ────────────────────────────────────────────────


def novel_to_episodes(
    text: str,
    cfg: AgnesConfig,
    *,
    max_episodes: int = 4,
    verbose: bool = True,
) -> list[Script]:
    """Full pipeline: chunk novel, generate one script per chunk with
    cross-episode continuity tracking."""
    chunks = chunk_novel(text, verbose=verbose)
    if not chunks:
        raise SystemExit("Novel produced no chunks.")

    # Analyze the first chunk for overall story metadata
    first_chunk = chunks[0]
    title, characters, episodes, remaining = analyze_novel(
        first_chunk.text, cfg, verbose=verbose,
    )

    total = min(len(chunks), max_episodes)
    if verbose:
        print(f"\n  Generating {total} episode(s) from {len(chunks)} chunk(s) "
              f"with continuity tracking.", file=sys.stderr)

    # Initialize continuity state
    continuity = ContinuityState()

    scripts: list[Script] = []
    for i in range(total):
        chunk = chunks[i]
        ep = episodes[i] if i < len(episodes) else {
            "number": i + 1,
            "title": f"第{i+1}集",
            "summary": chunk.summary,
            "character_focus": [c.name for c in characters],
        }

        if verbose:
            print(f"\n--- Episode {i+1}: {ep.get('title', '')} ---",
                  file=sys.stderr)

        script = generate_episode_script(
            title,
            ep,
            characters,
            chunk.text,
            cfg,
            continuity_state=continuity,
            episode_number=i + 1,
            total_episodes=total,
            verbose=verbose,
        )

        # Apply visual_updates from this script into continuity state
        if script.visual_updates:
            continuity.apply_visual_updates(script.visual_updates)

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

"""Reference video analysis — extract visual style from a reference video.

Pipeline:
  1. Download reference video from URL (if needed), or use local file.
  2. Extract key frames from the reference video via ffmpeg.
  3. Encode frames as base64 data URIs (small JPEG).
  4. Send frames to Agnes 2.0 Flash for visual style analysis.
  5. Build a structured StyleProfile JSON.
  6. Generate a new script that applies the reference style to a user topic.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Script
from agnes_video_creator.utils import request_json


# ── Data model ─────────────────────────────────────────────────────────


class StyleProfile:
    """Structured description of a reference video's visual style."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.style_label: str = raw.get("style_label", "cinematic")
        self.color_palette: str = raw.get("color_palette", "balanced natural tones")
        self.lighting: str = raw.get("lighting", "natural lighting")
        self.camera_style: str = raw.get("camera_style", "static tripod shots")
        self.composition: str = raw.get("composition", "rule of thirds")
        self.mood: str = raw.get("mood", "neutral")
        self.scene_type: str = raw.get("scene_type", "general")
        self.motion_character: str = raw.get(
            "motion_character", "gentle, slow-paced"
        )
        self.key_visual_elements: str = raw.get(
            "key_visual_elements", ""
        )
        self.raw = raw

    def to_dict(self) -> dict[str, str]:
        return {
            "style_label": self.style_label,
            "color_palette": self.color_palette,
            "lighting": self.lighting,
            "camera_style": self.camera_style,
            "composition": self.composition,
            "mood": self.mood,
            "scene_type": self.scene_type,
            "motion_character": self.motion_character,
            "key_visual_elements": self.key_visual_elements,
        }

    def style_prompt_suffix(self) -> str:
        """Return a prose description to append to generation prompts."""
        return (
            f"Style: {self.style_label}. "
            f"Color palette: {self.color_palette}. "
            f"Lighting: {self.lighting}. "
            f"Camera: {self.camera_style}. "
            f"Composition: {self.composition}. "
            f"Mood: {self.mood}. "
            f"Motion: {self.motion_character}."
        )

    def __str__(self) -> str:
        lines = [
            f"Style:          {self.style_label}",
            f"Color palette:  {self.color_palette}",
            f"Lighting:       {self.lighting}",
            f"Camera:         {self.camera_style}",
            f"Composition:    {self.composition}",
            f"Mood:           {self.mood}",
            f"Scene type:     {self.scene_type}",
            f"Motion:         {self.motion_character}",
        ]
        if self.key_visual_elements:
            lines.append(f"Key elements:   {self.key_visual_elements[:120]}")
        return "\n".join(lines)


# ── URL download ──────────────────────────────────────────────────────


def _resolve_video_source(src: str, temp_dir: Path, *, verbose: bool = True) -> str:
    """Return a local path to the video.

    If *src* is an HTTP(S) URL, download it to *temp_dir* first.
    Otherwise return *src* unchanged (local file).
    """
    if not src.startswith(("http://", "https://")):
        path = Path(src)
        if not path.exists():
            raise SystemExit(f"Reference video not found: {src}")
        return str(path.resolve())

    # ── URL download ─────────────────────────────────────────────
    if verbose:
        print(f"  Downloading reference video from URL...", file=sys.stderr)

    # Determine filename from URL or fallback
    url_path = urllib.request.urlparse(src).path
    filename = os.path.basename(url_path) if url_path else "reference_video.mp4"
    if not filename or "." not in filename:
        filename = "reference_video.mp4"

    dest = temp_dir / filename
    if dest.exists():
        if verbose:
            print(f"  Already cached: {dest}", file=sys.stderr)
        return str(dest)

    try:
        def _report(block_count: int, block_size: int, total_size: int) -> None:
            if verbose and total_size > 0:
                downloaded = block_count * block_size / (1024 * 1024)
                total_mb = total_size / (1024 * 1024)
                print(
                    f"    Downloaded: {downloaded:.1f} / {total_mb:.1f} MB",
                    end="\r",
                    file=sys.stderr,
                )

        urllib.request.urlretrieve(src, str(dest), reporthook=_report)
        if verbose:
            print(file=sys.stderr)  # newline after progress
    except Exception as exc:
        raise SystemExit(f"Failed to download reference video: {exc}") from exc

    if not dest.exists() or dest.stat().st_size == 0:
        raise SystemExit(f"Downloaded file is empty or missing: {dest}")

    mb = dest.stat().st_size / (1024 * 1024)
    if verbose:
        print(f"  ✓ Saved to: {dest} ({mb:.1f} MB)", file=sys.stderr)

    return str(dest)


# ── Frame extraction ───────────────────────────────────────────────────


def extract_frames(
    video_path: str,
    output_dir: str | Path,
    *,
    num_frames: int = 3,
    size: str = "512:-1",
) -> list[Path]:
    """Extract evenly-spaced JPEG frames from a video using ffmpeg.

    Returns paths to the extracted frame files.
    """
    video = Path(video_path)
    if not video.exists():
        raise SystemExit(f"Reference video not found: {video_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Get video duration
    duration = _get_duration(video)
    if duration <= 0:
        raise SystemExit(f"Could not determine duration of {video_path}")

    # Compute frame timestamps evenly spaced across the video
    # Avoid first/last 0.5s to skip fade-in/out
    safe_duration = max(duration - 1.0, 1.0)
    interval = safe_duration / (num_frames + 1)
    timestamps = [interval * (i + 1) for i in range(num_frames)]

    extracted: list[Path] = []
    for i, ts in enumerate(timestamps):
        out_path = out_dir / f"ref_frame_{i:03d}.jpg"
        if out_path.exists():
            extracted.append(out_path)
            continue

        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(ts),
            "-i", str(video),
            "-vframes", "1",
            "-vf", f"scale={size}",
            "-q:v", "3",  # high-quality JPEG
            str(out_path),
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                f"ffmpeg frame extraction failed at {ts}s: {exc.stderr}"
            ) from exc

        extracted.append(out_path)

    return extracted


def _get_duration(video: Path) -> float:
    """Get video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0.0


def encode_frame(image_path: Path) -> str:
    """Read a JPEG image and return a base64 data URI."""
    data = image_path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


# ── Style analysis via vision API ──────────────────────────────────────


_ANALYSIS_SYSTEM_PROMPT = """You are a professional video director and cinematographer. 
Analyze the provided video frame(s) and extract the visual style.

Output **only** valid JSON — no markdown fences, no commentary:

{
  "style_label": "One or two words describing the overall style (e.g. cinematic, documentary, anime, vintage, noir, sci-fi, minimalist)",
  "color_palette": "Describe the dominant colors, contrast, saturation (e.g. warm amber and teal with high contrast, desaturated earth tones, vibrant neon on dark backgrounds)",
  "lighting": "Describe the lighting setup (e.g. natural golden hour, hard dramatic key light with deep shadows, soft diffused overhead, neon practicals)",
  "camera_style": "Describe camera movement and angle (e.g. slow tracking shots with shallow DOF, static wide establishing shots, handheld documentary style, low-angle dramatic push-ins)",
  "composition": "Describe framing and composition patterns (e.g. rule of thirds with leading lines, centered symmetrical, extreme close-ups, negative space)",
  "mood": "One sentence on emotional tone (e.g. melancholic and contemplative, energetic and urgent, calm and meditative)",
  "scene_type": "Type of scene / genre context (e.g. urban night exterior, indoor interview, nature landscape, product close-up)",
  "motion_character": "Describe the motion feel (e.g. slow and fluid, quick cuts with fast camera whip-pans, gentle floating, locked-off static)",
  "key_visual_elements": "Notable recurring visual motifs or elements visible in the frame"
}

Be specific and detailed. Reference concrete visual qualities of the frame(s)."""


def analyze_frame_style(
    data_uris: list[str],
    cfg: AgnesConfig,
    *,
    verbose: bool = True,
) -> StyleProfile:
    """Send frame(s) to Agnes 2.0 Flash and get back a structured style analysis."""
    if verbose:
        print(
            f"  Analyzing {len(data_uris)} reference frame(s) with vision model...",
            file=sys.stderr,
        )

    # Build content array: text + image(s)
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Analyze the visual style of this video from the extracted frame(s). "
                "Be specific about colors, lighting, camera work, composition, and mood."
            ),
        }
    ]
    for uri in data_uris:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": uri},
            }
        )

    payload = {
        "model": cfg.text_model,
        "messages": [
            {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
    }

    data = request_json(
        "POST",
        "/v1/chat/completions",
        payload,
        cfg=cfg,
        timeout=120,
    )

    try:
        raw_content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(
            f"Style analysis failed: {json.dumps(data, ensure_ascii=False)}"
        ) from exc

    if not raw_content:
        raise SystemExit(
            "Style analysis returned empty content.\n"
            f"Raw response: {json.dumps(data, ensure_ascii=False)}"
        )

    parsed = _parse_style_json(raw_content)

    if verbose:
        print(f"  Extracted style: {parsed}", file=sys.stderr)

    return parsed


def _parse_style_json(raw: str) -> StyleProfile:
    """Parse the model's JSON output into a StyleProfile."""
    cleaned = raw.strip()
    # Strip markdown fences
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
        elif "```" in cleaned:
            cleaned = cleaned[: cleaned.rindex("```")].strip()

    try:
        data: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: find {...} block
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    return StyleProfile(data)


# ── Script generation with reference style ────────────────────────────


_REF_SCRIPT_SYSTEM_PROMPT = """You are a professional short-video scriptwriter.
You will be given:
1. A **visual style profile** extracted from a reference video.
2. A **topic** for a new video.

Create a storyboard that matches the reference's visual style while telling
a new story about the given topic.

Output **only** valid JSON with this exact structure — no markdown, no commentary:

{
  "title": "Video title",
  "description": "One-sentence summary",
  "total_duration": 15.0,
  "style_guide": "The reference style guide in full detail",
  "mood": "Mood derived from reference",
  "target_audience": "Who this is for",
  "scenes": [
    {
      "id": 1,
      "narration": "Voice-over text 1-2 sentences",
      "visual_prompt": "Detailed generation prompt incorporating the reference video's style: color palette, lighting, camera, composition, mood, motion. Include subject, action, environment.",
      "duration_seconds": 5.0,
      "camera": "Camera movement matching reference style",
      "style": "Consistent with reference style profile"
    }
  ]
}

Rules:
- Total 15-60 seconds, each scene 3-10s.
- Every visual_prompt MUST explicitly reference the style profile details
  (color palette, lighting, camera style, composition, mood, motion).
- The output must be parseable as JSON with json.loads()."""


def generate_reference_script(
    topic: str,
    profile: StyleProfile,
    *,
    cfg: AgnesConfig | None = None,
    target_duration: float = 15.0,
    verbose: bool = True,
) -> Script:
    """Generate a script that applies the reference video's style to a new topic."""
    if cfg is None:
        cfg = AgnesConfig.from_env()

    user_prompt = (
        f"Reference style profile:\n"
        f"{json.dumps(profile.to_dict(), indent=2)}\n\n"
        f"New video topic: {topic}\n"
        f"Target duration: {target_duration} seconds.\n"
        f"IMPORTANT: Every scene's visual_prompt must incorporate the reference "
        f"style details — color palette, lighting, camera, composition, mood, and motion."
    )

    if verbose:
        print(
            f"  Generating style-consistent script for: {topic}",
            file=sys.stderr,
        )

    payload = {
        "model": cfg.text_model,
        "messages": [
            {"role": "system", "content": _REF_SCRIPT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": cfg.text_temperature,
        "max_tokens": cfg.text_max_tokens,
    }

    data = request_json("POST", "/v1/chat/completions", payload, cfg=cfg)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(
            f"Reference script generation failed: {json.dumps(data, ensure_ascii=False)}"
        ) from exc

    if not content:
        raise SystemExit("Reference script generation returned empty content.")

    # Reuse the same JSON parser from script_generator
    from agnes_video_creator.script_generator import _parse_script_json  # noqa: PLC0415

    script = _parse_script_json(content, topic)
    script.output_dir = cfg.output_dir

    if verbose:
        scene_count = len(script.scenes)
        print(
            f"  ✓ Script generated: {script.title} "
            f"({scene_count} scenes, ~{script.total_duration}s total)"
            f" — in style of reference",
            file=sys.stderr,
        )

    return script


# ── Orchestration ──────────────────────────────────────────────────────


def analyze_reference_video(
    video_path: str,
    cfg: AgnesConfig,
    *,
    num_frames: int = 3,
    verbose: bool = True,
) -> StyleProfile:
    """Full pipeline: resolve URL → extract frames → encode → analyze → return profile."""
    if verbose:
        print(f"\n  Reference video: {video_path}", file=sys.stderr)

    cfg.ensure_dirs()

    # Step 0: resolve URL (download if needed)
    local_path = _resolve_video_source(video_path, cfg.resolved_temp, verbose=verbose)

    frames_dir = cfg.resolved_temp / "ref_frames"

    # Step 1: extract frames
    if verbose:
        print(f"  Extracting {num_frames} frame(s)...", file=sys.stderr)
    frames = extract_frames(local_path, frames_dir, num_frames=num_frames)
    if not frames:
        raise SystemExit("No frames could be extracted from the reference video.")

    if verbose:
        for f in frames:
            size_kb = f.stat().st_size / 1024
            print(f"    Frame: {f.name} ({size_kb:.0f} KB)", file=sys.stderr)

    # Step 2: encode as data URIs
    data_uris = [encode_frame(f) for f in frames]

    # Step 3: analyze
    profile = analyze_frame_style(data_uris, cfg, verbose=verbose)

    # Save profile to output dir for inspection
    profile_path = cfg.resolved_output / "reference_style.json"
    profile_path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2)
    )
    if verbose:
        print(f"  Style profile saved to: {profile_path}", file=sys.stderr)

    return profile

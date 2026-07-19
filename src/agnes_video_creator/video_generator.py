"""Video generation — uses Agnes Video V2.0 to create clips per scene.

Each scene can be rendered as:
  - text-to-video (from visual_prompt alone)
  - image-to-video (from the generated keyframe + prompt)
  - keyframe animation (from consecutive scene images)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Script
from agnes_video_creator.utils import (
    download_file,
    poll_video_task,
    prepare_prompt,
    request_json,
    slugify,
)


def generate_video_clips(
    script: Script,
    *,
    cfg: AgnesConfig | None = None,
    mode: str = "image-to-video",
    poll: bool = True,
    verbose: bool = True,
) -> Script:
    """Generate a video clip for each scene.

    Parameters
    ----------
    mode : str
        One of:
        - "text-to-video"  : generate from scene.visual_prompt only
        - "image-to-video" : generate from scene.image + visual_prompt (default, best quality)
        - "keyframes"      : animate between consecutive scene images
    """
    if cfg is None:
        cfg = AgnesConfig.from_env()
    if not cfg.has_api_key:
        raise SystemExit("AGNES_API_KEY not set.")

    cfg.ensure_dirs()
    videos_dir = cfg.resolved_output / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    # ── Support modes ────────────────────────────────────────────
    if mode == "keyframes":
        _render_keyframes(script, cfg, videos_dir, poll, verbose)
    else:
        _render_scenes(script, cfg, videos_dir, mode, poll, verbose)

    return script


# ── Per-scene generation (text-to-video / image-to-video) ────────────


def _render_scenes(
    script: Script,
    cfg: AgnesConfig,
    videos_dir: Path,
    mode: str,
    poll: bool,
    verbose: bool,
) -> None:
    for i, scene in enumerate(script.scenes):
        if scene.is_video_ready:
            if verbose:
                print(
                    f"  Scene {scene.id}: video already available, skipping",
                    file=sys.stderr,
                )
            continue

        if verbose:
            src = "image-to-video" if scene.is_image_ready and mode == "image-to-video" else "text-to-video"
            print(
                f"  Scene {scene.id}/{len(script.scenes)}: generating video ({src})...",
                file=sys.stderr,
            )

        final_prompt, orig = prepare_prompt(scene.visual_prompt, cfg)

        payload: dict[str, Any] = {
            "model": cfg.video_model,
            "prompt": final_prompt,
            "width": cfg.video_width,
            "height": cfg.video_height,
            "num_frames": cfg.video_num_frames,
            "frame_rate": cfg.video_frame_rate,
        }
        if cfg.video_num_inference_steps is not None:
            payload["num_inference_steps"] = cfg.video_num_inference_steps
        if cfg.video_seed is not None:
            payload["seed"] = cfg.video_seed
        if cfg.video_negative_prompt:
            payload["negative_prompt"] = cfg.video_negative_prompt

        # Use image if available and mode requires it
        use_image = mode == "image-to-video" and scene.is_image_ready
        if use_image:
            payload["image"] = scene.image_url  # single image

        # Create the video task
        created = request_json("POST", "/v1/videos", payload, cfg=cfg)
        task_id = str(created.get("id") or created.get("task_id", ""))
        if not task_id:
            raise SystemExit(
                f"Scene {scene.id}: create response missing id — "
                f"{json.dumps(created)}"
            )

        if verbose:
            print(
                f"    Task created: {task_id}",
                file=sys.stderr,
            )

        if not poll:
            # Save task ID and continue
            if verbose:
                print(
                    f"    Not polling. Use --poll or:\n"
                    f"      python -m agnes_video_creator video-get {task_id}",
                    file=sys.stderr,
                )
            continue

        # Poll for completion
        data = poll_video_task(task_id, cfg)
        url = _extract_video_url(data)
        if not url:
            raise SystemExit(
                f"Scene {scene.id}: completed but no video URL — "
                f"{json.dumps(data)}"
            )

        scene.video_url = url

        # Download locally
        safe_name = f"scene_{scene.id:03d}_{slugify(script.title)[:30]}"
        local_path = videos_dir / f"{safe_name}.mp4"
        try:
            download_file(url, local_path)
            scene.video_path = str(local_path)
            if verbose:
                print(f"    ✓ Saved: {local_path.name}", file=sys.stderr)
        except Exception as exc:
            if verbose:
                print(
                    f"    ⚠ Download failed (will use URL): {exc}",
                    file=sys.stderr,
                )


# ── Keyframe animation ───────────────────────────────────────────────


def _render_keyframes(
    script: Script,
    cfg: AgnesConfig,
    videos_dir: Path,
    poll: bool,
    verbose: bool,
) -> None:
    """Generate video by animating between consecutive scene images.

    Each consecutive pair of scenes becomes one keyframe transition.
    If there's only one scene, falls back to image-to-video.
    """
    scenes = script.scenes
    if len(scenes) < 2:
        if verbose:
            print(
                "  Only 1 scene — falling back to image-to-video",
                file=sys.stderr,
            )
        _render_scenes(script, cfg, videos_dir, "image-to-video", poll, verbose)
        return

    for i in range(len(scenes) - 1):
        scene_a = scenes[i]
        scene_b = scenes[i + 1]

        if not scene_a.is_image_ready or not scene_b.is_image_ready:
            if verbose:
                print(
                    f"  Keyframe {i + 1}: images not ready, skipping",
                    file=sys.stderr,
                )
            continue

        if scene_b.is_video_ready:
            if verbose:
                print(
                    f"  Keyframe {i + 1}: video already available, skipping",
                    file=sys.stderr,
                )
            continue

        if verbose:
            print(
                f"  Keyframe {i + 1}/{len(scenes) - 1}: "
                f"animating scene {scene_a.id} → {scene_b.id}...",
                file=sys.stderr,
            )

        # Use scene_a's prompt as the transition description
        transition_prompt = (
            f"Create a smooth cinematic transition from the first image to the second image. "
            f"{scene_a.visual_prompt[:200]}"
        )
        final_prompt, _ = prepare_prompt(transition_prompt, cfg)

        payload: dict[str, Any] = {
            "model": cfg.video_model,
            "prompt": final_prompt,
            "width": cfg.video_width,
            "height": cfg.video_height,
            "num_frames": cfg.video_num_frames,
            "frame_rate": cfg.video_frame_rate,
            "extra_body": {
                "image": [scene_a.image_url, scene_b.image_url],
                "mode": "keyframes",
            },
        }

        created = request_json("POST", "/v1/videos", payload, cfg=cfg)
        task_id = str(created.get("id") or created.get("task_id", ""))
        if not task_id:
            raise SystemExit(
                f"Keyframe {i + 1}: create response missing id — "
                f"{json.dumps(created)}"
            )

        if verbose:
            print(f"    Task created: {task_id}", file=sys.stderr)

        if not poll:
            continue

        data = poll_video_task(task_id, cfg)
        url = _extract_video_url(data)
        if not url:
            raise SystemExit(
                f"Keyframe {i + 1}: completed but no video URL — "
                f"{json.dumps(data)}"
            )

        # Store on scene_b (the transition result)
        scene_b.video_url = url
        safe_name = f"keyframe_{i + 1:03d}_{slugify(script.title)[:30]}"
        local_path = videos_dir / f"{safe_name}.mp4"
        try:
            download_file(url, local_path)
            scene_b.video_path = str(local_path)
            if verbose:
                print(f"    ✓ Saved: {local_path.name}", file=sys.stderr)
        except Exception as exc:
            if verbose:
                print(
                    f"    ⚠ Download failed (will use URL): {exc}",
                    file=sys.stderr,
                )


# ── Helpers ──────────────────────────────────────────────────────────


def _extract_video_url(data: dict[str, Any]) -> str | None:
    """Extract the first video URL from an Agnes Video response.

    Checks, in order:
      1. Top-level keys: url, video_url, downloads
      2. Nested in metadata.url (common Agnes API pattern)
      3. List items in data["downloads"] or data["data"]
    """
    for key in ("url", "video_url", "remixed_from_video_id"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value

    # Nested in metadata.url
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        url = metadata.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url

    # Nested in data / downloads array
    for arr_key in ("data", "downloads", "videos"):
        items = data.get(arr_key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    url = item.get("url")
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        return url
    return None

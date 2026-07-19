"""Image generation — uses Agnes Image 2.1 Flash to create keyframe images per scene.

Also triggers character portrait generation if characters are defined.
"""

from __future__ import annotations

import sys
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Script
from agnes_video_creator.portraits import generate_character_portraits
from agnes_video_creator.utils import (
    download_file,
    prepare_prompt,
    request_json,
    slugify,
)


def generate_scene_images(
    script: Script,
    *,
    cfg: AgnesConfig | None = None,
    verbose: bool = True,
) -> Script:
    """For each scene in the script, generate a keyframe image.

    Each generated image URL is stored back on the Scene object,
    and the image is downloaded locally.
    """
    if cfg is None:
        cfg = AgnesConfig.from_env()
    if not cfg.has_api_key:
        raise SystemExit("AGNES_API_KEY not set.")

    cfg.ensure_dirs()
    images_dir = cfg.resolved_output / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Generate character portraits first (ensures consistent faces)
    if script.characters:
        generate_character_portraits(script, cfg=cfg, verbose=verbose)
        script.save(str(cfg.resolved_output / "script.json"))

    for i, scene in enumerate(script.scenes):
        if scene.is_image_ready:
            if verbose:
                print(
                    f"  Scene {scene.id}: image already available, skipping",
                    file=sys.stderr,
                )
            continue

        if verbose:
            print(
                f"  Scene {scene.id}/{len(script.scenes)}: generating image...",
                file=sys.stderr,
            )

        # Inject character appearances into the visual prompt
        enriched = script.inject_characters(
            scene.visual_prompt, scene.character_appearances
        )
        # Prepare the prompt (translate non-English if needed)
        final_prompt, orig = prepare_prompt(enriched, cfg)
        if orig and verbose:
            print(f"    (translated from: {orig[:80]}...)", file=sys.stderr)

        # Pick a seed from the first appearing character, else global or random
        scene_seed = cfg.video_seed
        if not scene_seed and scene.character_appearances and script.characters:
            for ch in script.characters:
                if ch.name in scene.character_appearances and ch.seed:
                    scene_seed = ch.seed + scene.id
                    break

        # Call Agnes Image API
        payload: dict[str, Any] = {
            "model": cfg.image_model,
            "prompt": final_prompt,
            "size": cfg.image_size,
        }
        if scene_seed:
            payload["seed"] = scene_seed
        # Add ratio if we have one
        if hasattr(cfg, "image_ratio") and cfg.image_ratio:
            payload["ratio"] = cfg.image_ratio

        # Request URL output format
        payload["extra_body"] = {"response_format": "url"}

        data = request_json(
            "POST",
            "/v1/images/generations",
            payload,
            cfg=cfg,
            timeout=180,
        )

        url = _extract_image_url(data)
        if not url:
            raise SystemExit(
                f"Scene {scene.id}: no image URL in response — "
                f"{data}"
            )

        scene.image_url = url

        # Download the image locally
        safe_name = f"scene_{scene.id:03d}_{slugify(script.title)[:30]}"
        local_path = images_dir / f"{safe_name}.png"
        try:
            download_file(url, local_path)
            scene.image_path = str(local_path)
            if verbose:
                print(f"    ✓ Saved: {local_path.name}", file=sys.stderr)
        except Exception as exc:
            if verbose:
                print(
                    f"    ⚠ Download failed (will use URL): {exc}",
                    file=sys.stderr,
                )

    return script


def _extract_image_url(data: dict[str, Any]) -> str | None:
    """Extract the first image URL from an Agnes Image response."""
    # Response format: { "data": [ { "url": "..." } ] }
    if isinstance(data.get("data"), list):
        for item in data["data"]:
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    return url
    # Direct url field
    url = data.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    return None

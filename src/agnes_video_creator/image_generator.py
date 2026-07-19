"""Image generation — uses Agnes Image 2.1 Flash to create keyframe images per scene.

Also triggers character portrait generation if characters are defined.
"""

from __future__ import annotations

import sys
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Character, Script
from agnes_video_creator.portraits import generate_character_portraits
from agnes_video_creator.utils import (
    download_file,
    prepare_prompt,
    request_json,
    slugify,
)


def _inject_face_features(
    visual_prompt: str,
    scene_characters: list[str],
    script: Script,
) -> str:
    """Inject detailed face features into visual prompt, preferring FaceFeatures
    over appearance text and falling back to the generic inject_characters."""
    if not scene_characters or not script.characters:
        return visual_prompt

    injections: list[str] = []
    for ch in script.characters:
        if ch.name not in scene_characters:
            continue
        if ch.face_features and ch.face_features.is_populated():
            injections.append(f"{ch.name}: {ch.face_features.to_prompt_snippet()}")
        elif ch.appearance:
            injections.append(f"{ch.name}: {ch.appearance}")

    if not injections:
        return visual_prompt
    return f"Characters: {'; '.join(injections)}. {visual_prompt}"


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

        enriched = _inject_face_features(
            scene.visual_prompt, scene.character_appearances, script
        )
        final_prompt, orig = prepare_prompt(enriched, cfg)
        if orig and verbose:
            print(f"    (translated from: {orig[:80]}...)", file=sys.stderr)

        scene_seed = cfg.video_seed
        if not scene_seed and scene.character_appearances and script.characters:
            for ch in script.characters:
                if ch.name in scene.character_appearances and ch.seed:
                    scene_seed = ch.seed + scene.id
                    break

        payload: dict[str, Any] = {
            "model": cfg.image_model,
            "prompt": final_prompt,
            "size": cfg.image_size,
        }
        if scene_seed:
            payload["seed"] = scene_seed
        if hasattr(cfg, "image_ratio") and cfg.image_ratio:
            payload["ratio"] = cfg.image_ratio

        portrait_ref = _pick_portrait_ref(scene.character_appearances, script.characters)
        if portrait_ref:
            payload["image"] = portrait_ref

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
    if isinstance(data.get("data"), list):
        for item in data["data"]:
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    return url
    url = data.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    return None


def _pick_portrait_ref(
    scene_characters: list[str],
    characters: list[Character],
) -> str | None:
    """Pick the first available portrait URL for characters in this scene.

    Returns the URL of the first character (by appearance order) that
    has a portrait_url, or None if no character has one.
    """
    if not scene_characters or not characters:
        return None
    for ch in characters:
        if ch.name in scene_characters and ch.portrait_url:
            return ch.portrait_url
    return None

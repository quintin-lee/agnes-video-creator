"""Character portrait generation — create reference images for face consistency.

Each character gets a consistent reference portrait.  These portraits
are used to:
  1. Serve as visual anchors in prompts ("like this person...")
  2. Provide consistent seed values per character
  3. (Future) Pass as reference images to image/video APIs
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Character, Script
from agnes_video_creator.utils import (
    download_file,
    prepare_prompt,
    request_json,
    slugify,
)


def generate_character_portraits(
    script: Script,
    *,
    cfg: AgnesConfig | None = None,
    verbose: bool = True,
) -> Script:
    """Generate a reference portrait for each character missing one.

    Mutates the Script's characters in place, setting portrait_url
    and portrait_path.  Existing portraits are skipped.
    """
    if cfg is None:
        cfg = AgnesConfig.from_env()
    if not cfg.has_api_key:
        raise SystemExit("AGNES_API_KEY not set.")

    if not script.characters:
        if verbose:
            print("  No characters defined — nothing to generate.", file=sys.stderr)
        return script

    cfg.ensure_dirs()
    portraits_dir = cfg.resolved_output / "portraits"
    portraits_dir.mkdir(parents=True, exist_ok=True)

    for i, char in enumerate(script.characters):
        if char.portrait_url and char.portrait_path:
            if verbose:
                print(f"  {char.name}: portrait exists, skipping", file=sys.stderr)
            continue

        if not char.appearance:
            if verbose:
                print(f"  {char.name}: no appearance description, skipping", file=sys.stderr)
            continue

        if verbose:
            print(f"  {char.name}: generating portrait...", file=sys.stderr)

        # Assign a consistent seed per character (hash of name)
        if char.seed == 0:
            char.seed = abs(hash(char.name)) % 2**31

        prompt = (
            f"A clear, well-lit character reference portrait of "
            f"{char.appearance}. "
            f"Front-facing, neutral expression, clean background, "
            f"professional lighting, photorealistic, 4K"
        )
        final_prompt, _ = prepare_prompt(prompt, cfg)

        payload: dict[str, Any] = {
            "model": cfg.image_model,
            "prompt": final_prompt,
            "size": cfg.image_size,
            "seed": char.seed,
            "extra_body": {"response_format": "url"},
        }

        data = request_json(
            "POST",
            "/v1/images/generations",
            payload,
            cfg=cfg,
            timeout=180,
        )

        url = _extract_portrait_url(data)
        if not url:
            if verbose:
                print(f"    ⚠ No URL in response for {char.name}", file=sys.stderr)
            continue

        char.portrait_url = url

        # Download locally
        safe_name = f"portrait_{slugify(char.name)[:20]}"
        local_path = portraits_dir / f"{safe_name}.png"
        try:
            download_file(url, local_path)
            char.portrait_path = str(local_path)
            if verbose:
                print(f"    ✓ Saved: {local_path.name} (seed={char.seed})", file=sys.stderr)
        except Exception as exc:
            if verbose:
                print(f"    ⚠ Download failed: {exc}", file=sys.stderr)

    return script


def inject_portraits_into_prompt(
    visual_prompt: str,
    character_appearances: list[str],
    characters: list[Character],
) -> str:
    """Enrich a visual_prompt with portrait references for appearing characters.

    Prepends portrait context so the API sees the reference.  Falls back
    to the base prompt if no characters have portraits.
    """
    if not character_appearances:
        return visual_prompt

    refs: list[str] = []
    for name in character_appearances:
        for ch in characters:
            if ch.name == name and ch.portrait_url:
                refs.append(f"{ch.name} (reference: {ch.portrait_url})")
                break

    if not refs:
        return visual_prompt

    return f"Character reference images: {'; '.join(refs)}. Ensure these characters look identical to their reference. {visual_prompt}"


def _extract_portrait_url(data: dict[str, Any]) -> str | None:
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

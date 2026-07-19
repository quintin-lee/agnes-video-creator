"""Face analysis — extract structured facial features from character portraits.

Uses the Agnes 2.0 Flash vision model to analyze portrait images and
extract detailed, structured face descriptions.  These features are
injected into every scene's visual prompt so the image/video model
produces consistent faces across all scenes.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import FaceFeatures


# ── Vision-based analysis prompt ────────────────────────────────────────

_ANALYSIS_SYSTEM_PROMPT = """You are a forensic face analyst.  Given a portrait photo,
identify the person's facial features precisely.

Output **only** valid JSON with this exact structure — no markdown fences,
no commentary:

{
  "face_shape": "oval|round|square|heart|diamond|long|",
  "eye_shape": "round|almond|hooded|monolid|downturned|",
  "eye_color": "dark_brown|light_brown|blue|green|grey|hazel|",
  "eyebrow": "straight|arched|thick|thin|bushy|",
  "nose": "straight|aquiline|button|wide|narrow|pointed|",
  "mouth_lips": "full|thin|wide|small|cupid_bow|",
  "jaw_chin": "strong_jaw|pointed_chin|round_chin|soft_jaw|",
  "skin_tone": "fair|light|medium|tan|olive|brown|dark|",
  "skin_texture": "smooth|freckled|weathered|clear|",
  "hair_style": "short|long|curly|straight|wavy|ponytail|bun|bald|",
  "hair_color": "black|brown|blonde|red|grey|white|dyed|",
  "age_range": "child|teen|young_adult|middle_aged|elderly|",
  "gender_presentation": "masculine|feminine|androgynous|",
  "distinctive_features": ["scar_on_left_cheek", "mole_above_lip", "glasses"]
}

Rules:
- Choose the single best value for each field from the pipe-separated options.
- Leave a field empty string if you cannot determine it confidently.
- distinctive_features is a list of notable marks, scars, moles, wrinkles,
  piercings, tattoos, or other unique identifiers.  Empty list if none.
- Be precise — this description will be used to recreate the exact same
  face across multiple AI-generated images.

IMPORTANT: If there is NO clearly visible human face in the image, return
{"error": "no face detected"} as the sole JSON key."""


# ── Public API ──────────────────────────────────────────────────────────


def analyze_face(
    image_url: str,
    cfg: AgnesConfig,
    *,
    verbose: bool = True,
) -> FaceFeatures | None:
    """Analyze a portrait image and return structured facial features.

    Parameters
    ----------
    image_url : str
        URL or data URI of the portrait image.
    cfg : AgnesConfig
        Global config (used for API credentials).

    Returns
    -------
    FaceFeatures or None
        None when no face could be detected in the image.
    """
    if verbose:
        from pathlib import Path
        # Show a short name instead of a huge URL
        label = Path(image_url).name if "://" in image_url else image_url[:60]
        print(f"  Analyzing face: {label}", file=sys.stderr)

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Analyze the face in this portrait. "
                "Extract every facial feature precisely."
            ),
        },
        {
            "type": "image_url",
            "image_url": {"url": image_url},
        },
    ]

    payload = {
        "model": cfg.text_model,
        "messages": [
            {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }

    data = request_json(
        "POST",
        "/v1/chat/completions",
        payload,
        cfg=cfg,
        timeout=120,
    )

    raw_content = _extract_content(data)
    if not raw_content:
        if verbose:
            print("    ⚠ Face analysis returned empty content", file=sys.stderr)
        return None

    parsed = _parse_features(raw_content)
    if parsed is None:
        if verbose:
            print("    ⚠ No face detected in portrait", file=sys.stderr)
        return None

    if verbose and parsed.is_populated():
        # Show a compact summary
        summary = parsed.to_prompt_snippet()
        print(f"    ✓ {summary[:120]}...", file=sys.stderr)

    return parsed


def validate_portrait_face(image_url: str, cfg: AgnesConfig) -> bool:
    """Quick check: does the image contain a detectable human face?

    Returns True if the vision model confirms a face is present.
    """
    features = analyze_face(image_url, cfg, verbose=False)
    return features is not None and features.is_populated()


# ── Internal helpers ────────────────────────────────────────────────────


def _extract_content(data: dict[str, Any]) -> str | None:
    """Extract message content from an OpenAI-compatible response."""
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _parse_features(raw: str) -> FaceFeatures | None:
    """Parse the model's JSON output into a FaceFeatures dataclass.

    Returns None if the model reports "no face detected".
    """
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
        data: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError:
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                data = json.loads(cleaned[brace_start: brace_end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None

    # Check if the model explicitly says no face
    if data.get("error") and "no face" in str(data["error"]).lower():
        return None

    # Map snake_case keys from the analysis to the FaceFeatures dataclass
    key_map = {
        "face_shape": "face_shape",
        "eye_shape": "eye_shape",
        "eye_color": "eye_color",
        "eyebrow": "eyebrow",
        "nose": "nose",
        "mouth_lips": "mouth_lips",
        "jaw_chin": "jaw_chin",
        "skin_tone": "skin_tone",
        "skin_texture": "skin_texture",
        "hair_style": "hair_style",
        "hair_color": "hair_color",
        "age_range": "age_range",
        "gender_presentation": "gender_presentation",
        "distinctive_features": "distinctive_features",
    }

    kwargs: dict[str, Any] = {}
    for json_key, dc_key in key_map.items():
        val = data.get(json_key, "")
        if json_key == "distinctive_features":
            # Ensure it's a list of strings
            if isinstance(val, list):
                kwargs[dc_key] = [str(v) for v in val]
            else:
                kwargs[dc_key] = []
        else:
            kwargs[dc_key] = str(val) if val else ""

    return FaceFeatures(**kwargs)


def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    cfg: AgnesConfig | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Re-exported from utils to avoid circular imports."""
    from agnes_video_creator.utils import request_json as _rj  # noqa: PLC0415
    return _rj(method, path, payload, cfg, timeout=timeout)

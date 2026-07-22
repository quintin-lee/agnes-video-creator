"""NLP-based scene sound effect matcher.

Analyses scene narration and visual_prompt to automatically suggest
SFX descriptions.  Uses a keyword map first (fast, free), then falls
back to the Agnes text model for ambiguous scenes.
"""

from __future__ import annotations

import re
import sys

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Script

# ── Built-in keyword → SFX map ──────────────────────────────────────────
# Each entry: {keyword: sfx_description}
# Matching is case-insensitive substring on narration + visual_prompt.
_KEYWORD_SFX: dict[str, str] = {
    # Weather / environment
    "rain": "rain falling",
    "raining": "rain falling",
    "storm": "thunder storm",
    "thunder": "thunder rumble",
    "lightning": "thunder rumble",
    "wind": "wind howling",
    "windy": "wind howling",
    "snow": "wind howling",
    "snowing": "wind howling",
    "ocean": "waves crashing",
    "sea": "waves crashing",
    "waves": "waves crashing",
    "river": "water flowing",
    "waterfall": "water flowing",
    "rain": "rain falling",
    "fire": "fire crackling",
    "burning": "fire crackling",
    "explosion": "explosion",
    "explode": "explosion",
    "earthquake": "rumbling",
    "quake": "rumbling",
    # Nature
    "forest": "birds chirping",
    "jungle": "birds chirping",
    "birds": "birds chirping",
    "bird": "birds chirping",
    "cricket": "crickets",
    "crickets": "crickets",
    "night": "crickets",
    "frog": "frog croaking",
    "frogs": "frog croaking",
    # Human actions
    "footstep": "footsteps",
    "footsteps": "footsteps",
    "walking": "footsteps",
    "running": "running footsteps",
    "run": "running footsteps",
    "door open": "door creaking",
    "door close": "door slam",
    "door slam": "door slam",
    "knock": "knocking",
    "knocking": "knocking",
    "phone": "phone ringing",
    "telephone": "phone ringing",
    "ringing": "phone ringing",
    "applause": "applause",
    "clap": "applause",
    "clapping": "applause",
    "laugh": "laughter",
    "laughing": "laughter",
    "laughter": "laughter",
    "crying": "crying",
    "cry": "crying",
    "scream": "screaming",
    "screaming": "screaming",
    "whisper": "whispering",
    "whispering": "whispering",
    "cough": "coughing",
    "coughing": "coughing",
    "sigh": "sigh",
    "sighing": "sigh",
    "drink": "drinking",
    "drinking": "drinking",
    "eat": "eating",
    "eating": "eating",
    "chew": "eating",
    "chewing": "eating",
    # Combat / action
    "sword": "sword clash",
    "fight": "fighting",
    "fighting": "fighting",
    "punch": "punch",
    "hit": "punch",
    "gun": "gunshot",
    "gunshot": "gunshot",
    "shoot": "gunshot",
    "shooting": "gunshot",
    "blast": "explosion",
    # Vehicles
    "car": "car engine",
    "engine": "car engine",
    "driving": "car driving",
    "racing": "car driving",
    "motorcycle": "motorcycle engine",
    "bike": "motorcycle engine",
    "train": "train",
    "subway": "train",
    "airplane": "airplane flyby",
    "plane": "airplane flyby",
    "helicopter": "helicopter",
    "helicopter": "helicopter",
    "ship": "ship horn",
    "boat": "boat engine",
    # Ambience
    "crowd": "crowd ambience",
    "market": "crowd ambience",
    "street": "traffic ambience",
    "city": "traffic ambience",
    "traffic": "traffic ambience",
    "restaurant": "restaurant ambience",
    "cafe": "restaurant ambience",
    "coffee": "restaurant ambience",
    "bar": "crowd ambience",
    "pub": "crowd ambience",
    "church": "church bell",
    "bell": "church bell",
    "clock": "clock ticking",
    "tick": "clock ticking",
    "alarm": "alarm",
    "siren": "siren",
    # Fantasy / cinematic
    "magic": "magic spell",
    "spell": "magic spell",
    "whoosh": "whoosh",
    "whoosh": "whoosh",
    "dragon": "dragon roar",
    "roar": "dragon roar",
    "monster": "monster roar",
    "ghost": "ghostly whisper",
    "ghostly": "ghostly whisper",
}

# ── Priority keywords (higher weight in scoring) ──────────────────────
_PRIORITY_KW: set[str] = {
    "explosion", "gunshot", "scream", "applause", "thunder",
    "door slam", "alarm", "siren", "roar", "magic",
}


def _tokenise(text: str) -> set[str]:
    """Lower-case, strip punctuation, return word set."""
    return set(re.findall(r"[a-z]+", text.lower()))


def suggest_sfx(narration: str, visual_prompt: str) -> str:
    """Suggest an SFX description from scene narration + visual_prompt.

    Uses keyword matching against the built-in map.  Returns the
    highest-scoring SFX description, or an empty string if nothing
    matches.
    """
    combined = f"{narration} {visual_prompt}"
    words = _tokenise(combined)

    # Score each candidate SFX by keyword hit count, with priority boost
    scores: dict[str, int] = {}
    for keyword, sfx_desc in _KEYWORD_SFX.items():
        kw_tokens = _tokenise(keyword)
        matches = len(kw_tokens & words)
        if matches > 0:
            boost = 2 if keyword in _PRIORITY_KW else 1
            cur = scores.get(sfx_desc, 0)
            scores[sfx_desc] = cur + matches * boost

    if not scores:
        return ""

    # Return highest-scoring
    best = max(scores, key=lambda k: scores[k])
    return best


def auto_fill_sfx(script: Script, cfg: AgnesConfig, *, verbose: bool = True) -> Script:
    """Auto-fill ``sfx`` field for scenes that don't have one yet.

    Uses keyword matching; scenes already with an ``sfx`` value are
    left unchanged.  Optionally falls back to the Agnes text model
    when keyword matching yields nothing and ``cfg.text_model`` is set.
    """
    filled = 0
    for scene in script.scenes:
        if scene.sfx:
            continue
        suggested = suggest_sfx(scene.narration, scene.visual_prompt)
        if suggested:
            scene.sfx = suggested
            filled += 1

    if filled and verbose:
        print(f"  sfx-matcher: auto-filled {filled} scene(s)", file=sys.stderr)

    # Optional LLM fallback for scenes still empty
    scenes_missing = [s for s in script.scenes if not s.sfx]
    if scenes_missing and cfg.sfx_dir:
        from .utils import request_json

        for scene in scenes_missing:
            prompt_body = (
                f"Given this scene description, suggest ONE short sound effect description "
                f"(3 words max, in English, e.g. 'wind howling' or 'sword clash'). "
                f"Only return the SFX text, nothing else.\n\n"
                f"Visual: {scene.visual_prompt}\nNarration: {scene.narration}"
            )
            try:
                data = request_json(
                    "POST",
                    "/v1/chat/completions",
                    {
                        "model": cfg.text_model,
                        "messages": [
                            {"role": "system", "content": "You suggest sound effects for video scenes. Return only the SFX description."},
                            {"role": "user", "content": prompt_body},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 30,
                    },
                )
                sfx_text = data["choices"][0]["message"]["content"].strip().lower()
                # Sanity: reject if too long or empty
                if sfx_text and len(sfx_text) < 60:
                    scene.sfx = sfx_text
                    filled += 1
            except Exception:
                pass

        if filled and verbose:
            print(f"  sfx-matcher: LLM filled {filled - (filled - len(scenes_missing) + len([s for s in scenes_missing if s.sfx]))} scene(s)", file=sys.stderr)

    return script

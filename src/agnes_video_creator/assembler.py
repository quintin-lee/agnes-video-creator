"""Video assembler — stiches scene clips into a final video using ffmpeg.

Supports:
  - Concatenating video clips
  - Crossfade / fade transitions between clips
  - Trimming clips to match scene duration
  - Optional TTS narration overlay (via edge-tts / pyttsx3 / espeak)
  - Final encode with consistent settings
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Script


def assemble_video(
    script: Script,
    *,
    cfg: AgnesConfig | None = None,
    output_name: str = "",
    verbose: bool = True,
) -> Path:
    """Assemble all scene clips into the final video.

    Returns the path to the rendered MP4.
    """
    if cfg is None:
        cfg = AgnesConfig.from_env()

    # Check ffmpeg availability
    if not _check_ffmpeg():
        raise SystemExit(
            "ffmpeg is required for video assembly. Install it via your package manager."
        )

    cfg.ensure_dirs()
    temp_dir = cfg.resolved_temp
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Collect available clips
    clip_paths: list[Path] = []
    for scene in script.scenes:
        if scene.video_path and Path(scene.video_path).exists():
            clip_paths.append(Path(scene.video_path))
        elif scene.video_url:
            if verbose:
                print(
                    f"  Scene {scene.id}: video URL not downloaded, will use it",
                    file=sys.stderr,
                )
        else:
            if verbose:
                print(
                    f"  Scene {scene.id}: no video available, skipping",
                    file=sys.stderr,
                )

    if not clip_paths:
        # If keyframe mode, check scene 2+
        for scene in script.scenes[1:]:
            if scene.video_path and Path(scene.video_path).exists():
                clip_paths.append(Path(scene.video_path))
        if not clip_paths:
            raise SystemExit("No video clips available to assemble. Run 'render' first.")

    if verbose:
        print(
            f"  Assembling {len(clip_paths)} clip(s)...",
            file=sys.stderr,
        )

    # ── Step 0: Trim clips ────────────────────────────────
    trimmed = []
    for i, clip in enumerate(clip_paths):
        scene = script.scenes[i] if i < len(script.scenes) else None
        if scene and (scene.trim_in or scene.trim_out):
            trim_path = temp_dir / f"trim_{i:04d}.mp4"
            _trim_clip(clip, trim_path, scene.trim_in, scene.trim_out, verbose)
            trimmed.append(trim_path)
        else:
            trimmed.append(clip)

    # ── Step 1: Normalise all clips with camera motion ─────────
    normalised = []
    for i, clip in enumerate(trimmed):
        norm_path = temp_dir / f"norm_{i:04d}.mp4"
        camera = script.scenes[i].camera if i < len(script.scenes) else ""
        _normalise_clip(clip, norm_path, cfg, verbose, camera_desc=camera)
        normalised.append(norm_path)

    # ── Step 2: Generate concat file ────────────────────────────
    concat_file = temp_dir / "concat_list.txt"

    if cfg.transition != "none" and len(normalised) > 1:
        final_path = _assemble_with_fades(normalised, concat_file, temp_dir, cfg, verbose)
    else:
        # Simple concat
        final_path = _assemble_simple(normalised, concat_file, temp_dir, cfg, verbose)

    # ── Step 3: Add narration if requested ──────────────────────
    if cfg.add_audio:
        final_path = _add_narration(final_path, script, temp_dir, cfg, verbose)

    # ── Step 3a: Prepend title card if requested ────────────────
    if cfg.title_card and script.title:
        title_path = _create_title_card(
            script.title, script.description or "", temp_dir, cfg, verbose
        )
        if title_path:
            concat_list = temp_dir / "title_concat.txt"
            concat_list.write_text(
                f"file '{title_path.resolve()}'\nfile '{final_path.resolve()}'\n"
            )
            concat_output = temp_dir / "with_title.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    str(concat_output),
                ],
                capture_output=True,
                check=False,
            )
            if concat_output.exists():
                final_path = concat_output

    # ── Step 3b: Append end credits if requested ────────────────
    if cfg.end_credits:
        credits_path = _create_end_credits(script, temp_dir, cfg, verbose)
        if credits_path:
            concat_list = temp_dir / "credits_concat.txt"
            concat_list.write_text(
                f"file '{final_path.resolve()}'\nfile '{credits_path.resolve()}'\n"
            )
            concat_output = temp_dir / "with_credits.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    str(concat_output),
                ],
                capture_output=True,
                check=False,
            )
            if concat_output.exists():
                final_path = concat_output

    # ── Step 3.5: Add background music if configured ────────────
    if cfg.bgm_path:
        bgm = Path(cfg.bgm_path)
        if bgm.exists():
            final_path = _add_bgm(final_path, temp_dir, cfg, verbose)
        elif verbose:
            print(f"  ⚠ BGM file not found: {bgm}", file=sys.stderr)

    # ── Step 4: Inject generation metadata ──────────────────────
    if cfg.add_metadata:
        final_path = _inject_metadata(final_path, script, temp_dir, cfg, verbose)

    # ── Step 4a: Embed chapter markers ──────────────────────────
    if cfg.add_chapters:
        final_path = _embed_chapters(final_path, script, temp_dir, verbose)

    # ── Step 5: Copy to final output ────────────────────────────
    if not output_name:
        safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in script.title)
        output_name = f"{safe}.mp4"
    output_path = cfg.resolved_output / output_name
    shutil.copy2(final_path, output_path)

    if verbose:
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(
            f"  ✓ Final video: {output_path} ({size_mb:.1f} MB)",
            file=sys.stderr,
        )

    # ── Step 5a: Extract thumbnail ──────────────────────────────
    if cfg.add_thumbnail:
        thumb = _extract_thumbnail(output_path, cfg.resolved_output, cfg, verbose)
        if thumb and verbose:
            print(f"  ✓ Thumbnail: {thumb}", file=sys.stderr)

    return output_path


# ── ffmpeg helpers ─────────────────────────────────────────────────────


def _check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _run_ffmpeg(cmd: list[str], description: str = "", verbose: bool = True) -> None:
    if verbose:
        print(f"  ffmpeg: {description}", file=sys.stderr)
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=not verbose,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        if not verbose:
            print(exc.stderr, file=sys.stderr)
        raise SystemExit(f"ffmpeg failed ({description}): {exc}") from exc


def _camera_motion_filter(
    camera_desc: str,
    duration: float,
    fps: int = 24,
    width: int = 1152,
    height: int = 768,
) -> str:
    """Map a Chinese/English camera description to an ffmpeg video filter.

    Returns an empty string when no motion should be applied.
    """
    desc = camera_desc.lower()

    # Zoom effects
    if any(k in desc for k in ("zoom", "push in", "dolly in", "close-up", "closeup", "close up")):
        return (
            f"zoompan=z='min(zoom+0.005,1.3)':d={int(duration * fps)}:s={width}x{height}:fps={fps}"
        )
    if any(k in desc for k in ("zoom out", "pull out", "dolly out", "wide")):
        return (
            f"zoompan=z='max(zoom-0.005,0.8)':d={int(duration * fps)}:s={width}x{height}:fps={fps}"
        )

    # Pan effects
    if "pan left" in desc:
        return f"crop=iw-50:ih:(iw-50)*(t/{duration}):0"
    if "pan right" in desc:
        return f"crop=iw-50:ih:(iw-50)*(1-t/{duration}):0"
    if any(k in desc for k in ("pan up", "tilt up")):
        return f"crop=iw:ih-50:0:(ih-50)*(t/{duration})"
    if any(k in desc for k in ("pan down", "tilt down")):
        return f"crop=iw:ih-50:0:(ih-50)*(1-t/{duration})"

    # Tracking / follow
    if any(k in desc for k in ("track", "follow", "dolly")):
        return (
            f"zoompan=z='min(zoom+0.003,1.2)':d={int(duration * fps)}:s={width}x{height}:fps={fps}"
        )

    # Rotation / Dutch angle
    if any(k in desc for k in ("rotate", "dutch", "tilted")):
        return "rotate=2*PI*t/5:ow=1.2*iw:oh=1.2*ih,crop=iw/1.2:ih/1.2"

    # Aerial / crane
    if any(k in desc for k in ("aerial", "crane", "bird", "overhead")):
        return (
            f"zoompan=z='max(zoom-0.008,0.7)':d={int(duration * fps)}:s={width}x{height}:fps={fps}"
        )

    # Handheld / shaking
    if any(k in desc for k in ("handheld", "shake", "shaky", "camera shake")):
        return "crop=iw-20:ih-20:random(0)*20:random(1)*20"

    # Static / default — no filter
    return ""


def _trim_clip(
    src: Path,
    dst: Path,
    trim_in: float,
    trim_out: float,
    verbose: bool = True,
) -> None:
    """Trim a video clip: cut trim_in seconds from start, trim_out from end."""
    if dst.exists():
        return
    if trim_in <= 0 and trim_out <= 0:
        # Nothing to trim — copy as-is
        import shutil

        shutil.copy2(src, dst)
        return

    dur = _get_duration(src)
    if dur <= 0:
        if verbose:
            print(f"    ⚠ Cannot determine duration for {src.name}, skipping trim", file=sys.stderr)
        import shutil

        shutil.copy2(src, dst)
        return

    start = trim_in
    end = dur - trim_out
    if end <= start:
        if verbose:
            print(
                f"    ⚠ Trim in/out would leave empty clip ({start}s → {end}s), keeping original",
                file=sys.stderr,
            )
        import shutil

        shutil.copy2(src, dst)
        return

    duration = end - start
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(src),
        "-t",
        f"{duration:.3f}",
        "-c",
        "copy",
        str(dst),
    ]
    _run_ffmpeg(cmd, f"trim {src.name} [{start:.1f}s → {end:.1f}s]", verbose)


def _normalise_clip(
    src: Path,
    dst: Path,
    cfg: AgnesConfig,
    verbose: bool,
    camera_desc: str = "",
) -> None:
    """Normalise a video clip, optionally applying camera motion.

    camera_desc is a Chinese/English camera-motion description (e.g.
    "slow zoom in", "pan right", "handheld").
    """
    if dst.exists():
        return

    # Build video filter chain
    filters = [f"fps={cfg.target_fps}"]
    if camera_desc:
        motion = _camera_motion_filter(
            camera_desc,
            5.0,
            fps=cfg.target_fps,
            width=cfg.video_width,
            height=cfg.video_height,
        )
        if motion:
            filters.append(motion)
    vf = ",".join(filters)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    _run_ffmpeg(cmd, f"normalise {src.name}", verbose)


def _assemble_simple(
    clips: list[Path],
    concat_file: Path,
    temp_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path:
    """Simple concatenation without transitions."""
    # Write concat file
    lines = [f"file '{clip.resolve()}'\n" for clip in clips]
    concat_file.write_text("".join(lines))

    output = temp_dir / "concat_simple.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output),
    ]
    _run_ffmpeg(cmd, "concatenate clips", verbose)
    return output


def _assemble_with_fades(
    clips: list[Path],
    concat_file: Path,
    temp_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path:
    """Concatenate with crossfade transitions using ffmpeg's filter graph."""
    if len(clips) == 1:
        return _assemble_simple(clips, concat_file, temp_dir, cfg, verbose)

    td = cfg.transition_duration

    # Build filter_complex for crossfade
    # For N clips, we need N-1 crossfade operations
    filter_parts: list[str] = []
    # Name each input stream
    for i in range(len(clips)):
        filter_parts.append(f"[{i}:v][{i}:a]")
    # Add crossfade filters
    filter_parts.append(f"concat=n={len(clips)}:v=1:a=1[outv][outa]")

    # More robust approach: use concat demuxer with trimmed overlaps
    # Write a concat file with duration info
    durations = []
    for clip in clips:
        dur = _get_duration(clip)
        durations.append(dur)

    output = temp_dir / "concat_fade.mp4"
    # Use complex filter for crossfade
    # For simplicity, use overlay fade approach
    cmd = [
        "ffmpeg",
        "-y",
    ]
    for clip in clips:
        cmd += ["-i", str(clip)]

    # Build filter: overlay each clip with fade transitions
    filters = []
    n = len(clips)

    # For each pair of adjacent clips, apply crossfade
    # Start with first clip
    prev = "0:v"
    prev_a = "0:a"

    for i in range(1, n):
        # Calculate cumulative duration of previous clips minus transitions
        prev_dur = sum(durations[:i]) - td * i
        xfade_type = _pick_transition(cfg.transition, i)
        filters.append(
            f"[{prev}][{i}:v]"
            f"xfade=transition={xfade_type}:duration={td}:offset={prev_dur - td}[v{i}]"
        )
        # Audio crossfade
        filters.append(f"[{prev_a}][{i}:a]acrossfade=d={td}[a{i}]")
        prev = f"v{i}"
        prev_a = f"a{i}"

    if not filters:
        return _assemble_simple(clips, concat_file, temp_dir, cfg, verbose)

    filter_chain = ";".join(filters)
    f"[v{n - 1}][a{n - 1}]"

    cmd += [
        "-filter_complex",
        filter_chain,
        "-map",
        f"[v{n - 1}]",
        "-map",
        f"[a{n - 1}]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output),
    ]

    _run_ffmpeg(cmd, "crossfade assembly", verbose)
    return output


def _get_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        # Default fallback: treat as 5 seconds
        return 5.0


_XFADE_POOL = [
    "fade",
    "dissolve",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "pixelize",
    "fadeblack",
    "fadewhite",
    "radial",
]

# Deterministic pseudo-random picker for transition types
_xfade_idx: int = 0


def _pick_transition(style: str, clip_index: int) -> str:
    """Map a friendly transition name to an ffmpeg xfade transition type.

    'fade' / 'dissolve' / 'wipe' / 'slide' map directly.
    'random' picks from the pool deterministically based on clip_index.
    """
    global _xfade_idx
    if style == "fade":
        return "fade"
    if style == "dissolve":
        return "dissolve"
    if style == "wipe":
        choices = ["wipeleft", "wiperight", "wipeup", "wipedown"]
        return choices[clip_index % len(choices)]
    if style == "slide":
        choices = ["slideleft", "slideright", "slideup", "slidedown"]
        return choices[clip_index % len(choices)]
    if style == "random":
        _xfade_idx += 1
        return _XFADE_POOL[abs(hash(f"{_xfade_idx}_{clip_index}")) % len(_XFADE_POOL)]
    return "fade"


# ── Narration / TTS ────────────────────────────────────────────────────


_NARRATION_SCRIPT = """#!/usr/bin/env python3
\"\"\"Generate narration + multi-character dialogue audio from scene scripts.\"\"\"
import json
import sys
from pathlib import Path

try:
    import edge_tts
except ImportError:
    print("edge-tts not installed. Install: pip install edge-tts")
    sys.exit(1)


def _voice_for(character_name: str, char_map: dict[str, str], default: str) -> str:
    return char_map.get(character_name, default)


async def main():
    script_path = sys.argv[1]
    output_dir = Path(sys.argv[2])
    default_voice = sys.argv[3] if len(sys.argv) > 3 else "zh-CN-XiaoxiaoNeural"
    # Optional 4th argument: JSON mapping of character_name -> voice
    char_voice_json = sys.argv[4] if len(sys.argv) > 4 else "{}"
    char_voice: dict[str, str] = json.loads(char_voice_json)

    data = json.loads(Path(script_path).read_text())
    audio_clips = []
    clip_index = 0

    for scene in data.get("scenes", []):
        sid = scene.get("id", 0)

        # 1. Narration (default voice)
        narration = scene.get("narration", "")
        if narration:
            out_path = output_dir / f"audio_{clip_index:04d}.mp3"
            communicate = edge_tts.Communicate(narration, default_voice)
            await communicate.save(str(out_path))
            audio_clips.append({
                "id": sid, "type": "narration", "character": "",
                "text": narration,
                "path": str(out_path),
                "duration": scene.get("duration_seconds", 5.0),
            })
            clip_index += 1

        # 2. Character dialogues (per-character voice)
        for dial in scene.get("dialogues", []):
            char_name = dial.get("character", "")
            line = dial.get("line", "")
            if not line:
                continue
            out_path = output_dir / f"audio_{clip_index:04d}.mp3"
            voice = _voice_for(char_name, char_voice, default_voice)
            communicate = edge_tts.Communicate(line, voice)
            await communicate.save(str(out_path))
            audio_clips.append({
                "id": sid, "type": "dialogue", "character": char_name,
                "text": f"{char_name}: {line}" if char_name else line,
                "path": str(out_path),
                "duration": 2.5,
            })
            clip_index += 1

    # Save audio manifest
    manifest = output_dir / "audio_manifest.json"
    manifest.write_text(json.dumps(audio_clips, indent=2))
    print(f"Generated {len(audio_clips)} audio clips ({clip_index} total)")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
"""


def _add_narration(
    video_path: Path,
    script: Script,
    temp_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path:
    """Add TTS narration overlay to the video.

    Uses edge-tts if available; otherwise skips TTS.
    The narrator script is generated inline so the project has no hard
    dependency on edge-tts.
    """
    if not shutil.which("edge-tts"):
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            if verbose:
                print(
                    "  edge-tts not available, skipping narration. Install: pip install edge-tts",
                    file=sys.stderr,
                )
            return video_path

    # Save script to temp for the narration sub-process
    script_json = temp_dir / "script_for_tts.json"
    script.save(script_json)

    # Write the narration script
    nar_script = temp_dir / "gen_narration.py"
    nar_script.write_text(_NARRATION_SCRIPT)

    # Run external TTS generation
    audio_dir = temp_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    if script.characters:
        char_voices = {c.name: c.voice or cfg.tts_voice for c in script.characters}
    else:
        char_voices = {}
    char_voices_json = json.dumps(char_voices, ensure_ascii=False)

    if verbose:
        voices_str = ", ".join(f"{k}={v}" for k, v in char_voices.items())
        print(
            f"  Generating narration audio (edge-tts, voice: {cfg.tts_voice}, "
            f"chars: {voices_str or 'none'})...",
            file=sys.stderr,
        )

    result = subprocess.run(
        [
            sys.executable,
            str(nar_script),
            str(script_json),
            str(audio_dir),
            cfg.tts_voice,
            char_voices_json,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if verbose:
            print(
                f"  ⚠ Narration generation failed: {result.stderr.strip()}",
                file=sys.stderr,
            )
        return video_path

    # Check if audio was generated
    audio_manifest = audio_dir / "audio_manifest.json"
    if not audio_manifest.exists():
        if verbose:
            print("  ⚠ No audio manifest generated, skipping narration", file=sys.stderr)
        return video_path

    # Concatenate all audio clips in manifest order (narration + dialogue interleaved)
    manifest_data = json.loads(audio_manifest.read_text())
    if not manifest_data:
        if verbose:
            print("  ⚠ No audio clips in manifest, skipping narration", file=sys.stderr)
        return video_path

    audio_files = [m["path"] for m in manifest_data]
    concat_audio = temp_dir / "audio_concat.mp3"
    concat_list = temp_dir / "audio_list.txt"
    concat_list.write_text("\n".join(f"file '{f}'" for f in audio_files))

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(concat_audio),
        ],
        capture_output=True,
        check=False,
    )

    if not concat_audio.exists():
        if verbose:
            print("  ⚠ Audio concatenation failed, skipping narration", file=sys.stderr)
        return video_path

    # Mix narration with original video audio
    output = video_path
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(concat_audio),
        "-filter_complex",
        "[1:a]volume=1.0[voice];[0:a][voice]amix=inputs=2:duration=first:dropout_transition=2",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output),
    ]

    _run_ffmpeg(cmd, "add narration audio", verbose)

    if output.exists():
        if cfg.add_subtitles:
            output = _burn_subtitles(output, manifest_data, temp_dir, cfg, verbose)
        return output
    return video_path


# ── Subtitles ───────────────────────────────────────────────────────────


def _generate_ass(manifest_data: list[dict], cfg: AgnesConfig) -> str:
    """Generate ASS subtitle content from the audio manifest with styling.

    Uses subtitle font/size/color/position from *cfg*. Font auto-detection
    falls back to _find_cjk_font when cfg.subtitle_font is empty.
    """
    font_path = cfg.subtitle_font or _find_cjk_font()
    font_name = Path(font_path).stem if font_path else "Arial"

    # Map position to ASS alignment values
    # 1=bottom-left, 2=bottom-center, 3=bottom-right,
    # 4=left, 5=center, 6=right,
    # 7=top-left, 8=top-center, 9=top-right
    pos_map = {"bottom": 2, "top": 8, "middle": 5}
    align = pos_map.get(cfg.subtitle_position, 2)

    # ASS header with style definition
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{cfg.subtitle_size},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,1,{align},20,20,20,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    seq = 0
    cursor = 0.0

    def _ts(sec: float) -> str:
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        cs = int((s - int(s)) * 100)
        return f"{int(h):01d}:{int(m):02d}:{int(s):02d}.{cs:02d}"

    for clip in manifest_data:
        text = clip.get("text", "")
        if not text:
            cursor += clip.get("duration", 0.0)
            continue
        dur = clip.get("duration", 2.5)
        seq += 1
        start = cursor
        end = cursor + dur
        # Escape ASS special characters
        safe = text.replace("{", "\\{").replace("}", "\\}")
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{safe}")
        cursor = end

    return "\n".join(lines)


def _generate_srt(manifest_data: list[dict]) -> str:
    """Generate SRT subtitle content from the audio manifest.

    Each manifest entry has "text" and "duration" fields.  Timing is
    derived from cumulative durations of preceding clips.
    """
    lines: list[str] = []
    seq = 0
    cursor = 0.0

    def _ts(sec: float) -> str:
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        cs = int((s - int(s)) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{cs:03d}"

    for clip in manifest_data:
        text = clip.get("text", "")
        if not text:
            cursor += clip.get("duration", 0.0)
            continue
        dur = clip.get("duration", 2.5)
        seq += 1
        start = cursor
        end = cursor + dur
        lines.append(str(seq))
        lines.append(f"{_ts(start)} --> {_ts(end)}")
        lines.append(text)
        lines.append("")
        cursor = end

    return "\n".join(lines)


def _burn_subtitles(
    video_path: Path,
    manifest_data: list[dict],
    temp_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path:
    """Burn subtitles into the video using ASS format for styled output.

    Falls back to the original video if ffmpeg fails or no text found.
    Uses ASS for coloured, positioned subtitles; falls back to SRT if
    the ASS approach fails.
    """
    if not manifest_data:
        return video_path

    ass_content = _generate_ass(manifest_data, cfg)
    if not ass_content.strip():
        return video_path

    sub_path = temp_dir / "subtitles.ass"
    sub_path.write_text(ass_content, encoding="utf-8")

    # Escape path for ffmpeg subtitles filter
    sub_escaped = str(sub_path).replace("\\", "/").replace(":", "\\:")
    if " " in sub_escaped:
        sub_escaped = f"'\\''{sub_escaped}'\\''"

    output = temp_dir / "with_subs.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"subtitles={sub_escaped}",
        "-c:a",
        "copy",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]

    _run_ffmpeg(cmd, "burn subtitles", verbose)

    if output.exists():
        return output
    return video_path


# ── Title / credits cards ──────────────────────────────────────────────


def _find_cjk_font() -> str:
    """Find a system font that supports CJK characters."""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    # Last resort — use whatever font is available (might not render CJK)
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _create_title_card(
    title: str,
    subtitle: str,
    temp_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path | None:
    """Generate a 4-second title card video with black background and centred text."""
    font = _find_cjk_font()
    escaped_title = title.replace(":", "\\:").replace("'", "\\\\'")
    escaped_sub = subtitle.replace(":", "\\:").replace("'", "\\\\'") if subtitle else ""

    output = temp_dir / "title_card.mp4"

    drawtext_title = (
        f"drawtext=text='{escaped_title}':"
        f"fontfile={font}:"
        f"fontsize=48:"
        f"fontcolor=white:"
        f"x=(w-text_w)/2:y=(h-text_h)/2-40:"
        f"box=1:boxcolor=black@0.6:boxborderw=20"
    )

    filters = [drawtext_title]
    if escaped_sub:
        filters.append(
            f"drawtext=text='{escaped_sub}':"
            f"fontfile={font}:"
            f"fontsize=24:"
            f"fontcolor=gray:"
            f"x=(w-text_w)/2:y=(h-text_h)/2+40:"
            f"box=1:boxcolor=black@0.4:boxborderw=10"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={cfg.video_width}x{cfg.video_height}:d=4:r={cfg.target_fps}",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output),
    ]
    _run_ffmpeg(cmd, "generate title card", verbose)
    return output if output.exists() else None


def _create_end_credits(
    script: Script,
    temp_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path | None:
    """Generate a 4-second end-credits card."""
    font = _find_cjk_font()
    lines = [f"导演/编剧: {script.title}"]
    if script.characters:
        for ch in script.characters:
            lines.append(f"角色 {ch.name}: {ch.role or '演员'}")
    lines.append("Powered by Agnes AI")

    # ffmpeg drawtext with multiple lines — use textfile instead
    credits_text = "\n".join(lines)
    textfile = temp_dir / "credits_text.txt"
    textfile.write_text(credits_text, encoding="utf-8")

    output = temp_dir / "end_credits.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={cfg.video_width}x{cfg.video_height}:d=4:r={cfg.target_fps}",
        "-vf",
        (
            f"drawtext=textfile={textfile}:"
            f"fontfile={font}:"
            f"fontsize=28:"
            f"fontcolor=white:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:"
            f"line_spacing=10:"
            f"box=1:boxcolor=black@0.5:boxborderw=15"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output),
    ]
    _run_ffmpeg(cmd, "generate end credits", verbose)
    return output if output.exists() else None


# ── Background music ────────────────────────────────────────────────────


def _add_bgm(
    video_path: Path,
    temp_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path:
    """Layer background music beneath the video's existing audio track.

    The BGM file is looped if shorter than the video, faded in/out,
    and mixed at `cfg.bgm_volume` (default 0.08 ≈ -22 dB relative to
    the original audio).
    """
    bgm_path = Path(cfg.bgm_path)
    if not bgm_path.exists():
        return video_path

    dur = _get_duration(video_path)
    if dur <= 0:
        dur = 30.0

    output = temp_dir / "with_bgm.mp4"

    # ffmpeg: loop BGM, trim, fade, volume adjust
    # If ducking is enabled, use sidechaincompress so BGM dips when narration plays
    if cfg.bgm_ducking:
        # sidechaincompress: first input (bgm) gets compressed when second
        # input (0:a = video+TTS) exceeds the threshold
        filter_complex = (
            f"[1:a]volume={cfg.bgm_volume},"
            f"atrim=duration={dur},"
            f"afade=t=in:d={cfg.bgm_fade_in},"
            f"afade=t=out:st={dur - cfg.bgm_fade_out}:d={cfg.bgm_fade_out}"
            f"[bgm];"
            f"[bgm][0:a]sidechaincompress="
            f"threshold={cfg.bgm_duck_threshold}dB:"
            f"ratio=20:attack=50:release=500:makeup=1"
            f"[bgm_ducked];"
            f"[0:a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2"
        )
    else:
        filter_complex = (
            f"[1:a]"
            f"volume={cfg.bgm_volume},"
            f"atrim=duration={dur},"
            f"afade=t=in:d={cfg.bgm_fade_in},"
            f"afade=t=out:st={dur - cfg.bgm_fade_out}:d={cfg.bgm_fade_out}"
            f"[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path.resolve()),
        "-filter_complex",
        filter_complex,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output),
    ]

    _run_ffmpeg(cmd, "add background music", verbose)

    if output.exists():
        return output
    return video_path


# ── Metadata injection ───────────────────────────────────────────────


def _inject_metadata(
    video_path: Path,
    script: Script,
    temp_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path:
    """Embed generation metadata as MP4 metadata tags."""
    output = temp_dir / "with_metadata.mp4"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    meta = {
        "title": script.title,
        "description": script.description[:200] if script.description else "",
        "encoding_tool": f"AgnesVideoCreator/{cfg.text_model}+{cfg.image_model}+{cfg.video_model}",
        "creation_time": now,
        "artist": "Agnes AI",
        "comment": (
            f"Scenes:{len(script.scenes)} "
            f"Duration:{script.total_duration:.0f}s "
            f"FPS:{cfg.target_fps} "
            f"Episodes:{script.episode}"
        ),
    }

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-c",
        "copy",
    ]
    for key, val in meta.items():
        if val:
            cmd += ["-metadata", f"{key}={val}"]
    cmd.append(str(output))

    _run_ffmpeg(cmd, "inject metadata", verbose)
    return output if output.exists() else video_path


# ── Thumbnail extraction ─────────────────────────────────────────────


def _extract_thumbnail(
    video_path: Path,
    output_dir: Path,
    cfg: AgnesConfig,
    verbose: bool,
) -> Path | None:
    """Extract a representative frame from the video as a JPEG thumbnail."""
    dur = _get_duration(video_path)
    if dur <= 0:
        return None
    ts = dur / 3  # grab frame at 1/3 mark (usually a good composition point)

    thumb_path = output_dir / f"{video_path.stem}_thumb.jpg"
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{ts:.1f}",
        "-i",
        str(video_path),
        "-vframes",
        "1",
        "-q:v",
        "3",
        str(thumb_path),
    ]
    _run_ffmpeg(cmd, "extract thumbnail", verbose)
    return thumb_path if thumb_path.exists() else None


# ── YouTube chapters ──────────────────────────────────────────────────


def _generate_chapters_file(
    script: Script,
    output_dir: Path,
    verbose: bool,
) -> Path | None:
    """Generate a YouTube-compatible chapters.txt sidecar and return its path.

    Chapter boundaries are computed from each scene's duration_seconds.
    """
    if not script.scenes:
        return None

    lines: list[str] = []
    cursor = 0.0
    for scene in script.scenes:
        dur = scene.duration_seconds or 5.0
        mins = int(cursor // 60)
        secs = int(cursor % 60)
        label = scene.narration[:60] if scene.narration else f"Scene {scene.id}"
        lines.append(f"{mins:02d}:{secs:02d} - {label}")
        cursor += dur

    chapters_path = output_dir / f"{script.title[:30]}_chapters.txt"
    chapters_path.write_text("\n".join(lines), encoding="utf-8")
    return chapters_path


def _embed_chapters(
    video_path: Path,
    script: Script,
    temp_dir: Path,
    verbose: bool,
) -> Path:
    """Write MP4 chapter markers based on scene boundaries.

    Uses ffmpeg metadata format: http://ffmpeg.org/ffmpeg-formats.html#Metadata-1
    """
    if not script.scenes:
        return video_path

    output = temp_dir / "with_chapters.mp4"
    meta_path = temp_dir / "chapters_meta.txt"

    # Build ffmetadata chapter entries
    meta_lines = [";FFMETADATA1"]
    cursor_ms = 0
    for scene in script.scenes:
        dur_ms = int((scene.duration_seconds or 5.0) * 1000)
        end_ms = cursor_ms + dur_ms
        label = scene.narration[:60] if scene.narration else f"Scene {scene.id}"
        meta_lines.extend([
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={cursor_ms}",
            f"END={end_ms}",
            f"title={label}",
            "",
        ])
        cursor_ms = end_ms

    meta_path.write_text("\n".join(meta_lines), encoding="utf-8")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(meta_path),
        "-map_metadata",
        "1",
        "-c",
        "copy",
        str(output),
    ]
    _run_ffmpeg(cmd, "embed chapter markers", verbose)

    # Save chapters sidecar for YouTube
    _generate_chapters_file(script, temp_dir, verbose)

    return output if output.exists() else video_path


# ── Aspect-ratio presets ────────────────────────────────────────────

ASPECT_PRESETS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),  # TikTok / Reels vertical
    "1:1": (1080, 1080),  # Instagram square
    "4:3": (1440, 1080),
    "21:9": (2560, 1080),  # ultrawide cinematic
}


def export_crop(
    src: Path,
    dst: Path,
    aspect: str = "16:9",
    verbose: bool = True,
) -> Path:
    """Crop a video to the target aspect ratio using a centered crop.

    Preserves source height, crops width to fit ratio.
    Falls back to source path if target is same as source or if crop fails.
    """
    if dst.exists():
        return dst

    target = ASPECT_PRESETS.get(aspect)
    if not target:
        raise ValueError(f"Unknown aspect ratio '{aspect}'. Supported: {', '.join(ASPECT_PRESETS)}")

    tw, th = target
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        f"crop=(in_h*{tw}/{th}):in_h:(in_w-in_h*{tw}/{th})/2:0",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    _run_ffmpeg(cmd, f"crop to {aspect} ({tw}×{th})", verbose)

    if dst.exists():
        return dst
    return src

"""Video assembler — stiches scene clips into a final video using ffmpeg.

Supports:
  - Concatenating video clips
  - Crossfade / fade transitions between clips
  - Trimming clips to match scene duration
  - Optional TTS narration overlay (via edge-tts / pyttsx3 / espeak)
  - Final encode with consistent settings
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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
            "ffmpeg is required for video assembly. "
            "Install it via your package manager."
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
                    f"  Scene {scene.id}: video URL not downloaded, "
                    f"will use it",
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
            raise SystemExit(
                "No video clips available to assemble. "
                "Run 'render' first."
            )

    if verbose:
        print(
            f"  Assembling {len(clip_paths)} clip(s)...",
            file=sys.stderr,
        )

    # ── Step 1: Normalise all clips with camera motion ─────────
    normalised = []
    for i, clip in enumerate(clip_paths):
        norm_path = temp_dir / f"norm_{i:04d}.mp4"
        camera = script.scenes[i].camera if i < len(script.scenes) else ""
        _normalise_clip(clip, norm_path, cfg, verbose, camera_desc=camera)
        normalised.append(norm_path)

    # ── Step 2: Generate concat file ────────────────────────────
    concat_file = temp_dir / "concat_list.txt"

    if cfg.transition == "fade" and len(normalised) > 1:
        # Use complex filter for crossfade
        final_path = _assemble_with_fades(
            normalised, concat_file, temp_dir, cfg, verbose
        )
    else:
        # Simple concat
        final_path = _assemble_simple(
            normalised, concat_file, temp_dir, cfg, verbose
        )

    # ── Step 3: Add narration if requested ──────────────────────
    if cfg.add_audio:
        final_path = _add_narration(
            final_path, script, temp_dir, cfg, verbose
        )

    # ── Step 4: Copy to final output ────────────────────────────
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
        return f"zoompan=z='min(zoom+0.005,1.3)':d={int(duration * fps)}:s={width}x{height}:fps={fps}"
    if any(k in desc for k in ("zoom out", "pull out", "dolly out", "wide")):
        return f"zoompan=z='max(zoom-0.005,0.8)':d={int(duration * fps)}:s={width}x{height}:fps={fps}"

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
        return f"zoompan=z='min(zoom+0.003,1.2)':d={int(duration * fps)}:s={width}x{height}:fps={fps}"

    # Rotation / Dutch angle
    if any(k in desc for k in ("rotate", "dutch", "tilted")):
        return "rotate=2*PI*t/5:ow=1.2*iw:oh=1.2*ih,crop=iw/1.2:ih/1.2"

    # Aerial / crane
    if any(k in desc for k in ("aerial", "crane", "bird", "overhead")):
        return f"zoompan=z='max(zoom-0.008,0.7)':d={int(duration * fps)}:s={width}x{height}:fps={fps}"

    # Handheld / shaking
    if any(k in desc for k in ("handheld", "shake", "shaky", "camera shake")):
        return "crop=iw-20:ih-20:random(0)*20:random(1)*20"

    # Static / default — no filter
    return ""


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
            camera_desc, 5.0,
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
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
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
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
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
    filter_parts.append(
        f"concat=n={len(clips)}:v=1:a=1[outv][outa]"
    )

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
    offset = 0

    for i in range(1, n):
        # Calculate cumulative duration of previous clips minus transitions
        prev_dur = sum(durations[:i]) - td * i
        filters.append(
            f"[{prev}][{i}:v]"
            f"xfade=transition=fade:duration={td}:offset={prev_dur - td}[v{i}]"
        )
        # Audio crossfade
        filters.append(
            f"[{prev_a}][{i}:a]"
            f"acrossfade=d={td}[a{i}]"
        )
        prev = f"v{i}"
        prev_a = f"a{i}"

    if not filters:
        return _assemble_simple(clips, concat_file, temp_dir, cfg, verbose)

    filter_chain = ";".join(filters)
    final_stream = f"[v{n - 1}][a{n - 1}]"

    cmd += [
        "-filter_complex", filter_chain,
        "-map", f"[v{n - 1}]",
        "-map", f"[a{n - 1}]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output),
    ]

    _run_ffmpeg(cmd, "crossfade assembly", verbose)
    return output


def _get_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        # Default fallback: treat as 5 seconds
        return 5.0


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
                    "  edge-tts not available, skipping narration. "
                    "Install: pip install edge-tts",
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
            f"  Generating narration audio (edge-tts, voice: {cfg.tts_voice}, chars: {voices_str or 'none'})...",
            file=sys.stderr,
        )

    result = subprocess.run(
        [sys.executable, str(nar_script), str(script_json), str(audio_dir), cfg.tts_voice, char_voices_json],
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
    concat_list.write_text(
        "\n".join(f"file '{f}'" for f in audio_files)
    )

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
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
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-i", str(concat_audio),
        "-filter_complex",
        "[1:a]volume=1.0[voice];"
        "[0:a][voice]amix=inputs=2:duration=first:dropout_transition=2",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        str(output),
    ]

    _run_ffmpeg(cmd, "add narration audio", verbose)

    if output.exists():
        if cfg.add_subtitles:
            output = _burn_subtitles(
                output, manifest_data, temp_dir, cfg, verbose
            )
        return output
    return video_path


# ── Subtitles ───────────────────────────────────────────────────────────


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
    """Burn subtitles (SRT) into the video.

    Falls back to the original video if ffmpeg fails or no text found.
    """
    if not manifest_data:
        return video_path

    srt_content = _generate_srt(manifest_data)
    if not srt_content.strip():
        return video_path

    srt_path = temp_dir / "subtitles.srt"
    srt_path.write_text(srt_content, encoding="utf-8")

    # Escape path for ffmpeg subtitles filter (colons and quotes)
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
    # Wrap in single quotes if path contains spaces
    if " " in srt_escaped:
        srt_escaped = f"'\\''{srt_escaped}'\\''"

    output = temp_dir / "with_subs.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"subtitles={srt_escaped}",
        "-c:a", "copy",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]

    _run_ffmpeg(cmd, "burn subtitles", verbose)

    if output.exists():
        return output
    return video_path

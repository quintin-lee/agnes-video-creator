#!/usr/bin/env python3
"""agnes-video — CLI for automatic short-video generation via Agnes AI.

Commands
--------
  init        Generate a script/storyboard from a topic.
  scenes      Generate keyframe images for each scene.
  render      Generate video clips for each scene.
  assemble    Concatenate clips + add transitions + optional narration.
  create      All-in-one: script → images → video → assemble.
  ref-create  Create a video that mimics the style of a reference video.
  status      Show a saved script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agnes_video_creator.assembler import assemble_video
from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.image_generator import generate_scene_images
from agnes_video_creator.models import Script
from agnes_video_creator.reference import analyze_reference_video, generate_reference_script
from agnes_video_creator.script_generator import generate_script
from agnes_video_creator.utils import json_pretty
from agnes_video_creator.video_generator import generate_video_clips


# ── Command implementations ───────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    """Generate a storyboard script from a topic."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    script = generate_script(
        args.topic,
        cfg=cfg,
        style_hint=args.style or "",
        target_duration=args.duration,
        verbose=not args.quiet,
    )

    script_path = _script_path(cfg)
    script.save(script_path)

    if not args.quiet:
        print(f"\nScript saved to: {script_path}", file=sys.stderr)
        print(f"Next: agnes-video scenes {script_path}", file=sys.stderr)


def cmd_scenes(args: argparse.Namespace) -> None:
    """Generate keyframe images for each scene."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    script = _load_script(args.script)
    script = generate_scene_images(script, cfg=cfg, verbose=not args.quiet)

    # Save updated script with image data
    script.save(args.script)
    if not args.quiet:
        print(f"\nUpdated script saved to: {args.script}", file=sys.stderr)
        print(f"Next: agnes-video render {args.script}", file=sys.stderr)


def cmd_render(args: argparse.Namespace) -> None:
    """Generate video clips for each scene."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    script = _load_script(args.script)
    script = generate_video_clips(
        script,
        cfg=cfg,
        mode=_video_mode(args),
        poll=not args.no_poll,
        verbose=not args.quiet,
    )

    script.save(args.script)
    if not args.quiet:
        print(f"\nUpdated script saved to: {args.script}", file=sys.stderr)
        print(f"Next: agnes-video assemble {args.script}", file=sys.stderr)


def cmd_assemble(args: argparse.Namespace) -> None:
    """Assemble video clips into final video."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    script = _load_script(args.script)
    output_path = assemble_video(
        script,
        cfg=cfg,
        output_name=args.output or "",
        verbose=not args.quiet,
    )

    if not args.quiet:
        print(f"\n✓ Video ready: {output_path}", file=sys.stderr)


def cmd_create(args: argparse.Namespace) -> None:
    """End-to-end: script → images → video clips → final assembly."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    # ── Step 1: Script generation ──
    if not args.quiet:
        print("\n=== Step 1/4: Generating script ===", file=sys.stderr)

    script = generate_script(
        args.topic,
        cfg=cfg,
        style_hint=args.style or "",
        target_duration=args.duration,
        verbose=not args.quiet,
    )

    script_path = _script_path(cfg)
    script.save(script_path)
    if not args.quiet:
        print(f"  Script: {script_path}", file=sys.stderr)

    # ── Step 2: Scene images ──
    if not args.skip_images:
        if not args.quiet:
            print("\n=== Step 2/4: Generating scene images ===", file=sys.stderr)
        script = generate_scene_images(script, cfg=cfg, verbose=not args.quiet)
        script.save(script_path)

    # ── Step 3: Video clips ──
    if not args.skip_video:
        if not args.quiet:
            print("\n=== Step 3/4: Generating video clips ===", file=sys.stderr)
        script = generate_video_clips(
            script,
            cfg=cfg,
            mode=_video_mode(args),
            poll=not args.no_poll,
            verbose=not args.quiet,
        )
        script.save(script_path)

    # ── Step 4: Assembly ──
    if not args.skip_assembly:
        if not args.quiet:
            print("\n=== Step 4/4: Assembling final video ===", file=sys.stderr)
        output_path = assemble_video(
            script,
            cfg=cfg,
            output_name=args.output or "",
            verbose=not args.quiet,
        )
        if not args.quiet:
            print(f"\n✓ Final video: {output_path}", file=sys.stderr)


def cmd_ref_create(args: argparse.Namespace) -> None:
    """End-to-end: analyze reference video → generate style-matched script → images → clips → assembly."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    # Override ref config from CLI
    cfg.ref_num_frames = args.ref_frames

    # ── Step 0: Analyze reference video ──
    if not args.quiet:
        print("\n=== Step 0/5: Analyzing reference video ===", file=sys.stderr)

    profile = analyze_reference_video(
        args.reference,
        cfg,
        num_frames=args.ref_frames,
        verbose=not args.quiet,
    )

    # ── Step 1: Script generation with reference style ──
    if not args.quiet:
        print("\n=== Step 1/5: Generating style-matched script ===", file=sys.stderr)

    script = generate_reference_script(
        args.topic,
        profile,
        cfg=cfg,
        target_duration=args.duration,
        verbose=not args.quiet,
    )
    script_path = _script_path(cfg)
    script.save(script_path)
    if not args.quiet:
        print(f"  Script: {script_path}", file=sys.stderr)

    # ── Step 2: Scene images ──
    if not args.skip_images:
        if not args.quiet:
            print("\n=== Step 2/5: Generating scene images ===", file=sys.stderr)
        script = generate_scene_images(script, cfg=cfg, verbose=not args.quiet)
        script.save(script_path)

    # ── Step 3: Video clips ──
    if not args.skip_video:
        if not args.quiet:
            print("\n=== Step 3/5: Generating video clips ===", file=sys.stderr)
        script = generate_video_clips(
            script,
            cfg=cfg,
            mode=_video_mode(args),
            poll=not args.no_poll,
            verbose=not args.quiet,
        )
        script.save(script_path)

    # ── Step 4: Assembly ──
    if not args.skip_assembly:
        if not args.quiet:
            print("\n=== Step 4/5: Assembling final video ===", file=sys.stderr)
        output_path = assemble_video(
            script,
            cfg=cfg,
            output_name=args.output or "",
            verbose=not args.quiet,
        )
        if not args.quiet:
            print(f"\n✓ Final video: {output_path}", file=sys.stderr)


def cmd_status(args: argparse.Namespace) -> None:
    """Display the current status of a saved script."""
    script = _load_script(args.script)

    print(f"Title:      {script.title}")
    print(f"Duration:   {script.total_duration}s")
    print(f"Scenes:     {len(script.scenes)}")
    print(f"Output:     {script.output_dir or '(not set)'}")
    print()

    for scene in script.scenes:
        img_status = "✓" if scene.is_image_ready else "—"
        vid_status = "✓" if scene.is_video_ready else "—"
        print(
            f"  Scene {scene.id}: "
            f"image[{img_status}]  video[{vid_status}]  "
            f"{scene.duration_seconds}s  "
            f"{scene.narration[:60]}..."
        )


# ── Argument parsing ──────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agnes-video",
        description="Automatic short-video generation powered by Agnes AI",
    )
    parser.add_argument("--api-key", help="Agnes API key (default: $AGNES_API_KEY)")
    parser.add_argument("--output-dir", help="Output directory (default: agnes_video_output)")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    parser.add_argument("--verbose", action="store_true", help="Detailed output")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── init ─────────────────────────────────────────────────────
    init = sub.add_parser("init", help="Generate a storyboard from a topic")
    init.add_argument("topic", help="Video topic description")
    init.add_argument("--style", help="Visual style hint (e.g. 'cyberpunk', 'nature doc')")
    init.add_argument("--duration", type=float, default=15.0, help="Target duration in seconds")
    init.set_defaults(func=cmd_init)

    # ── scenes ─────────────────────────────────────────────────────
    scenes = sub.add_parser("scenes", help="Generate keyframe images for each scene")
    scenes.add_argument("script", help="Script JSON file path")
    scenes.set_defaults(func=cmd_scenes)

    # ── render ─────────────────────────────────────────────────────
    render = sub.add_parser("render", help="Generate video clips for each scene")
    render.add_argument("script", help="Script JSON file path")
    render.add_argument("--mode", default="image-to-video",
                        choices=("text-to-video", "image-to-video", "keyframes"),
                        help="Video generation mode (default: image-to-video)")
    render.add_argument("--no-poll", action="store_true",
                        help="Don't poll for completion (just create tasks)")
    render.set_defaults(func=cmd_render)

    # ── assemble ──────────────────────────────────────────────────
    assemble = sub.add_parser("assemble", help="Stitch clips into final video")
    assemble.add_argument("script", help="Script JSON file path")
    assemble.add_argument("--output", "-o", help="Output filename (default: <title>.mp4)")
    assemble.set_defaults(func=cmd_assemble)

    # ── create ────────────────────────────────────────────────────
    create = sub.add_parser("create", help="Full pipeline: script → images → video → assembly")
    create.add_argument("topic", help="Video topic description")
    create.add_argument("--style", help="Visual style hint")
    create.add_argument("--duration", type=float, default=15.0, help="Target duration in seconds")
    create.add_argument("--mode", default="image-to-video",
                        choices=("text-to-video", "image-to-video", "keyframes"),
                        help="Video generation mode (default: image-to-video)")
    create.add_argument("--output", "-o", help="Output video filename")
    create.add_argument("--no-poll", action="store_true", help="Don't poll for video completion")
    create.add_argument("--skip-images", action="store_true", help="Skip image generation step")
    create.add_argument("--skip-video", action="store_true", help="Skip video generation step")
    create.add_argument("--skip-assembly", action="store_true", help="Skip video assembly step")
    create.set_defaults(func=cmd_create)

    # ── ref-create ────────────────────────────────────────────────
    ref = sub.add_parser(
        "ref-create",
        help="Generate a video that mimics a reference video's visual style",
    )
    ref.add_argument("reference", help="Path or URL of the reference video")
    ref.add_argument("topic", help="Description of the new video content")
    ref.add_argument("--style", help="Additional style hint (merged with reference)")
    ref.add_argument("--duration", type=float, default=15.0, help="Target duration in seconds")
    ref.add_argument("--ref-frames", type=int, default=3,
                     help="Number of frames to extract from reference (default: 3)")
    ref.add_argument("--mode", default="image-to-video",
                     choices=("text-to-video", "image-to-video", "keyframes"),
                     help="Video generation mode (default: image-to-video)")
    ref.add_argument("--output", "-o", help="Output video filename")
    ref.add_argument("--no-poll", action="store_true", help="Don't poll for video completion")
    ref.add_argument("--skip-images", action="store_true", help="Skip image generation step")
    ref.add_argument("--skip-video", action="store_true", help="Skip video generation step")
    ref.add_argument("--skip-assembly", action="store_true", help="Skip video assembly step")
    ref.set_defaults(func=cmd_ref_create)

    # ── status ────────────────────────────────────────────────────
    status = sub.add_parser("status", help="Show script/scene status")
    status.add_argument("script", help="Script JSON file path")
    status.set_defaults(func=cmd_status)

    return parser


# ── Helpers ───────────────────────────────────────────────────────────


def _build_cfg(args: argparse.Namespace) -> AgnesConfig:
    """Build AgnesConfig from CLI args + environment defaults."""
    cfg = AgnesConfig.from_env()
    if getattr(args, "api_key", None):
        cfg.api_key = args.api_key
    if getattr(args, "output_dir", None):
        cfg.output_dir = args.output_dir
    return cfg


def _require_key(cfg: AgnesConfig) -> None:
    if not cfg.has_api_key:
        raise SystemExit(
            "AGNES_API_KEY not found. Set the environment variable or "
            "pass --api-key."
        )


def _script_path(cfg: AgnesConfig) -> str:
    """Return a default script path within the output directory."""
    cfg.ensure_dirs()
    return str(cfg.resolved_output / "script.json")


def _load_script(path: str) -> Script:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Script file not found: {path}")
    return Script.load(p)


def _video_mode(args: argparse.Namespace) -> str:
    return getattr(args, "mode", "image-to-video")


# ── Entry point ────────────────────────────────────────────────────────


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except SystemExit as exc:
        if exc.code and str(exc.code) != "0":
            print(f"Error: {exc.code}", file=sys.stderr)
        sys.exit(exc.code if exc.code else 0)


if __name__ == "__main__":
    main()

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

from agnes_video_creator.assembler import assemble_video
from agnes_video_creator.batch import get_queue, get_worker
from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.consistency import check_script_file
from agnes_video_creator.image_generator import generate_scene_images
from agnes_video_creator.models import Script
from agnes_video_creator.novel import novel_to_episodes
from agnes_video_creator.pipeline_state import EpisodeState, PipelineState, SceneState
from agnes_video_creator.project import Project, find_project
from agnes_video_creator.reference import analyze_reference_video, generate_reference_script
from agnes_video_creator.script_generator import generate_script
from agnes_video_creator.video_generator import generate_video_clips
from agnes_video_creator.web_ui import run_server as _run_web_server

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

    # ── Pipeline state (seed or resume) ──
    state_path = cfg.resolved_output / "pipeline_state.json"
    state: PipelineState | None = None
    if args.resume:
        state = PipelineState.load(state_path)

    if state is not None:
        ep = state.episode(1) if state.episodes else None
        script = None
        if ep and ep.has_script:
            script = Script.load(ep.script_path) if Path(ep.script_path).exists() else None
        needs_script = ep is None or ep.status == "pending"
        needs_images = ep is None or not ep.all_images_done
        needs_videos = ep is None or not ep.all_videos_done
        if not args.quiet:
            print(f"  Resuming from: {state_path}", file=sys.stderr)
            print(state.summary(), file=sys.stderr)
    else:
        script = None
        needs_script, needs_images, needs_videos = True, True, True

    step = 0
    total_steps = (
        4
        - int(args.skip_images or (script is not None and not needs_images))
        - int(args.skip_video or (script is not None and not needs_videos))
        - int(args.skip_assembly)
    )

    # ── Step 1: Script generation ──
    step += 1
    if needs_script:
        if not args.quiet:
            print(f"\n=== Step {step}/{total_steps}: Generating script ===", file=sys.stderr)
        script = generate_script(
            args.topic,
            cfg=cfg,
            style_hint=args.style or "",
            target_duration=args.duration,
            verbose=not args.quiet,
        )
        voice_map = _parse_voice_map(getattr(args, "voice_map", None))
        _apply_voice_map(script, voice_map)
        if not args.no_review:
            _review_script(script)
    elif not args.quiet:
        print("\n  ✓ Script loaded from disk, skipping.", file=sys.stderr)

    script_path = _script_path(cfg)
    script.save(script_path)
    if not args.quiet and needs_script:
        print(f"  Script: {script_path}", file=sys.stderr)

    # Persist state after script step
    if state is None:
        state = PipelineState.fresh(
            project_name=args.topic[:60],
            output_dir=str(cfg.resolved_output),
            num_episodes=1,
        )
    ep = state.episode(1) or EpisodeState(episode_number=1)
    ep.status = "script_ready"
    ep.script_path = str(script_path)
    ep.scenes = [SceneState(scene_id=s.id) for s in script.scenes]
    state.upsert_episode(ep)
    state.save(state_path)

    scene_ids: set[int] | None = None
    if args.scene:
        scene_ids = {args.scene}
        if state is not None:
            # Reset scene N's state so it gets regenerated
            for ss in ep.scenes:
                if ss.scene_id == args.scene:
                    ss.image = ""
                    ss.image_url = ""
                    ss.video = ""
                    ss.video_url = ""
            needs_images = True
            needs_videos = True

    # ── Step 2: Scene images ──
    do_images = not args.skip_images and needs_images
    if do_images:
        step += 1
        if not args.quiet:
            print(f"\n=== Step {step}/{total_steps}: Generating scene images ===", file=sys.stderr)
        script = generate_scene_images(script, cfg=cfg, scene_ids=scene_ids, verbose=not args.quiet)
        script.save(script_path)
        # Update per-scene state
        for s in script.scenes:
            ss = next((x for x in ep.scenes if x.scene_id == s.id), None)
            if ss is not None:
                ss.image = "success" if s.is_image_ready else "failed"
                ss.image_url = s.image_url or ""
        ep.status = "images_ready" if ep.all_images_done else "failed"
        state.save(state_path)
    elif args.resume and not needs_images and not args.quiet:
        print("\n  ✓ All scenes already have images, skipping.", file=sys.stderr)

    # ── Step 3: Video clips ──
    do_videos = not args.skip_video and needs_videos
    if do_videos:
        step += 1
        if not args.quiet:
            print(f"\n=== Step {step}/{total_steps}: Generating video clips ===", file=sys.stderr)
        script = generate_video_clips(
            script,
            cfg=cfg,
            mode=_video_mode(args),
            poll=not args.no_poll,
            scene_ids=scene_ids,
            verbose=not args.quiet,
        )
        script.save(script_path)
        for s in script.scenes:
            ss = next((x for x in ep.scenes if x.scene_id == s.id), None)
            if ss is not None:
                ss.video = "success" if s.is_video_ready else "failed"
                ss.video_url = s.video_url or ""
        ep.status = "videos_ready" if ep.all_videos_done else "failed"
        state.save(state_path)
    elif args.resume and not needs_videos and not args.quiet:
        print("\n  ✓ All scenes already have videos, skipping.", file=sys.stderr)

    # ── Step 4: Assembly ──
    if not args.skip_assembly:
        step += 1
        if not args.quiet:
            print(f"\n=== Step {step}/{total_steps}: Assembling final video ===", file=sys.stderr)
        output_path = assemble_video(
            script,
            cfg=cfg,
            output_name=args.output or "",
            verbose=not args.quiet,
        )
        ep.status = "assembled"
        state.save(state_path)
        if not args.quiet:
            print(f"\n✓ Final video: {output_path}", file=sys.stderr)


def cmd_ref_create(args: argparse.Namespace) -> None:
    """End-to-end: analyze reference video → generate style-matched script
    → images → clips → assembly."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    cfg.ref_num_frames = args.ref_frames

    state_path = cfg.resolved_output / "pipeline_state.json"
    state: PipelineState | None = None
    if args.resume:
        state = PipelineState.load(state_path)

    if state is not None:
        ep = state.episode(1) if state.episodes else None
        script = None
        if ep and ep.has_script:
            script = Script.load(ep.script_path) if Path(ep.script_path).exists() else None
        needs_script = ep is None or ep.status == "pending"
        needs_images = ep is None or not ep.all_images_done
        needs_videos = ep is None or not ep.all_videos_done
        if not args.quiet:
            print(f"  Resuming from: {state_path}", file=sys.stderr)
            print(state.summary(), file=sys.stderr)
    else:
        script = None
        needs_script, needs_images, needs_videos = True, True, True

    total_steps = (
        (5 if needs_script else 4)
        - int(args.skip_images or not needs_images)
        - int(args.skip_video or not needs_videos)
        - int(args.skip_assembly)
    )

    step = 0

    # ── Step 0: Analyze reference video ──
    if needs_script:
        step += 1
        if not args.quiet:
            print(
                f"\n=== Step {step}/{total_steps}: Analyzing reference video ===", file=sys.stderr
            )
        profile = analyze_reference_video(
            args.reference,
            cfg,
            num_frames=args.ref_frames,
            verbose=not args.quiet,
        )

    # ── Step 1: Script generation with reference style ──
    if needs_script:
        step += 1
        if not args.quiet:
            print(
                f"\n=== Step {step}/{total_steps}: Generating style-matched script ===",
                file=sys.stderr,
            )
        script = generate_reference_script(
            args.topic,
            profile,
            cfg=cfg,
            target_duration=args.duration,
            verbose=not args.quiet,
        )
        voice_map = _parse_voice_map(getattr(args, "voice_map", None))
        _apply_voice_map(script, voice_map)
    elif not args.quiet:
        print("\n  ✓ Script loaded from disk, skipping.", file=sys.stderr)

    script_path = _script_path(cfg)
    script.save(script_path)
    if not args.quiet and needs_script:
        print(f"  Script: {script_path}", file=sys.stderr)

    # Persist state after script step
    if state is None:
        state = PipelineState.fresh(
            project_name=args.topic[:60],
            output_dir=str(cfg.resolved_output),
            num_episodes=1,
        )
    ep = state.episode(1) or EpisodeState(episode_number=1)
    ep.status = "script_ready"
    ep.script_path = str(script_path)
    ep.scenes = [SceneState(scene_id=s.id) for s in script.scenes]
    state.upsert_episode(ep)
    state.save(state_path)

    scene_ids: set[int] | None = None
    if args.scene:
        scene_ids = {args.scene}
        if state is not None:
            for ss in ep.scenes:
                if ss.scene_id == args.scene:
                    ss.image = ""
                    ss.image_url = ""
                    ss.video = ""
                    ss.video_url = ""
            needs_images = True
            needs_videos = True

    # ── Step 2: Scene images ──
    do_images = not args.skip_images and needs_images
    if do_images:
        step += 1
        if not args.quiet:
            print(f"\n=== Step {step}/{total_steps}: Generating scene images ===", file=sys.stderr)
        script = generate_scene_images(script, cfg=cfg, scene_ids=scene_ids, verbose=not args.quiet)
        script.save(script_path)
        for s in script.scenes:
            ss = next((x for x in ep.scenes if x.scene_id == s.id), None)
            if ss is not None:
                ss.image = "success" if s.is_image_ready else "failed"
                ss.image_url = s.image_url or ""
        ep.status = "images_ready" if ep.all_images_done else "failed"
        state.save(state_path)
    elif args.resume and not needs_images and not args.quiet:
        print("\n  ✓ All scenes already have images, skipping.", file=sys.stderr)

    # ── Step 3: Video clips ──
    do_videos = not args.skip_video and needs_videos
    if do_videos:
        step += 1
        if not args.quiet:
            print(f"\n=== Step {step}/{total_steps}: Generating video clips ===", file=sys.stderr)
        script = generate_video_clips(
            script,
            cfg=cfg,
            mode=_video_mode(args),
            poll=not args.no_poll,
            scene_ids=scene_ids,
            verbose=not args.quiet,
        )
        script.save(script_path)
        for s in script.scenes:
            ss = next((x for x in ep.scenes if x.scene_id == s.id), None)
            if ss is not None:
                ss.video = "success" if s.is_video_ready else "failed"
                ss.video_url = s.video_url or ""
        ep.status = "videos_ready" if ep.all_videos_done else "failed"
        state.save(state_path)
    elif args.resume and not needs_videos and not args.quiet:
        print("\n  ✓ All scenes already have videos, skipping.", file=sys.stderr)

    # ── Step 4: Assembly ──
    if not args.skip_assembly:
        step += 1
        if not args.quiet:
            print(f"\n=== Step {step}/{total_steps}: Assembling final video ===", file=sys.stderr)
        output_path = assemble_video(
            script,
            cfg=cfg,
            output_name=args.output or "",
            verbose=not args.quiet,
        )
        ep.status = "assembled"
        state.save(state_path)
        if not args.quiet:
            print(f"\n✓ Final video: {output_path}", file=sys.stderr)


def cmd_novel(args: argparse.Namespace) -> None:
    """Import a novel text file and generate episode scripts."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    novel_path = Path(args.file)
    if not novel_path.exists():
        raise SystemExit(f"Novel file not found: {args.file}")
    text = novel_path.read_text(encoding="utf-8")

    if not args.quiet:
        print(f"\nReading novel: {novel_path.name} ({len(text)} chars)", file=sys.stderr)

    # Resume check
    state_path = cfg.resolved_output / "pipeline_state.json"
    state = PipelineState.load(state_path)
    resume_episode = 0
    if state is not None:
        for ep in state.episodes:
            if ep.status in ("pending", "failed"):
                resume_episode = ep.episode_number
                break
            resume_episode = ep.episode_number + 1
        total_episodes = len(state.episodes)
        if not args.quiet:
            print(f"  Resuming from episode {resume_episode}", file=sys.stderr)
    else:
        state = PipelineState.fresh(
            project_name=novel_path.stem,
            output_dir=str(cfg.resolved_output),
        )
        total_episodes = args.episodes

    if state is None:
        state = PipelineState.fresh(
            project_name=novel_path.stem,
            output_dir=str(cfg.resolved_output),
            num_episodes=total_episodes,
        )

    scripts = novel_to_episodes(
        text,
        cfg,
        max_episodes=total_episodes,
        resume_from=resume_episode,
        verbose=not args.quiet,
    )

    cfg.ensure_dirs()
    voice_map = _parse_voice_map(getattr(args, "voice_map", None))
    saved = []
    for script in scripts:
        if args.episode and script.episode != args.episode:
            continue

        _apply_voice_map(script, voice_map)
        ep_path = cfg.resolved_output / f"episode_{script.episode:02d}.json"
        script.save(ep_path)
        saved.append(str(ep_path))

        # Update pipeline state
        ep = state.episode(script.episode) or EpisodeState(
            episode_number=script.episode,
        )
        ep.status = "script_ready"
        ep.script_path = str(ep_path)
        ep.scenes = [SceneState(scene_id=s.id) for s in script.scenes]
        state.upsert_episode(ep)

        if not args.quiet:
            print(f"\n  Episode {script.episode} saved: {ep_path}", file=sys.stderr)

    state.save(state_path)

    if not args.quiet:
        print(
            f"\n✓ {len(saved)} episode script(s) saved to {cfg.resolved_output}/", file=sys.stderr
        )
        for p in saved:
            print(f"    {p}", file=sys.stderr)
        print("\nNext: agnes-video project render --episode 1", file=sys.stderr)


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


def cmd_check(args: argparse.Namespace) -> None:
    """Check script(s) for plot and continuity consistency."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    script_paths: list[str] = list(args.script)

    # --project mode: check all episodes
    if hasattr(args, "project_check") and args.project_check:
        proj = find_project()
        if not proj:
            raise SystemExit("No project.json found. --project requires a project directory.")
        project = Project.load(proj)
        ep_paths = [
            e.script_path
            for e in project.episodes
            if e.script_path and Path(e.script_path).exists() and e.status not in ("pending",)
        ]
        if not ep_paths:
            raise SystemExit("No episodes with scripts found in project.")
        script_paths = ep_paths
        if not args.quiet:
            print(f"  Checking {len(script_paths)} episode(s)...", file=sys.stderr)

    if not script_paths:
        raise SystemExit("No scripts specified. Pass script file(s) or use --project.")

    total_critical = 0
    total_warnings = 0

    for path in script_paths:
        if len(script_paths) > 1:
            print(f"\n  ── {Path(path).name} ──", file=sys.stderr)
        report = check_script_file([path], cfg=cfg, verbose=not args.quiet)
        total_critical += report.critical_count
        total_warnings += report.warning_count

    if len(script_paths) > 1:
        sep = "=" * 30
        print(f"\n  {sep}", file=sys.stderr)
        print(f"  Total: {total_critical} critical, {total_warnings} warning(s)", file=sys.stderr)

    if total_critical > 0:
        raise SystemExit(f"✗ {total_critical} critical, {total_warnings} warning(s) found.")


# ── Project commands ──────────────────────────────────────────────────


def cmd_project_init(args: argparse.Namespace) -> None:
    """Create a new project from a novel file."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    project = Project.init(
        args.name,
        novel_path=args.novel,
        style_guide=args.style or "",
        mood=args.mood or "",
        target_audience=args.target or "",
        add_audio=not args.no_audio,
        add_subtitles=not args.no_subtitles,
        video_mode=args.mode,
        parallel=args.parallel,
        max_workers=args.max_workers,
        preview_storyboard=not args.no_storyboard,
    )
    print(f"Project created: {project.root}/", file=sys.stderr)
    if args.novel:
        print(f"  Novel: {args.novel}", file=sys.stderr)
    print("\nNext: agnes-video project analyze", file=sys.stderr)


def cmd_project_analyze(args: argparse.Namespace) -> None:
    """Analyze novel and create episode scripts."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    proj_path = find_project()
    if not proj_path:
        raise SystemExit("No project.json found in current or parent directories.")
    project = Project.load(proj_path)

    if not project.novel_path:
        raise SystemExit("No novel.txt in project — did you run 'project init' with a novel?")
    if not Path(project.novel_path).exists():
        raise SystemExit(f"Novel file not found: {project.novel_path}")

    project.analyze_novel(max_episodes=args.episodes, verbose=not args.quiet)
    print("\n✓ Analysis complete.", file=sys.stderr)
    print("Next: agnes-video project status", file=sys.stderr)


def cmd_project_render(args: argparse.Namespace) -> None:
    """Render one or all episodes."""
    cfg = _build_cfg(args)
    _require_key(cfg)

    proj_path = find_project()
    if not proj_path:
        raise SystemExit("No project.json found in current or parent directories.")
    project = Project.load(proj_path)

    if getattr(args, "parallel", False):
        project.parallel = True
    if getattr(args, "max_workers", 0):
        project.max_workers = args.max_workers
    if hasattr(args, "no_storyboard") and args.no_storyboard:
        project.preview_storyboard = False

    if args.episode:
        project.render_episode(
            args.episode,
            skip_images=args.skip_images,
            skip_video=args.skip_video,
            skip_assembly=args.skip_assembly,
            no_poll=args.no_poll,
            verbose=not args.quiet,
        )
    else:
        project.render_all(
            skip_images=args.skip_images,
            skip_video=args.skip_video,
            skip_assembly=args.skip_assembly,
            no_poll=args.no_poll,
            verbose=not args.quiet,
            parallel=project.parallel,
            max_workers=project.max_workers,
        )

    project.save()
    print(f"\n{project.status_report()}", file=sys.stderr)


def cmd_web(args: argparse.Namespace) -> None:
    """Launch the web UI dashboard."""
    _run_web_server(host=args.host, port=args.port)


def cmd_batch(args: argparse.Namespace) -> None:
    """Manage the batch job queue."""
    q = get_queue()

    if args.batch_command == "submit":
        if args.job_type == "render_all":
            q.submit("render_all", project=args.project)
        elif args.job_type == "analyze":
            q.submit("analyze", project=args.project)
        elif args.job_type == "check":
            q.submit("check", project=args.project, episode_num=args.episode or 0)
        else:
            q.submit(
                "render_episode",
                project=args.project,
                episode_num=args.episode or 0,
            )
        print("  ✓ Job submitted to batch queue.", file=sys.stderr)
        # Ensure the worker is running
        get_worker(q)

    elif args.batch_command == "list":
        items = q.list_jobs(project=args.project or "", limit=args.limit or 50)
        if not items:
            print("  (no jobs)", file=sys.stderr)
            return
        counts = q.count_by_status(project=args.project or "")
        print(f"  Items: {counts}", file=sys.stderr)
        for j in items:
            ep = f" EP{j.episode_num}" if j.episode_num else ""
            print(
                f"  [{j.status:>9}] {j.id}  {j.job_type}{ep}  {j.created_at[:19]}",
                file=sys.stderr,
            )

    elif args.batch_command == "status":
        job = q.get_job(args.job_id)
        if not job:
            raise SystemExit(f"Job '{args.job_id}' not found")
        print(f"  ID:     {job.id}", file=sys.stderr)
        print(f"  Type:   {job.job_type}", file=sys.stderr)
        print(f"  Status: {job.status}", file=sys.stderr)
        print(f"  Error:  {job.error or '—'}", file=sys.stderr)
        print(f"  Created: {job.created_at[:19]}", file=sys.stderr)
        if job.started_at:
            print(f"  Started: {job.started_at[:19]}", file=sys.stderr)
        if job.completed_at:
            print(f"  Done:    {job.completed_at[:19]}", file=sys.stderr)

    elif args.batch_command == "cancel":
        ok = q.cancel(args.job_id)
        if ok:
            print(f"  ✓ Job {args.job_id} cancelled.", file=sys.stderr)
        else:
            print(f"  ⚠ Job {args.job_id} not found or already done.", file=sys.stderr)


def cmd_project_status(args: argparse.Namespace) -> None:
    """Show project status."""
    proj_path = find_project()
    if not proj_path:
        raise SystemExit("No project.json found in current or parent directories.")
    project = Project.load(proj_path)
    print(project.status_report())


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
    render.add_argument(
        "--mode",
        default="image-to-video",
        choices=("text-to-video", "image-to-video", "keyframes"),
        help="Video generation mode (default: image-to-video)",
    )
    render.add_argument(
        "--no-poll", action="store_true", help="Don't poll for completion (just create tasks)"
    )
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
    create.add_argument(
        "--mode",
        default="image-to-video",
        choices=("text-to-video", "image-to-video", "keyframes"),
        help="Video generation mode (default: image-to-video)",
    )
    create.add_argument("--output", "-o", help="Output video filename")
    create.add_argument("--no-poll", action="store_true", help="Don't poll for video completion")
    create.add_argument(
        "--resume",
        "-r",
        action="store_true",
        help="Resume from existing output directory, skipping completed steps",
    )
    create.add_argument(
        "--no-review",
        action="store_true",
        help="Skip the pause-for-review step after script generation",
    )
    create.add_argument(
        "--voice-map",
        help="Per-character voice assignment, JSON or key=value pairs "
        '(e.g. \'{"林黛玉":"zh-CN-XiaoxiaoNeural"}\')',
    )
    create.add_argument(
        "--subtitle-font", help="System font path for subtitles (default: auto-detect CJK)"
    )
    create.add_argument(
        "--subtitle-size", type=int, default=0, help="Subtitle font size (default: 28)"
    )
    create.add_argument("--subtitle-color", help="Subtitle font color (default: white)")
    create.add_argument(
        "--subtitle-position",
        choices=("bottom", "top", "middle"),
        help="Subtitle vertical position (default: bottom)",
    )
    create.add_argument(
        "--scene",
        type=int,
        default=0,
        help="Only regenerate this specific scene ID (requires --resume)",
    )
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
    ref.add_argument(
        "--ref-frames",
        type=int,
        default=3,
        help="Number of frames to extract from reference (default: 3)",
    )
    ref.add_argument(
        "--mode",
        default="image-to-video",
        choices=("text-to-video", "image-to-video", "keyframes"),
        help="Video generation mode (default: image-to-video)",
    )
    ref.add_argument("--output", "-o", help="Output video filename")
    ref.add_argument("--no-poll", action="store_true", help="Don't poll for video completion")
    ref.add_argument(
        "--resume",
        "-r",
        action="store_true",
        help="Resume from existing output directory, skipping completed steps",
    )
    ref.add_argument("--voice-map", help="Per-character voice assignment, JSON or key=value pairs")
    ref.add_argument(
        "--subtitle-font", help="System font path for subtitles (default: auto-detect CJK)"
    )
    ref.add_argument(
        "--subtitle-size", type=int, default=0, help="Subtitle font size (default: 28)"
    )
    ref.add_argument("--subtitle-color", help="Subtitle font color (default: white)")
    ref.add_argument(
        "--subtitle-position",
        choices=("bottom", "top", "middle"),
        help="Subtitle vertical position (default: bottom)",
    )
    ref.add_argument(
        "--scene",
        type=int,
        default=0,
        help="Only regenerate this specific scene ID (requires --resume)",
    )
    ref.add_argument("--skip-images", action="store_true", help="Skip image generation step")
    ref.add_argument("--skip-video", action="store_true", help="Skip video generation step")
    ref.add_argument("--skip-assembly", action="store_true", help="Skip video assembly step")
    ref.set_defaults(func=cmd_ref_create)

    # ── status ────────────────────────────────────────────────────
    status = sub.add_parser("status", help="Show script/scene status")
    status.add_argument("script", help="Script JSON file path")
    status.set_defaults(func=cmd_status)

    # ── check ────────────────────────────────────────────────────
    check = sub.add_parser("check", help="Check script(s) for plot continuity issues")
    check.add_argument("script", nargs="*", default=[], help="Script JSON file path(s)")
    check.add_argument(
        "--project",
        action="store_true",
        dest="project_check",
        help="Check all episodes in the current project",
    )
    check.set_defaults(func=cmd_check)

    # ── novel ─────────────────────────────────────────────────────
    novel = sub.add_parser("novel", help="Import novel text and generate episode scripts")
    novel.add_argument("file", help="Path to the novel text file (.txt)")
    novel.add_argument(
        "--episodes", type=int, default=4, help="Max episodes to generate (default: 4)"
    )
    novel.add_argument(
        "--episode",
        type=int,
        default=0,
        help="Generate only this specific episode number (default: all)",
    )
    novel.add_argument(
        "--voice-map", help="Per-character voice assignment, JSON or key=value pairs"
    )
    novel.set_defaults(func=cmd_novel)

    # ── project ────────────────────────────────────────────────────
    project = sub.add_parser("project", help="Multi-episode project management")
    project_sub = project.add_subparsers(dest="project_command", required=True)

    p_init = project_sub.add_parser("init", help="Create a new project from a novel")
    p_init.add_argument("name", help="Project name (also the output directory name)")
    p_init.add_argument("novel", nargs="?", default="", help="Path to novel text file (.txt)")
    p_init.add_argument("--style", help="Visual style guide")
    p_init.add_argument("--mood", help="Overall mood/tone")
    p_init.add_argument("--target", help="Target audience")
    p_init.add_argument("--no-audio", action="store_true", help="Disable TTS narration")
    p_init.add_argument("--no-subtitles", action="store_true", help="Disable subtitles")
    p_init.add_argument(
        "--mode",
        default="image-to-video",
        choices=("text-to-video", "image-to-video", "keyframes"),
        help="Video mode (default: image-to-video)",
    )
    p_init.add_argument(
        "--parallel",
        "-j",
        action="store_true",
        help="Enable parallel episode rendering (default: sequential)",
    )
    p_init.add_argument(
        "--max-workers", type=int, default=2, help="Max parallel workers (default: 2)"
    )
    p_init.add_argument(
        "--no-storyboard", action="store_true", help="Disable storyboard preview after images"
    )
    p_init.set_defaults(func=cmd_project_init)

    p_status = project_sub.add_parser("status", help="Show project status")
    p_status.set_defaults(func=cmd_project_status)

    p_render = project_sub.add_parser("render", help="Render one or all episodes")
    p_render.add_argument(
        "--episode", type=int, default=0, help="Episode number to render (default: all pending)"
    )
    p_render.add_argument("--no-poll", action="store_true", help="Don't poll for video completion")
    p_render.add_argument("--skip-images", action="store_true", help="Skip image generation")
    p_render.add_argument("--skip-video", action="store_true", help="Skip video generation")
    p_render.add_argument("--skip-assembly", action="store_true", help="Skip assembly")
    p_render.add_argument(
        "--parallel", "-j", action="store_true", help="Render episodes concurrently"
    )
    p_render.add_argument(
        "--max-workers", type=int, default=0, help="Max parallel workers (default: 2)"
    )
    p_render.add_argument(
        "--no-storyboard", action="store_true", help="Skip storyboard preview after images"
    )
    p_render.set_defaults(func=cmd_project_render)

    p_analyze = project_sub.add_parser("analyze", help="Analyze novel and create episode scripts")
    p_analyze.add_argument(
        "--episodes", type=int, default=12, help="Max episodes to generate (default: 12)"
    )
    p_analyze.set_defaults(func=cmd_project_analyze)

    # ── web ─────────────────────────────────────────────────────────
    web = sub.add_parser("web", help="Launch web UI dashboard")
    web.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    web.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    web.set_defaults(func=cmd_web)

    # ── batch ──────────────────────────────────────────────────────
    batch = sub.add_parser("batch", help="Batch job queue management")
    batch_sub = batch.add_subparsers(dest="batch_command", required=True)

    b_submit = batch_sub.add_parser("submit", help="Submit a job to the batch queue")
    b_submit.add_argument(
        "job_type",
        choices=("render_episode", "render_all", "analyze", "check"),
        help="Type of job to submit",
    )
    b_submit.add_argument("--project", default="", help="Project name (default: auto-detect)")
    b_submit.add_argument(
        "--episode", type=int, default=0, help="Episode number (required for render_episode, check)"
    )
    b_submit.set_defaults(func=cmd_batch)

    b_list = batch_sub.add_parser("list", help="List recent batch jobs")
    b_list.add_argument("--project", default="", help="Filter by project name")
    b_list.add_argument("--limit", type=int, default=50, help="Max items to show")
    b_list.set_defaults(func=cmd_batch)

    b_status = batch_sub.add_parser("status", help="Show job status by ID")
    b_status.add_argument("job_id", help="Job ID")
    b_status.set_defaults(func=cmd_batch)

    b_cancel = batch_sub.add_parser("cancel", help="Cancel a pending/running job")
    b_cancel.add_argument("job_id", help="Job ID")
    b_cancel.set_defaults(func=cmd_batch)

    return parser


# ── Helpers ───────────────────────────────────────────────────────────


def _build_cfg(args: argparse.Namespace) -> AgnesConfig:
    """Build AgnesConfig from CLI args + environment defaults."""
    cfg = AgnesConfig.from_env()
    if getattr(args, "api_key", None):
        cfg.api_key = args.api_key
    if getattr(args, "output_dir", None):
        cfg.output_dir = args.output_dir
    if getattr(args, "subtitle_font", None):
        cfg.subtitle_font = args.subtitle_font
    if getattr(args, "subtitle_size", 0):
        cfg.subtitle_size = args.subtitle_size
    if getattr(args, "subtitle_color", None):
        cfg.subtitle_color = args.subtitle_color
    if getattr(args, "subtitle_position", None):
        cfg.subtitle_position = args.subtitle_position
    return cfg


def _require_key(cfg: AgnesConfig) -> None:
    if not cfg.has_api_key:
        raise SystemExit("AGNES_API_KEY not found. Set the environment variable or pass --api-key.")


def _script_path(cfg: AgnesConfig) -> str:
    """Return a default script path within the output directory."""
    cfg.ensure_dirs()
    return str(cfg.resolved_output / "script.json")


def _load_script(path: str) -> Script:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Script file not found: {path}")
    return Script.load(p)


def _review_script(script: Script) -> None:
    """Pause and prompt the user to review the generated script before continuing."""
    print("\n" + "=" * 60, file=sys.stderr)
    print(f"Script generated: {script.title}", file=sys.stderr)
    print(f"  Scenes:    {len(script.scenes)}", file=sys.stderr)
    print(f"  Duration:  {script.total_duration:.1f}s", file=sys.stderr)
    print(f"  Style:     {script.style_guide or '(not set)'}", file=sys.stderr)
    print(f"  Mood:      {script.mood or '(not set)'}", file=sys.stderr)
    print(f"  Target:    {script.target_audience or '(not set)'}", file=sys.stderr)
    if script.characters:
        print(f"  Characters ({len(script.characters)}):")
        for c in script.characters:
            print(f"    - {c.name} ({c.role})", file=sys.stderr)
    for sc in script.scenes[:5]:
        print(f"  Scene {sc.id}: {sc.duration_seconds}s — {sc.narration[:80]}...", file=sys.stderr)
    if len(script.scenes) > 5:
        print(f"  ... and {len(script.scenes) - 5} more scene(s)", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("You can edit the script JSON file before continuing.", file=sys.stderr)
    print("Continue without changes? [Y/n]: ", end="", file=sys.stderr)
    try:
        answer = input().strip().lower()
        if answer in ("n", "no"):
            print("\nEdit the script JSON file, then re-run with --resume.", file=sys.stderr)
            raise SystemExit(0)
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(0) from None


def _video_mode(args: argparse.Namespace) -> str:
    return getattr(args, "mode", "image-to-video")


def _apply_voice_map(script: Script, voice_map: dict[str, str]) -> None:
    """Assign edge-tts voices to characters from a name→voice mapping.

    Mutates script.characters in place — sets the ``voice`` field on
    each Character whose name appears in *voice_map*.
    """
    if not voice_map or not script.characters:
        return
    for ch in script.characters:
        if ch.name in voice_map:
            ch.voice = voice_map[ch.name]


def _parse_voice_map(raw: str | None) -> dict[str, str]:
    """Parse --voice-map into a character-name→voice dict.

    Accepts JSON (``{"林黛玉":"zh-CN-XiaoxiaoNeural"}``) or
    comma-separated key=value pairs (``林黛玉=zh-CN-XiaoxiaoNeural,贾宝玉=zh-CN-YunxiNeural``).
    Returns an empty dict when *raw* is empty.
    """
    if not raw:
        return {}
    raw = raw.strip()
    # Try JSON first
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print("  ⚠ --voice-map JSON parse failed, trying key=value format", file=sys.stderr)
    # Fallback: comma-separated key=value
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


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

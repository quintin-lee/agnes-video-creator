"""Web UI — FastAPI-based dashboard for project management and pipeline control.

Usage
-----
    agnes-video web          # start on http://localhost:8765
    agnes-video web --port 8080 --host 0.0.0.0
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:
    _HAS_FASTAPI = False
else:
    _HAS_FASTAPI = True

from agnes_video_creator.batch import get_queue, get_worker, BatchJob
from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.consistency import check_script_file
from agnes_video_creator.models import Character, Script
from agnes_video_creator.project import Project, EpisodeInfo, find_project


# ── Helpers ─────────────────────────────────────────────────────────────


def _projects_dir() -> Path:
    """Return the directory to scan for projects."""
    env = os.environ.get("AGNES_PROJECTS_DIR")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def _discover_projects() -> list[dict[str, Any]]:
    """Scan for project.json files and return project summaries."""
    results: list[dict[str, Any]] = []
    root = _projects_dir()
    if not root.exists():
        return results

    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        proj_file = entry / "project.json"
        if not proj_file.exists():
            continue
        try:
            data = json.loads(proj_file.read_text())
            episodes = data.get("episodes", [])
            status_counts: dict[str, int] = {}
            for ep in episodes:
                s = ep.get("status", "pending")
                status_counts[s] = status_counts.get(s, 0) + 1
            results.append({
                "name": data.get("name", entry.name),
                "root": str(entry.resolve()),
                "novel_path": data.get("novel_path", ""),
                "episode_count": len(episodes),
                "status_summary": status_counts,
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return results


def _check_installed() -> None:
    if not _HAS_FASTAPI:
        raise SystemExit(
            "fastapi and uvicorn are required for the web UI.\n"
            "Install: pip install fastapi uvicorn"
        )


# ── Log capture infrastructure ──────────────────────────────────────────


class PipelineLog:
    """Thread-safe ring buffer of log lines for a project."""

    def __init__(self, max_lines: int = 5000) -> None:
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._max = max_lines
        self._event = threading.Event()
        self._done = False

    def write(self, text: str) -> None:
        with self._lock:
            self._lines.append(text)
            if len(self._lines) > self._max:
                self._lines = self._lines[-self._max:]
            self._event.set()

    def read_since(self, cursor: int) -> tuple[list[str], int]:
        with self._lock:
            new = self._lines[cursor:]
            return new, len(self._lines)

    def mark_done(self) -> None:
        with self._lock:
            self._done = True
            self._event.set()

    @property
    def is_done(self) -> bool:
        with self._lock:
            return self._done

    def wait(self, timeout: float = 0.5) -> bool:
        return self._event.wait(timeout)

    def reset_event(self) -> None:
        self._event.clear()


class LogCapture:
    """Captures print() stderr output during pipeline runs."""

    def __init__(self, project_name: str) -> None:
        self.project = project_name
        self.log = PipelineLog()
        self._saved_stderr: Any = None

    def __enter__(self) -> PipelineLog:
        self._saved_stderr = sys.stderr
        sys.stderr = _CaptureStream(self._saved_stderr, self.log)
        return self.log

    def __exit__(self, *args: Any) -> None:
        if self._saved_stderr is not None:
            sys.stderr = self._saved_stderr


class _CaptureStream:
    """Wraps a real stderr stream, tee-ing into a PipelineLog."""

    def __init__(self, original: Any, log: PipelineLog) -> None:
        self._original = original
        self._log = log
        self._buf: list[str] = []

    def write(self, text: str) -> None:
        self._original.write(text)
        self._original.flush()
        if text.strip():
            self._buf.append(text)
            if text.endswith("\n"):
                self._log.write("".join(self._buf).rstrip())
                self._buf = []

    def flush(self) -> None:
        self._original.flush()

    @property
    def isatty(self) -> bool:
        return False


# ── In-memory run registry ──────────────────────────────────────────────


_running: dict[str, threading.Thread] = {}
_logs: dict[str, PipelineLog] = {}
_lock = threading.Lock()


def _start_background(name: str, fn: Any, *args: Any) -> bool:
    """Start a background pipeline run for *name* (project or project+ep)."""
    with _lock:
        if name in _running and _running[name].is_alive():
            return False
        log = PipelineLog()
        _logs[name] = log
        t = threading.Thread(target=_run_wrapper, args=(name, fn, log, *args), daemon=True)
        _running[name] = t
        t.start()
        return True


def _run_wrapper(name: str, fn: Any, log: PipelineLog, *args: Any) -> None:
    """Execute the pipeline function with log capture."""
    with LogCapture(name):
        try:
            fn(*args)
        except SystemExit as e:
            log.write(f"⚠ SystemExit: {e.code}")
        except Exception as e:
            log.write(f"✗ Error: {e}")
        finally:
            log.mark_done()
    with _lock:
        _running.pop(name, None)


# ── FastAPI app factory ──────────────────────────────────────────────────

_STATIC = Path(__file__).parent / "web_app"


def create_app() -> FastAPI:
    """Create and return the FastAPI application."""

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        _STATIC.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="Agnes Video Creator", lifespan=_lifespan)

    # ── Serve SPA ──────────────────────────────────────────────────
    if _STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    # ── API: Server status ──────────────────────────────────────────

    @app.get("/api/status")
    def server_status():
        """Return server status including API key configuration."""
        cfg = AgnesConfig()
        return {
            "api_key_configured": cfg.has_api_key,
            "projects_dir": str(_projects_dir().resolve()),
        }

    # ── API: Project list / create ──────────────────────────────────

    @app.get("/api/projects")
    def list_projects():
        """Return all discovered projects with summary info."""
        return {"projects": _discover_projects()}

    @app.post("/api/projects")
    async def create_project(request: Request):
        """Create a new project from a novel file path."""
        from pydantic import BaseModel

        class _Body(BaseModel):
            name: str
            novel_path: str = ""
            style: str = ""
            mood: str = ""
            target: str = ""

        try:
            raw = await request.json()
            body = _Body(**raw)
        except Exception as e:
            raise HTTPException(422, f"Invalid request body: {e}")

        novel = Path(body.novel_path).resolve() if body.novel_path else ""
        if body.novel_path and not Path(body.novel_path).exists():
            raise HTTPException(400, f"Novel file not found: {body.novel_path}")

        root = _projects_dir() / body.name
        if root.exists():
            raise HTTPException(409, f"Project '{body.name}' already exists at {root}")

        project = Project.init(
            body.name,
            novel_path=str(novel) if novel else "",
            root=str(root),
            style_guide=body.style,
            mood=body.mood,
            target_audience=body.target,
            add_audio=True,
            add_subtitles=True,
            video_mode="image-to-video",
        )
        return {"name": project.name, "root": project.root}

    # ── API: Single project detail ──────────────────────────────────

    @app.get("/api/projects/{name:path}")
    def get_project(name: str):
        """Return full project detail including episode statuses."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")
        try:
            project = Project.load(proj_file)
        except Exception as e:
            raise HTTPException(500, f"Failed to load project: {e}")

        has_novel = bool(project.novel_path and Path(project.novel_path).exists())
        with _lock:
            is_running = name in _running and _running[name].is_alive()

        chars = []
        try:
            chars = project.get_characters()
        except Exception:
            pass

        return {
            "name": project.name,
            "root": project.root,
            "novel_path": project.novel_path,
            "has_novel": has_novel,
            "style_guide": project.style_guide,
            "mood": project.mood,
            "target_audience": project.target_audience,
            "parallel": project.parallel,
            "preview_storyboard": project.preview_storyboard,
            "video_mode": project.video_mode,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "is_running": is_running,
            "characters": [
                {"name": c.name, "role": c.role, "appearance": c.appearance or "",
                 "voice": c.voice or "", "portrait_path": c.portrait_path or ""}
                for c in chars
            ],
            "episodes": [
                {
                    "number": e.number,
                    "title": e.title or "",
                    "status": e.status,
                    "script_path": e.script_path,
                    "output_path": e.output_path or "",
                }
                for e in project.episodes
            ],
        }

    # ── API: Analyze novel ──────────────────────────────────────────

    @app.post("/api/projects/{name}/analyze")
    def analyze_project(name: str):
        """Trigger novel analysis in a background thread."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)
        if not project.novel_path or not Path(project.novel_path).exists():
            raise HTTPException(400, "No novel file found in project")

        run_key = f"{name}__analyze"
        if not _start_background(run_key, _do_analyze, project):
            raise HTTPException(409, "Analysis already running")

        return {"status": "started", "project": name}

    def _do_analyze(project: Project) -> None:
        """Run novel analysis."""
        project.analyze_novel(max_episodes=12, verbose=True)

    # ── API: Render ─────────────────────────────────────────────────

    @app.post("/api/projects/{name}/render")
    async def render_project(name: str, request: Request):
        """Trigger rendering in a background thread."""
        try:
            raw = await request.json()
            episode = int(raw.get("episode", 0))
        except Exception:
            episode = 0

        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)

        if episode:
            run_key = f"{name}__render_ep{episode}"
            if not _start_background(run_key, _do_render_one, project, episode):
                raise HTTPException(409, "Render already running for this episode")
        else:
            run_key = f"{name}__render"
            if not _start_background(run_key, _do_render_all, project):
                raise HTTPException(409, "Render already running")

        return {"status": "started", "project": name, "episode": episode or "all"}

    def _do_render_one(project: Project, ep_num: int) -> None:
        project.render_episode(ep_num, verbose=True)
        project.save()

    def _do_render_all(project: Project) -> None:
        project.render_all(verbose=True, parallel=project.parallel, max_workers=project.max_workers)
        project.save()

    # ── API: Episode detail ─────────────────────────────────────────

    @app.get("/api/projects/{name}/episodes/{num}")
    def get_episode(name: str, num: int):
        """Return episode script with per-scene details."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)
        ep_info: EpisodeInfo | None = None
        for ep in project.episodes:
            if ep.number == num:
                ep_info = ep
                break
        if not ep_info:
            raise HTTPException(404, f"Episode {num} not found")

        script = None
        scenes_data = []
        if ep_info.script_path and Path(ep_info.script_path).exists():
            try:
                script = Script.load(ep_info.script_path)
                for s in script.scenes:
                    img_rel = ""
                    if s.image_path:
                        try:
                            img_rel = str(Path(s.image_path).relative_to(root))
                        except ValueError:
                            img_rel = s.image_path
                    vid_rel = ""
                    if s.video_path:
                        try:
                            vid_rel = str(Path(s.video_path).relative_to(root))
                        except ValueError:
                            vid_rel = s.video_path
                    scenes_data.append({
                        "id": s.id,
                        "narration": s.narration,
                        "visual_prompt": s.visual_prompt,
                        "duration_seconds": s.duration_seconds,
                        "camera": s.camera,
                        "style": s.style,
                        "character_appearances": s.character_appearances,
                        "dialogues": s.dialogues,
                        "is_image_ready": s.is_image_ready,
                        "is_video_ready": s.is_video_ready,
                        "image_path": s.image_path or "",
                        "image_rel": img_rel,
                        "video_path": s.video_path or "",
                        "video_rel": vid_rel,
                    })
            except Exception as e:
                raise HTTPException(500, f"Failed to load script: {e}")

        with _lock:
            run_key = f"{name}__render_ep{num}"
            is_running = run_key in _running and _running[run_key].is_alive()
            if not is_running:
                run_key = f"{name}__render"
                is_running = run_key in _running and _running[run_key].is_alive()

        return {
            "number": ep_info.number,
            "title": ep_info.title or "",
            "status": ep_info.status,
            "script_path": ep_info.script_path or "",
            "output_path": ep_info.output_path or "",
            "is_running": is_running,
            "script": {
                "title": script.title if script else "",
                "description": script.description if script else "",
                "total_duration": script.total_duration if script else 0,
                "style_guide": script.style_guide if script else "",
                "scenes": scenes_data,
            } if script else None,
        }

    # ── API: Storyboard HTML ────────────────────────────────────────

    @app.get("/api/projects/{name}/storyboard/{num}")
    def get_storyboard(name: str, num: int):
        """Return the storyboard HTML for an episode."""
        root = _projects_dir() / name
        storyboard_path = root / f"episode_{num:02d}" / "storyboard.html"
        if not storyboard_path.exists():
            # Try to generate it on the fly
            proj_file = root / "project.json"
            if not proj_file.exists():
                raise HTTPException(404, "Storyboard not found")
            project = Project.load(proj_file)
            ep_info = None
            for ep in project.episodes:
                if ep.number == num:
                    ep_info = ep
                    break
            if not ep_info or not ep_info.script_path or not Path(ep_info.script_path).exists():
                raise HTTPException(404, "Episode script not found")
            script = Script.load(ep_info.script_path)
            from agnes_video_creator.storyboard import generate_storyboard_html
            storyboard_path = generate_storyboard_html(script, storyboard_path)

        return FileResponse(str(storyboard_path), media_type="text/html")

    # ── API: Serve scene images ─────────────────────────────────────

    @app.get("/api/projects/{name}/images/{episode_num:path}")
    def get_scene_image(name: str, episode_num: str, file: str = Query(...)):
        """Serve a scene image file."""
        root = _projects_dir() / name
        img_path = root / episode_num / "images" / file
        # Also try the relative path directly
        if not img_path.exists():
            img_path = root / file
        if not img_path.exists() or not img_path.is_file():
            raise HTTPException(404, "Image not found")
        return FileResponse(str(img_path))

    # ── API: Serve scene videos ─────────────────────────────────────

    @app.get("/api/projects/{name}/videos/{file_path:path}")
    def get_scene_video(name: str, file_path: str):
        """Serve a scene video file by project-relative path."""
        root = _projects_dir() / name
        vid_path = root / file_path
        if not vid_path.exists() or not vid_path.is_file():
            raise HTTPException(404, f"Video not found: {file_path}")
        return FileResponse(str(vid_path), media_type="video/mp4")

    # ── API: Script edit (scene-level) ──────────────────────────────

    @app.put("/api/projects/{name}/episodes/{num}/scene/{scene_id}")
    async def update_scene(name: str, num: int, scene_id: int, request: Request):
        """Update a scene's narration, visual_prompt, or duration."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)
        ep_info: EpisodeInfo | None = None
        for ep in project.episodes:
            if ep.number == num:
                ep_info = ep
                break
        if not ep_info or not ep_info.script_path or not Path(ep_info.script_path).exists():
            raise HTTPException(404, f"Episode {num} script not found")

        script = Script.load(ep_info.script_path)
        scene = next((s for s in script.scenes if s.id == scene_id), None)
        if not scene:
            raise HTTPException(404, f"Scene {scene_id} not found")

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid JSON body")

        changed = False
        for field in ("narration", "visual_prompt", "duration_seconds", "camera", "style"):
            if field in body:
                setattr(scene, field, body[field])
                changed = True

        # Also allow editing dialogue lines
        if "dialogues" in body and isinstance(body["dialogues"], list):
            scene.dialogues = body["dialogues"]
            changed = True

        if not changed:
            raise HTTPException(400, "No editable fields provided")

        script.save()
        project.mark_updated()
        project.save()

        return {"status": "ok", "scene_id": scene_id}

    # ── API: Scene trim ─────────────────────────────────────────────

    @app.put("/api/projects/{name}/episodes/{num}/scene/{scene_id}/trim")
    async def trim_scene(name: str, num: int, scene_id: int, request: Request):
        """Set trim in/out points for a scene video clip."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)
        ep_info = next((e for e in project.episodes if e.number == num), None)
        if not ep_info or not ep_info.script_path or not Path(ep_info.script_path).exists():
            raise HTTPException(404, f"Episode {num} script not found")

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid JSON body")

        trim_in = float(body.get("trim_in", 0))
        trim_out = float(body.get("trim_out", 0))
        if trim_in < 0 or trim_out < 0:
            raise HTTPException(400, "trim_in and trim_out must be >= 0")

        script = Script.load(ep_info.script_path)
        scene = next((s for s in script.scenes if s.id == scene_id), None)
        if not scene:
            raise HTTPException(404, f"Scene {scene_id} not found")

        scene.trim_in = trim_in
        scene.trim_out = trim_out
        script.save()
        return {"status": "ok", "scene_id": scene_id, "trim_in": trim_in, "trim_out": trim_out}

    # ── API: Voice-map assignment ───────────────────────────────────

    @app.put("/api/projects/{name}/voice-map")
    async def update_voice_map(name: str, request: Request):
        """Update voice assignments for project characters."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid JSON body")

        voice_map: dict[str, str] = body.get("voice_map", {})
        if not isinstance(voice_map, dict) or not voice_map:
            raise HTTPException(400, "'voice_map' must be a non-empty dict")

        # Update character voice settings via project
        chars = project.get_characters()
        updated = 0
        for c in chars:
            if c.name in voice_map:
                c.voice = voice_map[c.name]
                updated += 1

        if updated == 0:
            raise HTTPException(400, "No matching characters found for voice_map keys")

        project.save()
        return {"status": "ok", "updated": updated}

    # ── API: Character sheets ───────────────────────────────────────

    @app.get("/api/projects/{name}/characters")
    def list_characters(name: str):
        """Get full character sheets for a project."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")
        project = Project.load(proj_file)
        chars = project.get_characters()
        return {
            "characters": [
                {
                    "name": c.name,
                    "role": c.role,
                    "voice": c.voice,
                    "appearance": c.appearance,
                    "personality": c.personality,
                    "age": c.age,
                    "sample_dialogue": c.sample_dialogue,
                    "backstory": c.backstory,
                    "portrait_url": c.portrait_url,
                }
                for c in chars
            ]
        }

    @app.put("/api/projects/{name}/characters")
    async def update_characters(name: str, request: Request):
        """Batch-update character sheets for a project."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid JSON body")

        updates: list[dict] = body.get("characters", [])
        if not updates:
            raise HTTPException(400, "'characters' list is required")

        chars = project.get_characters()
        update_map = {u.get("name", ""): u for u in updates if u.get("name")}
        updated = 0
        for c in chars:
            u = update_map.get(c.name)
            if not u:
                continue
            for field in ("role", "voice", "appearance", "personality", "age",
                          "sample_dialogue", "backstory"):
                if field in u:
                    setattr(c, field, u[field])
            updated += 1

        project.set_characters(chars)
        project.save()
        return {"status": "ok", "updated": updated}

    # ── API: Consistency check ──────────────────────────────────────

    @app.get("/api/projects/{name}/check/{num}")
    def check_episode(name: str, num: int):
        """Run consistency check on an episode script."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)
        ep_info: EpisodeInfo | None = None
        for ep in project.episodes:
            if ep.number == num:
                ep_info = ep
                break
        if not ep_info or not ep_info.script_path or not Path(ep_info.script_path).exists():
            raise HTTPException(404, f"Episode {num} script not found")

        cfg = AgnesConfig()
        if not cfg.has_api_key:
            raise HTTPException(400,
                "AGNES_API_KEY not set. Set the environment variable or pass --api-key.")

        report = check_script_file(ep_info.script_path, cfg=cfg, verbose=False)

        return {
            "episode": num,
            "critical": report.critical_count,
            "warnings": report.warning_count,
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "description": i.description,
                    "location": i.location,
                    "suggestion": i.suggestion,
                }
                for i in report.issues
            ],
            "summary": report.summary,
        }

    # ── API: Project-level consistency check ────────────────────────

    @app.get("/api/projects/{name}/check")
    def check_project_all(name: str):
        """Run consistency check on ALL episodes in a project."""
        root = _projects_dir() / name
        proj_file = root / "project.json"
        if not proj_file.exists():
            raise HTTPException(404, f"Project '{name}' not found")

        project = Project.load(proj_file)
        results: list[dict] = []
        total_critical = 0
        total_warnings = 0

        cfg = AgnesConfig()
        if not cfg.has_api_key:
            raise HTTPException(400,
                "AGNES_API_KEY not set. Set the environment variable or pass --api-key.")

        for ep in project.episodes:
            if not ep.script_path or not Path(ep.script_path).exists():
                continue
            report = check_script_file([ep.script_path], cfg=cfg, verbose=False)
            total_critical += report.critical_count
            total_warnings += report.warning_count
            results.append({
                "episode": ep.number,
                "critical": report.critical_count,
                "warnings": report.warning_count,
                "issues": [
                    {
                        "severity": i.severity,
                        "category": i.category,
                        "description": i.description,
                        "location": i.location,
                        "suggestion": i.suggestion,
                    }
                    for i in report.issues
                ],
                "summary": report.summary,
            })

        return {
            "project": name,
            "total_critical": total_critical,
            "total_warnings": total_warnings,
            "episode_count": len(results),
            "episodes": results,
        }

    # ── API: Log streaming (SSE) ────────────────────────────────────

    @app.get("/api/logs/{name:path}")
    async def stream_logs(name: str):
        """SSE endpoint for real-time pipeline logs."""
        key = name

        # Wait a bit for the log to be registered
        for _ in range(50):
            log = _logs.get(key)
            if log is not None:
                break
            await asyncio.sleep(0.1)
        else:
            # Check if the log key might have __analyze or __render suffix
            for actual_key, log in _logs.items():
                if actual_key.startswith(key):
                    key = actual_key
                    break
            else:
                raise HTTPException(404, "No log stream available")

        async def _generate():
            cursor = 0
            while True:
                lines, cursor = log.read_since(cursor)
                for line in lines:
                    yield f"data: {json.dumps({'text': line, 'done': False})}\n\n"
                if log.is_done:
                    yield f"data: {json.dumps({'text': '', 'done': True})}\n\n"
                    break
                log.wait(0.5)
                log.reset_event()

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── API: Batch job queue ───────────────────────────────────────

    @app.post("/api/batch/submit")
    async def batch_submit(request: Request):
        """Submit a job to the batch queue."""
        try:
            raw = await request.json()
            job_type = raw.get("job_type", "")
            project = raw.get("project", "")
            episode_num = int(raw.get("episode", 0))
        except Exception:
            raise HTTPException(422, "Invalid request body. Requires: job_type, project")

        q = get_queue()
        job = q.submit(job_type, project=project, episode_num=episode_num)
        get_worker(q)
        return {"status": "submitted", "job": job.to_dict()}

    @app.get("/api/batch/jobs")
    def batch_list(project: str = Query(""), limit: int = Query(50)):
        """List recent batch jobs."""
        q = get_queue()
        jobs = q.list_jobs(project=project, limit=limit)
        counts = q.count_by_status(project=project)
        return {
            "jobs": [j.to_dict() for j in jobs],
            "counts": counts,
        }

    @app.get("/api/batch/jobs/{job_id}")
    def batch_get(job_id: str):
        """Get a single job's details."""
        q = get_queue()
        job = q.get_job(job_id)
        if not job:
            raise HTTPException(404, f"Job '{job_id}' not found")
        return {"job": job.to_dict()}

    @app.post("/api/batch/cancel/{job_id}")
    def batch_cancel(job_id: str):
        """Cancel a pending or running job."""
        q = get_queue()
        ok = q.cancel(job_id)
        return {"cancelled": ok}

    # ── SPA catch-all ───────────────────────────────────────────────

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the SPA for all non-API routes."""
        if full_path.startswith("api/"):
            raise HTTPException(404)
        idx = _STATIC / "index.html"
        if not idx.exists():
            return HTMLResponse("<h1>Web UI not built</h1><p>Run the tool to generate the UI.</p>", status_code=200)
        return HTMLResponse(idx.read_text(encoding="utf-8"))

    return app


# ── CLI entry point ─────────────────────────────────────────────────────


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the web UI server."""
    _check_installed()
    import uvicorn

    app = create_app()
    print(f"\n  🌐 Agnes Video Creator Web UI", file=sys.stderr)
    print(f"  ─────────────────────────────", file=sys.stderr)
    print(f"  URL:  http://{host}:{port}", file=sys.stderr)
    print(f"  Quit: Ctrl+C", file=sys.stderr)
    print(file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="warning")

"""Project management — multi-episode short-drama orchestration.

A Project wraps a directory containing novel text, per-episode scripts,
generated assets (images / videos), and the final assembled episodes.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from agnes_video_creator.assembler import assemble_video
from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.image_generator import generate_scene_images
from agnes_video_creator.models import Character, Script
from agnes_video_creator.novel import analyze_novel
from agnes_video_creator.video_generator import generate_video_clips

# ── Episode state machine ──────────────────────────────────────────────

EPISODE_STATES = (
    "pending",
    "script_ready",
    "images_ready",
    "videos_ready",
    "assembled",
)

_NEXT_STATE: dict[str, str | None] = {
    None: "pending",
    "pending": "script_ready",
    "script_ready": "images_ready",
    "images_ready": "videos_ready",
    "videos_ready": "assembled",
    "assembled": None,
}


# ── Data models ─────────────────────────────────────────────────────────


@dataclass
class EpisodeInfo:
    number: int
    title: str = ""
    status: str = "pending"
    script_path: str = ""
    image_dir: str = ""
    video_dir: str = ""
    output_path: str = ""

    def advance(self) -> None:
        nxt = _NEXT_STATE.get(self.status)
        if nxt:
            self.status = nxt


@dataclass
class Project:
    name: str
    root: str  # Path as str for JSON serialisability
    novel_path: str = ""
    created_at: str = ""
    updated_at: str = ""
    characters: list[dict] = field(default_factory=list)
    style_guide: str = ""
    mood: str = ""
    target_audience: str = ""
    episodes: list[EpisodeInfo] = field(default_factory=list)
    add_audio: bool = True
    add_subtitles: bool = True
    video_mode: str = "image-to-video"
    parallel: bool = False  # render episodes concurrently
    max_workers: int = 2  # max parallel threads
    preview_storyboard: bool = True  # pause after images for review

    # ── Class methods ──────────────────────────────────────────────

    @classmethod
    def init(
        cls,
        name: str,
        novel_path: str = "",
        root: str = "",
        *,
        style_guide: str = "",
        mood: str = "",
        target_audience: str = "",
        add_audio: bool = True,
        add_subtitles: bool = True,
        video_mode: str = "image-to-video",
        parallel: bool = False,
        max_workers: int = 2,
        preview_storyboard: bool = True,
    ) -> Project:
        if not root:
            root = name
        root_p = Path(root).resolve()
        root_p.mkdir(parents=True, exist_ok=True)

        now = datetime.now().isoformat(timespec="seconds")
        project = cls(
            name=name,
            root=str(root_p),
            novel_path=str(Path(novel_path).resolve()) if novel_path else "",
            created_at=now,
            updated_at=now,
            style_guide=style_guide,
            mood=mood,
            target_audience=target_audience,
            add_audio=add_audio,
            add_subtitles=add_subtitles,
            video_mode=video_mode,
            parallel=parallel,
            max_workers=max_workers,
            preview_storyboard=preview_storyboard,
        )
        if novel_path:
            src = Path(novel_path).resolve()
            if src.exists():
                dest = root_p / "novel.txt"
                shutil.copy2(str(src), str(dest))
                project.novel_path = str(dest)
        project.save()
        return project

    # ── Persistence ─────────────────────────────────────────────────

    @property
    def root_path(self) -> Path:
        return Path(self.root).resolve()

    def save(self) -> None:
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        data = {
            "name": self.name,
            "root": self.root,
            "novel_path": self.novel_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "characters": self.characters,
            "style_guide": self.style_guide,
            "mood": self.mood,
            "target_audience": self.target_audience,
            "add_audio": self.add_audio,
            "add_subtitles": self.add_subtitles,
            "video_mode": self.video_mode,
            "parallel": self.parallel,
            "max_workers": self.max_workers,
            "preview_storyboard": self.preview_storyboard,
            "episodes": [
                {
                    "number": e.number,
                    "title": e.title,
                    "status": e.status,
                    "script_path": e.script_path,
                    "image_dir": e.image_dir,
                    "video_dir": e.video_dir,
                    "output_path": e.output_path,
                }
                for e in self.episodes
            ],
        }
        path = self.root_path / "project.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> Project:
        data = json.loads(Path(path).read_text())
        episodes = [EpisodeInfo(**e) for e in data.pop("episodes", [])]
        return cls(episodes=episodes, **data)

    def reorder_episodes(self, new_order: list[int]) -> None:
        """Reorder episodes by their numbers.

        new_order must be a permutation of existing episode numbers.
        """
        by_num = {e.number: e for e in self.episodes}
        if set(by_num) != set(new_order):
            raise ValueError("new_order must include all episode numbers exactly once")
        self.episodes = [by_num[n] for n in new_order]

    # ── Character helpers ───────────────────────────────────────────

    def get_characters(self) -> list[Character]:
        return [Character(**c) for c in self.characters]

    def set_characters(self, chars: list[Character]) -> None:
        self.characters = [asdict(c) for c in chars]

    def _build_cfg(self) -> AgnesConfig:
        return AgnesConfig(
            add_audio=self.add_audio,
            add_subtitles=self.add_subtitles,
            bgm_path=os.environ.get("AGNES_BGM_PATH", ""),
        )

    # ── Episode helpers ─────────────────────────────────────────────

    def _ep_dir(self, num: int, *sub: str) -> Path:
        p = self.root_path / f"episode_{num:02d}"
        if sub:
            p = p.joinpath(*sub)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def ensure_episode(self, num: int) -> EpisodeInfo:
        for ep in self.episodes:
            if ep.number == num:
                return ep
        ep = EpisodeInfo(number=num)
        self.episodes.append(ep)
        self.save()
        return ep

    def get_script_objects(self) -> list[tuple[int, Script | None]]:
        """Load all episode scripts that exist on disk."""
        results: list[tuple[int, Script | None]] = []
        for ep in self.episodes:
            sp = Path(ep.script_path)
            if sp.exists():
                results.append((ep.number, Script.load(sp)))
            else:
                results.append((ep.number, None))
        return results

    # ── Novel analysis ──────────────────────────────────────────────

    def analyze_novel(
        self,
        max_episodes: int = 12,
        *,
        verbose: bool = True,
    ) -> list[Script]:
        """Analyse the novel and create initial episode scripts."""
        if not self.novel_path or not Path(self.novel_path).exists():
            raise SystemExit(f"Novel file not found: {self.novel_path}")

        text = Path(self.novel_path).read_text(encoding="utf-8")
        if verbose:
            print(f"Analyzing novel ({len(text)} chars)...", file=sys.stderr)

        cfg = self._build_cfg()
        title, chars, episode_list, remaining = analyze_novel(text, cfg, verbose=verbose)
        self.set_characters(chars)
        if not self.style_guide:
            self.style_guide = ""
        self.save()

        from agnes_video_creator.novel import generate_episode_script

        scripts: list[Script] = []
        for i, ep in enumerate(episode_list):
            if i >= max_episodes:
                break
            ep_num = ep["number"]
            if verbose:
                print(
                    f"\n  Episode {ep_num}/{min(len(episode_list), max_episodes)}...",
                    file=sys.stderr,
                )

            ep_script = generate_episode_script(
                title,
                ep,
                chars,
                text,
                cfg,
                verbose=verbose,
            )
            ep_dir = self._ep_dir(ep_num)
            sp = ep_dir / "script.json"
            ep_script.save(sp)

            ep_info = self.ensure_episode(ep_num)
            ep_info.title = ep_script.title
            ep_info.script_path = str(sp)
            ep_info.image_dir = str(ep_dir / "images")
            ep_info.video_dir = str(ep_dir / "videos")
            ep_info.output_path = str(ep_dir / "final.mp4")
            ep_info.advance()  # pending → script_ready
            scripts.append(ep_script)
            self.save()

        return scripts

    # ── Pipeline ────────────────────────────────────────────────────

    def render_episode(
        self,
        num: int,
        *,
        skip_images: bool = False,
        skip_video: bool = False,
        skip_assembly: bool = False,
        no_poll: bool = False,
        verbose: bool = True,
        _lock: threading.Lock | None = None,
    ) -> None:
        """Run the full pipeline (or resume) for a single episode.

        _lock is an optional threading.Lock for synchronising stderr
        output when called from parallel workers.
        """
        ep = self.ensure_episode(num)
        sp = Path(ep.script_path)
        if not sp.exists():
            if verbose:
                print(f"  Episode {num}: script not found, skipping.", file=sys.stderr)
            return

        script = Script.load(sp)
        cfg = self._build_cfg()
        script.output_dir = str(self.root_path)

        def _log(msg: str) -> None:
            if not verbose:
                return
            if _lock:
                _lock.acquire()
            try:
                print(msg, file=sys.stderr)
            finally:
                if _lock:
                    _lock.release()

        # ── Helpers: scene locking ──
        def _unfinished_scene_ids(check_attr: str) -> set[int]:
            """Return IDs of unlocked scenes missing *check_attr* readiness."""
            ids: set[int] = set()
            for s in script.scenes:
                if s.locked:
                    continue
                ready = getattr(s, check_attr)()
                if not ready:
                    ids.add(s.id)
            return ids

        # ── Images ──
        if not skip_images and ep.status in ("script_ready", "pending", "images_ready"):
            need_ids = _unfinished_scene_ids("is_image_ready")
            if need_ids:
                _log(
                    f"\n  [{num}] Generating images ({len(need_ids)} scenes, "
                    f"{len(script.scenes) - len(need_ids)} locked/ready skipped)..."
                )
                script = generate_scene_images(script, cfg=cfg, scene_ids=need_ids, verbose=verbose)
                ep.advance()
            else:
                ep.advance()

            if self.preview_storyboard:
                ep_dir = self._ep_dir(num)
                storyboard_html = ep_dir / "storyboard.html"
                from agnes_video_creator.storyboard import (  # noqa: PLC0415
                    generate_storyboard_html,
                )

                generate_storyboard_html(script, storyboard_html)
                _log(f"\n  [{num}] 📋 Storyboard: {storyboard_html}")
        elif ep.status == "images_ready":
            pass
        else:
            ep.advance()

        script.save(sp)
        self.save()

        # ── Videos ──
        if not skip_video and ep.status in ("images_ready", "script_ready", "videos_ready"):
            need_video_ids = _unfinished_scene_ids("is_video_ready")
            if need_video_ids:
                _log(
                    f"\n  [{num}] Generating videos ({len(need_video_ids)} scenes, "
                    f"{len(script.scenes) - len(need_video_ids)} locked/ready skipped)..."
                )
                script = generate_video_clips(
                    script,
                    cfg=cfg,
                    mode=self.video_mode,
                    poll=not no_poll,
                    scene_ids=need_video_ids,
                    verbose=verbose,
                )
                ep.advance()
            else:
                ep.advance()
        elif ep.status == "videos_ready":
            pass
        else:
            ep.advance()

        script.save(sp)
        self.save()

        # ── Assembly ──
        if not skip_assembly and ep.status in ("videos_ready", "images_ready", "assembled"):
            do_assemble = ep.status != "assembled"
            if do_assemble:
                if verbose:
                    print(f"\n  [{num}] Assembling video...", file=sys.stderr)
                output_path = assemble_video(
                    script,
                    cfg=cfg,
                    output_name=ep.output_path,
                    verbose=verbose,
                )
                ep.output_path = str(output_path)
                ep.advance()
            else:
                ep.advance()

        script.save(sp)
        self.save()

        if verbose:
            print(f"\n  ✓ Episode {num}: {ep.status}", file=sys.stderr)

    def render_all(
        self,
        *,
        skip_images: bool = False,
        skip_video: bool = False,
        skip_assembly: bool = False,
        no_poll: bool = False,
        verbose: bool = True,
        parallel: bool | None = None,
        max_workers: int | None = None,
    ) -> None:
        """Render episodes, optionally in parallel.

        Parameters
        ----------
        parallel : bool or None
            Override the project's parallel setting.  None = use project default.
        max_workers : int or None
            Override worker count.  None = use project default.
        """
        if parallel is None:
            parallel = self.parallel
        if max_workers is None:
            max_workers = self.max_workers

        pending = [ep for ep in self.episodes if ep.status != "assembled"]
        if not pending:
            if verbose:
                print("  All episodes already assembled.", file=sys.stderr)
            return

        # Already-assembled episodes
        done_count = len(self.episodes) - len(pending)
        if verbose and done_count:
            print(f"  {done_count} episode(s) already done, skipping.", file=sys.stderr)
        if verbose:
            print(f"  Rendering {len(pending)} episode(s)...", file=sys.stderr)

        self._build_cfg()

        if parallel and len(pending) > 1:
            self._render_parallel(
                pending,
                skip_images=skip_images,
                skip_video=skip_video,
                skip_assembly=skip_assembly,
                no_poll=no_poll,
                max_workers=max_workers,
                verbose=verbose,
            )
        else:
            self._render_sequential(
                pending,
                skip_images=skip_images,
                skip_video=skip_video,
                skip_assembly=skip_assembly,
                no_poll=no_poll,
                verbose=verbose,
            )

    def _render_sequential(
        self,
        episodes: list[EpisodeInfo],
        *,
        skip_images: bool = False,
        skip_video: bool = False,
        skip_assembly: bool = False,
        no_poll: bool = False,
        verbose: bool = True,
    ) -> None:
        """Render episodes one at a time."""
        for ep in episodes:
            self.render_episode(
                ep.number,
                skip_images=skip_images,
                skip_video=skip_video,
                skip_assembly=skip_assembly,
                no_poll=no_poll,
                verbose=verbose,
            )

    def _render_parallel(
        self,
        episodes: list[EpisodeInfo],
        *,
        skip_images: bool = False,
        skip_video: bool = False,
        skip_assembly: bool = False,
        no_poll: bool = False,
        max_workers: int = 2,
        verbose: bool = True,
    ) -> None:
        """Render multiple episodes concurrently.

        A threading.Lock protects stderr output from interleaving.
        Per-episode API calls (image + video gen) are the bottleneck,
        so parallel CPU work is negligible.
        """
        lock = threading.Lock()

        def _worker(ep_num: int) -> int:
            """Render one episode; return its number on success."""
            self.render_episode(
                ep_num,
                skip_images=skip_images,
                skip_video=skip_video,
                skip_assembly=skip_assembly,
                no_poll=no_poll,
                verbose=verbose,
                _lock=lock,
            )
            return ep_num

        # Only parallelise episodes that need actual work
        todo = [ep.number for ep in episodes]

        if verbose:
            with lock:
                print(
                    f"  Parallel rendering {len(todo)} episode(s) with {max_workers} worker(s)...",
                    file=sys.stderr,
                )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_worker, num): num for num in todo}
            for future in as_completed(futures):
                num = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    if verbose:
                        with lock:
                            print(
                                f"  ✗ Episode {num} failed: {exc}",
                                file=sys.stderr,
                            )

    # ── Status ──────────────────────────────────────────────────────

    def status_report(self) -> str:
        lines = [
            f"Project:  {self.name}",
            f"Root:     {self.root}",
            f"Novel:    {self.novel_path or '(none)'}",
            f"Episodes: {len(self.episodes)}",
            f"Created:  {self.created_at}",
            f"Updated:  {self.updated_at}",
            "",
            "Episodes:",
        ]
        if not self.episodes:
            lines.append("  (none — run analyze first)")
        for ep in self.episodes:
            sp = Path(ep.script_path)
            sc = len(sp.read_text()) // 1000 if sp.exists() else 0
            sc_label = f"~{sc}KB" if sp.exists() else ""
            icon = {
                "pending": "○",
                "script_ready": "S",
                "images_ready": "I",
                "videos_ready": "V",
                "assembled": "✓",
            }.get(ep.status, "?")
            lines.append(
                f"  [{icon}] ep{ep.number:02d}  {ep.title or '(untitled)'}  {ep.status}  {sc_label}"
            )
        return "\n".join(lines)


def find_project(start: str | Path = ".") -> Path | None:
    """Walk up directories looking for a project.json."""
    p = Path(start).resolve()
    for ancestor in [p] + list(p.parents):
        candidate = ancestor / "project.json"
        if candidate.exists():
            return candidate
    return None

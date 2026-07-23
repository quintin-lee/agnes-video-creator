"""Batch job queue — persistent SQLite-backed async job processing.

Usage
-----
    from agnes_video_creator.batch import get_queue

    q = get_queue()
    job = q.submit("render_episode", project_name="my-drama", episode_num=2)
    # worker pool picks it up automatically
    q.list_jobs()  # [(job_id, status, ...), ...]
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Job types ─────────────────────────────────────────────────────────

JOB_TYPES = (
    "render_episode",
    "render_all",
    "analyze",
    "check",
)

JOB_STATUSES = ("pending", "running", "completed", "failed", "cancelled")


# ── Data model ────────────────────────────────────────────────────────


@dataclass
class BatchJob:
    """A single batch job."""

    id: str
    job_type: str
    project: str
    status: str = "pending"
    episode_num: int = 0
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_type": self.job_type,
            "project": self.project,
            "status": self.status,
            "episode_num": self.episode_num,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# ── Persistent queue ──────────────────────────────────────────────────


class BatchQueue:
    """SQLite-backed persistent job queue.

    Thread-safe.  ``_db_path`` defaults to ``~/.agnes-video/batch.db``.
    """

    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path or str(Path.home() / ".agnes-video" / "batch.db")
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    # ── connection management ──────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id          TEXT PRIMARY KEY,
                    job_type    TEXT NOT NULL,
                    project     TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    episode_num INTEGER NOT NULL DEFAULT 0,
                    error       TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL,
                    started_at  TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status)
            """)
            conn.commit()

    # ── public API ─────────────────────────────────────────────────

    def submit(
        self,
        job_type: str,
        project: str,
        episode_num: int = 0,
    ) -> BatchJob:
        """Submit a new job and return it."""
        if job_type not in JOB_TYPES:
            raise ValueError(f"Unknown job type '{job_type}'. Valid: {JOB_TYPES}")

        job = BatchJob(
            id=str(uuid.uuid4())[:12],
            job_type=job_type,
            project=project,
            episode_num=episode_num,
            status="pending",
            created_at=_now(),
        )
        with self._lock:
            conn = self._conn()
            conn.execute(
                """INSERT INTO jobs (id, job_type, project, status, episode_num,
                                     created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (job.id, job.job_type, job.project, job.status, job.episode_num, job.created_at),
            )
            conn.commit()
        return job

    def claim_pending(self) -> BatchJob | None:
        """Atomically claim one pending job (FIFO)."""
        with self._lock:
            conn = self._conn()
            row = conn.execute(
                """SELECT * FROM jobs
                   WHERE status = 'pending'
                   ORDER BY created_at ASC
                   LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            now = _now()
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            conn.commit()
            job = self._row_to_job(row)
            job.status = "running"
            job.started_at = now
            return job

    def complete(self, job_id: str, *, error: str = "") -> None:
        """Mark a job as completed or failed."""
        status = "failed" if error else "completed"
        with self._lock:
            conn = self._conn()
            conn.execute(
                """UPDATE jobs
                   SET status = ?, error = ?, completed_at = ?
                   WHERE id = ?""",
                (status, error, _now(), job_id),
            )
            conn.commit()

    def cancel(self, job_id: str) -> bool:
        """Cancel a pending/running job.  Returns True if cancelled."""
        with self._lock:
            conn = self._conn()
            cur = conn.execute(
                """UPDATE jobs SET status = 'cancelled', completed_at = ?
                   WHERE id = ? AND status IN ('pending', 'running')""",
                (_now(), job_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def retry(self, job_id: str) -> BatchJob | None:
        """Re-submit a failed/cancelled job. Returns the new job or None."""
        with self._lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND status IN ('failed', 'cancelled')",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            new_job = BatchJob(
                id=str(uuid.uuid4())[:12],
                job_type=row["job_type"],
                project=row["project"],
                episode_num=row["episode_num"],
                status="pending",
                created_at=_now(),
            )
            conn.execute(
                """INSERT INTO jobs (id, job_type, project, status, episode_num, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_job.id, new_job.job_type, new_job.project, new_job.status,
                 new_job.episode_num, new_job.created_at),
            )
            conn.commit()
        return new_job

    def list_jobs(
        self,
        *,
        project: str = "",
        job_type: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[BatchJob]:
        """List recent jobs, newest first. Supports optional filters."""
        with self._lock:
            conn = self._conn()
            clauses: list[str] = []
            params: list[str] = []
            if project:
                clauses.append("project = ?")
                params.append(project)
            if job_type:
                clauses.append("job_type = ?")
                params.append(job_type)
            if status:
                clauses.append("status = ?")
                params.append(status)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM jobs{where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def get_job(self, job_id: str) -> BatchJob | None:
        """Get a single job by ID."""
        with self._lock:
            row = self._conn().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None

    def count_by_status(self, project: str = "") -> dict[str, int]:
        """Return counts grouped by status."""
        with self._lock:
            conn = self._conn()
            if project:
                rows = conn.execute(
                    """SELECT status, COUNT(*) as cnt FROM jobs
                       WHERE project = ? GROUP BY status""",
                    (project,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT status, COUNT(*) as cnt FROM jobs
                       GROUP BY status"""
                ).fetchall()
            return {r["status"]: r["cnt"] for r in rows}

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> BatchJob:
        return BatchJob(
            id=row["id"],
            job_type=row["job_type"],
            project=row["project"],
            status=row["status"],
            episode_num=row["episode_num"],
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )


# ── Background worker ─────────────────────────────────────────────────


class BatchWorker:
    """Background worker pool that consumes from a BatchQueue."""

    def __init__(
        self,
        queue: BatchQueue,
        *,
        max_workers: int = 2,
        poll_interval: float = 2.0,
    ) -> None:
        self._queue = queue
        self._max_workers = max_workers
        self._poll_interval = poll_interval
        self._executor: ThreadPoolExecutor | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    @staticmethod
    def _resolve_project_root(project_name: str) -> Path | None:
        """Locate a project.json by name, searching multiple locations.

        Returns the path to project.json or None.
        """
        # 1) AGNES_PROJECTS_DIR env var
        env_dir = os.environ.get("AGNES_PROJECTS_DIR")
        if env_dir:
            candidate = Path(env_dir) / project_name / "project.json"
            if candidate.exists():
                return candidate

        # 2) ~/.agnes-video/projects/{name}/
        candidate = Path.home() / ".agnes-video" / "projects" / project_name / "project.json"
        if candidate.exists():
            return candidate

        # 3) CWD upward via find_project
        try:
            from agnes_video_creator.project import find_project

            found = find_project()
            if found:
                # Verify the project name matches (if given)
                import json

                data = json.loads(found.read_text())
                if not project_name or data.get("name") == project_name:
                    return found
        except Exception:
            pass

        # 4) CWD / project.json direct
        candidate = Path.cwd() / "project.json"
        if candidate.exists():
            return candidate

        return None

    def start(self) -> None:
        """Start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self, wait: bool = True) -> None:
        """Stop the worker pool."""
        self._running = False
        if self._executor:
            self._executor.shutdown(wait=wait)
            self._executor = None

    def _poll_loop(self) -> None:
        while self._running:
            try:
                job = self._queue.claim_pending()
                if job is not None:
                    self._executor.submit(self._execute_job, job)
                    # Don't wait — let the executor manage it
                else:
                    time.sleep(self._poll_interval)
            except Exception:
                time.sleep(self._poll_interval)

    def _execute_job(self, job: BatchJob) -> None:
        """Execute a single job (runs in worker thread)."""
        from agnes_video_creator.project import Project

        try:
            proj_root = self._resolve_project_root(job.project)
            if proj_root is None or not proj_root.exists():
                raise FileNotFoundError(
                    f"Project '{job.project}' not found. "
                    f"Searched: ~/.agnes-video/projects/, "
                    f"CWD + parents, $AGNES_PROJECTS_DIR"
                )
            project = Project.load(proj_root)

            if job.job_type == "render_episode" and job.episode_num:
                project.render_episode(job.episode_num, verbose=False)
                project.save()
            elif job.job_type == "render_all":
                project.render_all(
                    verbose=False,
                    parallel=project.parallel,
                    max_workers=project.max_workers,
                )
                project.save()
            elif job.job_type == "analyze":
                project.analyze_novel(max_episodes=12, verbose=False)
                project.save()
            elif job.job_type == "check":
                from agnes_video_creator.config import AgnesConfig
                from agnes_video_creator.consistency import check_script_file

                cfg = AgnesConfig()
                if job.episode_num == 0:
                    # episode_num=0 means check all episodes
                    for ep in project.episodes:
                        if ep.script_path and Path(ep.script_path).exists():
                            check_script_file(ep.script_path, cfg=cfg, verbose=False)
                elif job.episode_num:
                    ep_info = next(
                        (e for e in project.episodes if e.number == job.episode_num),
                        None,
                    )
                    if ep_info and ep_info.script_path:
                        check_script_file(ep_info.script_path, cfg=cfg, verbose=False)

            self._queue.complete(job.id)
        except Exception as e:
            self._queue.complete(job.id, error=str(e))


# ── Global singleton ──────────────────────────────────────────────────

_queue: BatchQueue | None = None
_worker: BatchWorker | None = None
_worker_lock = threading.Lock()


def get_queue(db_path: str = "") -> BatchQueue:
    """Return the global BatchQueue singleton."""
    global _queue
    if _queue is None:
        _queue = BatchQueue(db_path=db_path)
    return _queue


def get_worker(
    queue: BatchQueue | None = None,
    *,
    max_workers: int = 2,
) -> BatchWorker:
    """Return the global BatchWorker singleton (starts it if needed)."""
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                q = queue or get_queue()
                _worker = BatchWorker(q, max_workers=max_workers)
                _worker.start()
    return _worker


def stop_worker() -> None:
    """Stop the global worker pool."""
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

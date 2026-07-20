"""Tests for batch job queue — BatchJob dataclass + BatchQueue CRUD with temp SQLite."""

from __future__ import annotations

from pathlib import Path

from agnes_video_creator.batch import BatchJob, BatchQueue


class TestBatchJob:
    def test_defaults(self) -> None:
        job = BatchJob(id="abc", job_type="render_episode", project="test")
        assert job.status == "pending"
        assert job.episode_num == 0
        assert job.error == ""
        assert job.created_at == ""
        assert job.started_at == ""
        assert job.completed_at == ""

    def test_is_terminal_completed(self) -> None:
        job = BatchJob(id="a", job_type="render_episode", project="t", status="completed")
        assert job.is_terminal

    def test_is_terminal_failed(self) -> None:
        job = BatchJob(id="a", job_type="render_episode", project="t", status="failed")
        assert job.is_terminal

    def test_is_terminal_cancelled(self) -> None:
        job = BatchJob(id="a", job_type="render_episode", project="t", status="cancelled")
        assert job.is_terminal

    def test_is_not_terminal_pending(self) -> None:
        job = BatchJob(id="a", job_type="render_episode", project="t", status="pending")
        assert not job.is_terminal

    def test_is_not_terminal_running(self) -> None:
        job = BatchJob(id="a", job_type="render_episode", project="t", status="running")
        assert not job.is_terminal

    def test_to_dict(self) -> None:
        job = BatchJob(
            id="abc", job_type="check", project="my-drama",
            status="running", episode_num=2,
        )
        d = job.to_dict()
        assert d["id"] == "abc"
        assert d["job_type"] == "check"
        assert d["project"] == "my-drama"
        assert d["status"] == "running"
        assert d["episode_num"] == 2


class TestBatchQueue:
    def _make_queue(self, tmp_path: Path) -> BatchQueue:
        return BatchQueue(db_path=str(tmp_path / "test_batch.db"))

    def test_submit_job(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        job = q.submit("render_episode", "my-drama", episode_num=1)
        assert job.job_type == "render_episode"
        assert job.project == "my-drama"
        assert job.episode_num == 1
        assert job.status == "pending"
        assert job.id != ""

    def test_submit_invalid_type(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        with pytest.raises(ValueError, match="Unknown job type"):
            q.submit("invalid_job", "test")

    def test_claim_pending_fifo(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        first = q.submit("analyze", "p1")
        second = q.submit("check", "p2")

        claimed = q.claim_pending()
        assert claimed is not None
        assert claimed.id == first.id
        assert claimed.status == "running"
        assert claimed.started_at != ""

    def test_claim_pending_returns_none_when_empty(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        assert q.claim_pending() is None

    def test_claim_pending_skips_running(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        job = q.submit("analyze", "test")
        q.claim_pending()
        assert q.claim_pending() is None  # only one pending, now running

    def test_complete_job(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        job = q.submit("render_all", "test")
        q.claim_pending()
        q.complete(job.id)
        stored = q.get_job(job.id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.completed_at != ""

    def test_complete_job_with_error(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        job = q.submit("analyze", "test")
        q.claim_pending()
        q.complete(job.id, error="API timeout")
        stored = q.get_job(job.id)
        assert stored.status == "failed"
        assert stored.error == "API timeout"

    def test_cancel_pending_job(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        job = q.submit("analyze", "test")
        cancelled = q.cancel(job.id)
        assert cancelled
        stored = q.get_job(job.id)
        assert stored is not None
        assert stored.status == "cancelled"

    def test_cancel_running_job(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        job = q.submit("render_episode", "test")
        q.claim_pending()
        cancelled = q.cancel(job.id)
        assert cancelled

    def test_cancel_completed_job_returns_false(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        job = q.submit("analyze", "test")
        q.claim_pending()
        q.complete(job.id)
        assert not q.cancel(job.id)  # already completed

    def test_cancel_nonexistent_job_returns_false(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        assert not q.cancel("nonexistent-id")

    def test_list_jobs_empty(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        assert q.list_jobs() == []

    def test_list_jobs_returns_newest_first(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        q.submit("analyze", "p1")
        q.submit("check", "p2")
        jobs = q.list_jobs()
        assert len(jobs) == 2
        # newest first (second submitted)
        assert jobs[0].project == "p2"
        assert jobs[1].project == "p1"

    def test_list_jobs_filter_by_project(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        q.submit("analyze", "project-a")
        q.submit("analyze", "project-b")
        jobs = q.list_jobs(project="project-a")
        assert len(jobs) == 1
        assert jobs[0].project == "project-a"

    def test_list_jobs_respects_limit(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        for i in range(5):
            q.submit("analyze", f"p{i}")
        assert len(q.list_jobs(limit=3)) == 3

    def test_get_job_nonexistent(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        assert q.get_job("no-such-job") is None

    def test_count_by_status(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        q.submit("analyze", "test")
        q.submit("analyze", "test")
        job3 = q.submit("check", "test")
        q.claim_pending()
        q.complete(job3.id)

        counts = q.count_by_status()
        assert counts.get("pending", 0) == 1   # one still pending (other was claimed → running)
        assert counts.get("completed", 0) == 1  # one completed

    def test_count_by_status_filter_project(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        q.submit("analyze", "a")
        q.submit("analyze", "b")
        counts = q.count_by_status(project="a")
        assert sum(counts.values()) == 1

    def test_close_and_reopen(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "persist.db")
        q1 = BatchQueue(db_path=db_path)
        job = q1.submit("analyze", "test")
        q1.close()

        q2 = BatchQueue(db_path=db_path)
        stored = q2.get_job(job.id)
        assert stored is not None
        assert stored.project == "test"

    def test_get_job_returns_full_data(self, tmp_path: Path) -> None:
        q = self._make_queue(tmp_path)
        job = q.submit("render_episode", "my-drama", episode_num=3)
        stored = q.get_job(job.id)
        assert stored is not None
        assert stored.job_type == "render_episode"
        assert stored.episode_num == 3


import pytest  # noqa: E402

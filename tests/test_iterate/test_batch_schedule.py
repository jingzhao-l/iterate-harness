"""Tests for unattended iterate scenarios: batch multi-repo ranking + cron schedule."""

from __future__ import annotations

import subprocess
from datetime import timezone
from pathlib import Path

import pytest

from iterate_harness.iterate import batch as iterate_batch
from iterate_harness.iterate import decision_log
from iterate_harness.services import cron_scheduler
from iterate_harness.services.cron import load_cron_jobs

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc


@pytest.fixture()
def isolated_registry(tmp_path, monkeypatch):
    """Redirect the cron registry/history to a temp data dir."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def clean_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo-a"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def append_report_entry(repo: Path, findings: list[dict]) -> None:
    decision_log.append_entry(
        repo,
        decision_log.make_entry(
            entry_type="report",
            round_number=1,
            data={"verdict": "needs_revision", "totalFindings": len(findings), "findings": findings},
        ),
    )


class TestScheduledCommand:
    def test_build_scheduled_command_dry_run(self):
        command = iterate_batch.build_scheduled_command(ref="HEAD", rounds=4, mode="dry-run")
        assert command == "ih iterate review --changed --clean-ok --ref HEAD --rounds 4"

    def test_build_scheduled_command_normal(self):
        command = iterate_batch.build_scheduled_command(ref="main", rounds=2, mode="normal")
        assert command.startswith("ih iterate run --changed --clean-ok --ref main")

    def test_invalid_ref_rejected(self):
        with pytest.raises(ValueError):
            iterate_batch.build_scheduled_command(ref="--exec", rounds=3, mode="dry-run")


class TestInstallSchedule:
    def test_invalid_cron_rejected(self, isolated_registry):
        with pytest.raises(ValueError, match="cron"):
            iterate_batch.install_schedule(cwd="/tmp", schedule="not-cron")

    def test_invalid_mode_rejected(self, isolated_registry):
        with pytest.raises(ValueError, match="mode"):
            iterate_batch.install_schedule(cwd="/tmp", schedule="0 9 * * *", mode="chaos")

    def test_install_upserts_and_removes(self, isolated_registry):
        job = iterate_batch.install_schedule(
            cwd="/repo", schedule="0 9 * * 1-5", ref="origin/main", rounds=2
        )
        assert job["name"] == iterate_batch.ITERATE_CRON_JOB_NAME
        assert job["cwd"] == "/repo"
        assert job["timeout"] == iterate_batch.DEFAULT_SCHEDULE_TIMEOUT_SECONDS
        assert "--clean-ok" in job["command"]
        # replace with a tighter schedule
        iterate_batch.install_schedule(cwd="/repo", schedule="*/30 * * * *")
        jobs = [j for j in load_cron_jobs() if j["name"] == iterate_batch.ITERATE_CRON_JOB_NAME]
        assert len(jobs) == 1
        assert jobs[0]["schedule"] == "*/30 * * * *"
        assert iterate_batch.remove_schedule() is True
        assert iterate_batch.remove_schedule() is False

    def test_status_reports_job_and_history(self, isolated_registry):
        assert iterate_batch.schedule_status() is None
        iterate_batch.install_schedule(cwd="/repo", schedule="0 9 * * *")
        cron_scheduler.append_history(
            {"name": iterate_batch.ITERATE_CRON_JOB_NAME, "status": "success", "returncode": 0}
        )
        info = iterate_batch.schedule_status()
        assert info is not None
        assert info["job"]["cwd"] == "/repo"
        assert info["lastRun"]["status"] == "success"


class TestScheduleTimezone:
    """v1.1: cron schedules evaluate in a local IANA zone, stored as UTC."""

    def test_validate_timezone(self):
        from iterate_harness.services.cron import validate_timezone

        assert validate_timezone("Asia/Shanghai") is True
        assert validate_timezone(" America/New_York ") is True
        assert validate_timezone("UTC") is True
        assert validate_timezone("Mars/Olympus") is False
        assert validate_timezone("") is False
        assert validate_timezone(None) is False  # type: ignore[arg-type]

    def test_next_run_time_evaluated_in_local_zone(self):
        from datetime import datetime

        from iterate_harness.services.cron import next_run_time

        base = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)  # 08:00 Beijing
        nxt = next_run_time("0 9 * * *", base=base, tz_name="Asia/Shanghai")
        # 09:00 Beijing == 01:00 UTC the same day
        assert nxt == datetime(2026, 8, 15, 1, 0, tzinfo=UTC)

    def test_next_run_time_without_zone_stays_utc(self):
        from datetime import datetime

        from iterate_harness.services.cron import next_run_time

        base = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)
        assert next_run_time("0 9 * * *", base=base) == datetime(2026, 8, 15, 9, 0, tzinfo=UTC)

    def test_next_run_time_unknown_zone_raises(self):
        from datetime import datetime

        from iterate_harness.services.cron import next_run_time

        base = datetime(2026, 8, 15, tzinfo=UTC)
        with pytest.raises(ValueError, match="unknown timezone"):
            next_run_time("0 9 * * *", base=base, tz_name="Mars/Olympus")

    def test_install_schedule_rejects_unknown_timezone(self, isolated_registry):
        with pytest.raises(ValueError, match="timezone"):
            iterate_batch.install_schedule(cwd="/repo", schedule="0 9 * * *", timezone="Not/AZone")

    def test_installed_job_carries_timezone_and_utc_next_run(self, isolated_registry):
        job = iterate_batch.install_schedule(
            cwd="/repo", schedule="0 9 * * *", timezone="Asia/Shanghai"
        )
        assert job["timezone"] == "Asia/Shanghai"
        assert job["next_run"].endswith("+00:00")
        stored = iterate_batch.schedule_status()["job"]
        assert stored["timezone"] == "Asia/Shanghai"

    def test_install_without_timezone_omits_key(self, isolated_registry):
        job = iterate_batch.install_schedule(cwd="/repo", schedule="0 9 * * *")
        assert "timezone" not in job

    def test_mark_job_run_recomputes_next_run_in_zone(self, isolated_registry):
        from iterate_harness.services.cron import mark_job_run

        iterate_batch.install_schedule(cwd="/repo", schedule="0 9 * * *", timezone="Asia/Shanghai")
        mark_job_run(iterate_batch.ITERATE_CRON_JOB_NAME, success=True)
        stored = iterate_batch.schedule_status()["job"]
        # next_run stays UTC-normalized (09:00 Beijing → 01:00 UTC) after a run.
        assert stored["next_run"].endswith("+00:00")
        assert "T01:00:00" in stored["next_run"]

    def test_scheduler_due_check_uses_stored_utc_time(self, isolated_registry):
        """A Shanghai 09:00 job must be due at 01:00 UTC, not 09:00 UTC."""
        from datetime import datetime

        iterate_batch.install_schedule(cwd="/repo", schedule="0 9 * * *", timezone="Asia/Shanghai")
        stored = iterate_batch.schedule_status()["job"]
        due_at_utc_0100 = cron_scheduler._jobs_due([stored], datetime.fromisoformat(stored["next_run"]))
        assert any(j["name"] == iterate_batch.ITERATE_CRON_JOB_NAME for j in due_at_utc_0100)


class TestJobTimeout:
    def test_default_when_missing(self):
        assert cron_scheduler._job_timeout({}) == cron_scheduler.DEFAULT_JOB_TIMEOUT_SECONDS

    def test_override_and_clamp(self):
        assert cron_scheduler._job_timeout({"timeout": 900}) == 900
        assert cron_scheduler._job_timeout({"timeout": 999999}) == cron_scheduler.MAX_JOB_TIMEOUT_SECONDS
        assert cron_scheduler._job_timeout({"timeout": "bogus"}) == cron_scheduler.DEFAULT_JOB_TIMEOUT_SECONDS

    @pytest.mark.asyncio
    async def test_execute_job_honors_timeout(self, isolated_registry, tmp_path):
        iterate_batch.install_schedule(cwd=str(tmp_path), schedule="0 9 * * *")
        job = {
            "name": "test.sleep",
            "command": "sleep 30",
            "cwd": str(tmp_path),
            "timeout": 1,
        }
        entry = await cron_scheduler.execute_job(job)
        assert entry["status"] == "timeout"
        assert "timed out after 1s" in entry["stderr"]
        # history records the timeout run
        history = cron_scheduler.load_history(job_name="test.sleep")
        assert history and history[-1]["status"] == "timeout"


class TestBatchRanking:
    def test_repo_score_weights(self):
        severity = {"critical": 1, "high": 1, "medium": 1, "low": 1}
        assert iterate_batch.repo_score(severity) == 18

    def test_rank_records_worst_first(self):
        reviewed_bad = {
            "repo": "bad", "status": "reviewed", "score": 20, "totalFindings": 3
        }
        reviewed_good = {
            "repo": "good", "status": "reviewed", "score": 2, "totalFindings": 1
        }
        clean = {"repo": "clean", "status": "clean", "score": 0, "totalFindings": 0}
        error = {"repo": "err", "status": "error", "score": 0, "totalFindings": 0}
        ranked = iterate_batch.rank_records([clean, error, reviewed_good, reviewed_bad])
        assert [r["repo"] for r in ranked] == ["bad", "good", "clean", "err"]

    def test_render_ranking_table(self):
        records = [
            {
                "repo": "alpha",
                "status": "reviewed",
                "totalFindings": 2,
                "severity": {"critical": 0, "high": 1, "medium": 1, "low": 0},
                "score": 7,
                "verdict": "needs_revision",
            },
            {"repo": "beta", "status": "clean", "totalFindings": 0,
             "severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
             "score": 0, "verdict": "-"},
        ]
        table = iterate_batch.render_ranking(records)
        assert "2 repo(s)" in table
        assert "alpha" in table and "beta" in table
        assert alpha_before_beta(table, "alpha", "beta")

    def test_reviewed_record_reads_decision_log(self, clean_git_repo):
        append_report_entry(
            clean_git_repo,
            [
                {"severity": "high", "file": "a.py", "dimension": "correctness"},
                {"severity": "low", "file": "a.py", "dimension": "style"},
            ],
        )
        record = iterate_batch._reviewed_record(clean_git_repo, 1.23)
        assert record["status"] == "reviewed"
        assert record["totalFindings"] == 2
        assert record["severity"]["high"] == 1
        assert record["score"] == 6
        assert record["verdict"] == "needs_revision"


def alpha_before_beta(table: str, first: str, second: str) -> bool:
    return table.index(first) < table.index(second)


class TestRunBatch:
    async def test_clean_repo_short_circuits_without_agent(self, clean_git_repo):
        records = await iterate_batch.run_batch(repos=[str(clean_git_repo)])
        assert records[0]["status"] == "clean"
        assert records[0]["score"] == 0

    async def test_missing_repo_is_error_record(self, tmp_path):
        records = await iterate_batch.run_batch(repos=[str(tmp_path / "nope")])
        assert records[0]["status"] == "error"
        assert "not a directory" in records[0]["note"]

    async def test_invalid_ref_is_error_record(self, clean_git_repo):
        records = await iterate_batch.run_batch(repos=[str(clean_git_repo)], ref=";', drop")
        assert records[0]["status"] == "error"

    async def test_mixed_repos_keep_one_record_each(self, clean_git_repo, tmp_path):
        records = await iterate_batch.run_batch(
            repos=[str(clean_git_repo), str(tmp_path / "ghost")]
        )
        assert len(records) == 2
        assert {r["status"] for r in records} == {"clean", "error"}

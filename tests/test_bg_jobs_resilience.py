"""Regression coverage for background-job persistence recovery."""

from src import bg_jobs


def test_load_rejects_non_mapping_job_store(tmp_path, monkeypatch):
    store = tmp_path / "bg_jobs.json"
    store.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(bg_jobs, "_STORE", store)

    assert bg_jobs._load() == {}


def test_refresh_marks_invalid_exit_code_as_failed(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "bg_jobs"
    jobs_dir.mkdir()
    exit_path = jobs_dir / "job-1.exit"
    exit_path.write_text("not-an-exit-code", encoding="utf-8")
    store = tmp_path / "bg_jobs.json"
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(bg_jobs, "_STORE", store)
    bg_jobs._save({
        "job-1": {
            "id": "job-1",
            "status": "running",
            "exit_path": str(exit_path),
            "started_at": 0,
            "max_runtime_s": 3600,
        }
    })

    refreshed = bg_jobs.refresh()

    assert refreshed["job-1"]["status"] == "failed"
    assert refreshed["job-1"]["exit_code"] == 1

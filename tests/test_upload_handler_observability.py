"""Observability and fail-closed tests for upload authorization fallbacks."""

import json

from src import upload_handler
from src.upload_handler import UploadHandler


def test_failed_admin_lookup_denies_cross_owner_upload_and_reports(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    dated_dir = upload_dir / "2026" / "07" / "17"
    dated_dir.mkdir(parents=True)
    upload_id = "a" * 32 + ".txt"
    stored_file = dated_dir / upload_id
    stored_file.write_text("private", encoding="utf-8")
    (upload_dir / "uploads.json").write_text(
        json.dumps(
            {
                "bob:hash": {
                    "id": upload_id,
                    "path": str(stored_file),
                    "owner": "bob",
                }
            }
        ),
        encoding="utf-8",
    )

    class FailingAuthManager:
        is_configured = True

        @staticmethod
        def is_admin(_owner):
            raise RuntimeError("authorization backend unavailable")

    events = []
    monkeypatch.setattr(
        upload_handler,
        "report_exception",
        lambda _logger, event, _error, **kwargs: events.append((event, kwargs)),
    )
    handler = UploadHandler(str(tmp_path), str(upload_dir))

    assert handler.resolve_upload(upload_id, owner="alice", auth_manager=FailingAuthManager()) is None
    assert events == [
        (
            "upload_admin_lookup_failed",
            {
                "outcome": "best_effort",
                "context": {"upload_id": upload_id, "owner": "alice"},
            },
        )
    ]

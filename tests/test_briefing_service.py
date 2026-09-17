from datetime import datetime, timezone
from services.briefing import compose_briefing

NOW = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)


def test_compose_briefing_selects_today_and_unread():
    emails = [
        {"uid": "1", "from": "a@x", "subject": "Urgent", "snippet": "…", "date": "2026-09-16T07:00:00+00:00", "flag_read": False, "flag_star": True},
        {"uid": "2", "from": "b@x", "subject": "Old", "snippet": "…", "date": "2026-09-10T07:00:00+00:00", "flag_read": True, "flag_star": False},
        {"uid": "3", "from": "c@x", "subject": "Read but starred", "snippet": "…", "date": "2026-09-15T20:00:00+00:00", "flag_read": True, "flag_star": True},
    ]
    events = [
        {"uid": "e1", "summary": "Standup", "dtstart": "2026-09-16T10:00:00+00:00", "dtend": "2026-09-16T10:15:00+00:00", "all_day": False},
        {"uid": "e2", "summary": "Tomorrow", "dtstart": "2026-09-17T10:00:00+00:00", "dtend": "2026-09-17T11:00:00+00:00", "all_day": False},
        {"uid": "e3", "summary": "All day", "dtstart": "2026-09-16", "dtend": "2026-09-17", "all_day": True},
    ]
    notes = [
        {"id": "n1", "title": "Ship v1", "note_type": "checklist", "items": [{"text": "Write changelog", "done": False}], "due_date": "2026-09-16", "pinned": True, "archived": False},
        {"id": "n2", "title": "Someday", "note_type": "note", "items": [], "due_date": None, "pinned": False, "archived": False},
        {"id": "n3", "title": "Archived pin", "note_type": "note", "items": [], "due_date": None, "pinned": True, "archived": True},
    ]
    tasks = [
        {"id": "t1", "name": "Nightly backup", "next_run": "2026-09-16T23:00:00+00:00", "status": "active"},
        {"id": "t2", "name": "Paused", "next_run": "2026-09-16T12:00:00+00:00", "status": "paused"},
    ]
    b = compose_briefing(now=NOW, emails=emails, events=events, notes=notes, tasks=tasks)
    assert [e["uid"] for e in b["emails"]] == ["1", "3"]
    assert [e["uid"] for e in b["events"]] == ["e1", "e3"]
    assert [n["id"] for n in b["notes"]] == ["n1"]
    assert [t["id"] for t in b["tasks"]] == ["t1"]
    assert b["date"] == "2026-09-16"
    assert b["counts"] == {"emails": 2, "events": 2, "notes": 1, "tasks": 1}


def test_compose_briefing_handles_empty_and_bad_dates():
    b = compose_briefing(now=NOW, emails=[{"uid": "x", "date": "not-a-date", "flag_read": False}], events=[{"uid": "e", "dtstart": None}], notes=None, tasks=None)
    assert b["emails"] == [] and b["events"] == [] and b["notes"] == [] and b["tasks"] == []


def test_email_cap_and_order():
    emails = [{"uid": str(i), "subject": f"m{i}", "date": f"2026-09-16T0{i}:00:00+00:00", "flag_read": False, "flag_star": False} for i in range(8)]
    b = compose_briefing(now=NOW, emails=emails, events=[], notes=[], tasks=[])
    assert len(b["emails"]) == 5 and b["emails"][0]["uid"] == "7"  # newest first, capped

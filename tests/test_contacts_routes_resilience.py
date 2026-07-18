"""Regression coverage for contact import and CardDAV cache fallbacks."""

import csv

from routes import contacts_routes


def test_csv_import_uses_defaults_when_dialect_detection_fails(monkeypatch):
    class FailingSniffer:
        def sniff(self, _sample):
            raise csv.Error("unknown dialect")

        def has_header(self, _sample):
            raise csv.Error("unknown header")

    monkeypatch.setattr(contacts_routes.csv, "Sniffer", FailingSniffer)
    monkeypatch.setattr(contacts_routes, "_fetch_contacts", lambda: [])
    monkeypatch.setattr(contacts_routes, "_create_contact", lambda _name, _email: True)

    result = contacts_routes._import_csv_contacts("Ada Lovelace,ada@example.test")

    assert result == {"imported": 1, "failed": 0, "total": 1}


def test_resource_lookup_reports_refresh_failure_and_uses_safe_fallback(monkeypatch):
    events = []
    contacts_routes._contact_cache["contacts"] = []
    monkeypatch.setattr(
        contacts_routes,
        "_fetch_contacts",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("CardDAV unavailable")),
    )
    monkeypatch.setattr(contacts_routes, "_vcard_url", lambda uid: f"https://contacts.test/{uid}.vcf")
    monkeypatch.setattr(
        contacts_routes,
        "report_exception",
        lambda _logger, event, _error, **kwargs: events.append((event, kwargs)),
    )

    result = contacts_routes._resolve_resource_url("contact-1")

    assert result == "https://contacts.test/contact-1.vcf"
    assert events == [
        (
            "contacts_resource_refresh_failed",
            {"outcome": "best_effort", "context": {"contact_id": "contact-1"}},
        )
    ]

"""Tests for routes.compare_helpers.resolve_ad_hoc_endpoint — the shared
Compare/Council ad-hoc endpoint resolution + owner scoping.

Uses the fake-session pattern from tests/test_localmodels_registry.py:
a minimal fake SQLAlchemy Session/Query that doesn't touch a real DB.
"""

import routes.compare_helpers as compare_helpers


class _FakeEP:
    # Class-level attr so `ModelEndpoint.base_url == X` (evaluated before the
    # fake .filter() ignores it) yields a bool instead of raising AttributeError.
    base_url = None

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *a):
        return _FakeQuery(self.rows)

    def close(self):
        pass


def _fake_owner_filter(query, model_cls, user, include_shared=True):
    """Stand-in for src.auth_helpers.owner_filter that actually filters the
    fake rows by owner, so the ownership-scoping test can observe it."""
    rows = [
        r for r in query._rows
        if not user or getattr(r, "owner", None) == user
        or (include_shared and getattr(r, "owner", None) is None)
    ]
    return _FakeQuery(rows)


def test_matching_url_attaches_stored_key(monkeypatch):
    ep = _FakeEP(base_url="http://a.example", api_key="secret-key", is_enabled=True, owner=None)
    monkeypatch.setattr(compare_helpers, "SessionLocal", lambda: _FakeSession([ep]))
    monkeypatch.setattr(compare_helpers, "build_headers", lambda key, base: {"Authorization": f"Bearer {key}"})

    _, headers = compare_helpers.resolve_ad_hoc_endpoint("http://a.example/v1/chat/completions")

    assert headers == {"Authorization": "Bearer secret-key"}


def test_non_matching_url_returns_empty_headers(monkeypatch):
    monkeypatch.setattr(compare_helpers, "SessionLocal", lambda: _FakeSession([]))

    _, headers = compare_helpers.resolve_ad_hoc_endpoint("http://nope.example")

    assert headers == {}


def test_owner_scoping_blocks_another_users_endpoint(monkeypatch):
    bobs_ep = _FakeEP(base_url="http://shared.example", api_key="bobs-secret", is_enabled=True, owner="bob")
    monkeypatch.setattr(compare_helpers, "SessionLocal", lambda: _FakeSession([bobs_ep]))
    monkeypatch.setattr(compare_helpers, "build_headers", lambda key, base: {"Authorization": f"Bearer {key}"})
    monkeypatch.setattr(compare_helpers, "owner_filter", _fake_owner_filter)

    # alice must NOT be able to spend bob's stored key.
    _, alice_headers = compare_helpers.resolve_ad_hoc_endpoint("http://shared.example", owner="alice")
    assert alice_headers == {}

    # bob himself can still use his own endpoint.
    _, bobs_headers = compare_helpers.resolve_ad_hoc_endpoint("http://shared.example", owner="bob")
    assert bobs_headers == {"Authorization": "Bearer bobs-secret"}

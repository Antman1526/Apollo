from services.memory.brain import distill_and_store


def test_distill_and_store_dedups_and_indexes():
    added, indexed = [], []
    fake_mm = type("MM", (), {
        "load": lambda self, owner=None: [{"text": "User uses Postgres 16"}],
        "load_all": lambda self: [{"text": "User uses Postgres 16"}],
        "find_duplicates": lambda self, text, entries: any(e["text"] == text for e in entries),
        "add_entry": lambda self, text, source="user", category="fact", owner=None: added.append(text) or {"id": f"id{len(added)}", "text": text},
        "save": lambda self, allm: None,
    })()
    fake_vec = type("V", (), {"healthy": True, "add": lambda self, mid, text: indexed.append((mid, text))})()

    facts = ["User uses Postgres 16", "Prefers dark mode"]  # first is a dup
    res = distill_and_store(facts, owner="me", source="agent", session_id="s1",
                            memory_manager=fake_mm, memory_vector=fake_vec)
    assert added == ["Prefers dark mode"]        # dup skipped
    assert indexed == [("id1", "Prefers dark mode")]
    assert res["added"] == 1 and res["skipped"] == 1


def test_find_duplicates_returning_list_is_treated_as_duplicate():
    """Real MemoryManager.find_duplicates returns a List[Dict]; a truthy list must dedupe."""
    added, indexed = [], []
    fake_mm = type("MM", (), {
        "load": lambda self, owner=None: [{"text": "Existing fact"}],
        # returns a non-empty list for a match, [] otherwise — like the real impl
        "find_duplicates": lambda self, text, entries: [e for e in entries if e["text"].lower() == text.lower()],
        "add_entry": lambda self, text, source="user", category="fact", owner=None: added.append(text) or {"id": f"id{len(added)}", "text": text},
        "save": lambda self, allm: None,
    })()
    fake_vec = type("V", (), {"healthy": True, "add": lambda self, mid, text: indexed.append((mid, text))})()

    res = distill_and_store(["Existing fact", "New fact"], owner="me", source="import",
                            session_id=None, memory_manager=fake_mm, memory_vector=fake_vec)
    assert added == ["New fact"]
    assert res["added"] == 1 and res["skipped"] == 1
    assert indexed == [("id1", "New fact")]


def test_unhealthy_vector_skips_indexing_but_still_stores():
    added, indexed = [], []
    fake_mm = type("MM", (), {
        "load": lambda self, owner=None: [],
        "find_duplicates": lambda self, text, entries: [],
        "add_entry": lambda self, text, source="user", category="fact", owner=None: added.append(text) or {"id": "x1", "text": text},
        "save": lambda self, allm: None,
    })()
    fake_vec = type("V", (), {"healthy": False, "add": lambda self, mid, text: indexed.append((mid, text))})()

    res = distill_and_store(["Only fact"], owner=None, source="agent", session_id="s9",
                            memory_manager=fake_mm, memory_vector=fake_vec)
    assert added == ["Only fact"]
    assert indexed == []          # not indexed because vector store is unhealthy
    assert res["added"] == 1 and res["skipped"] == 0


def test_stored_entry_carries_session_id_and_save_called_once():
    saved = {"count": 0, "entries": None}
    added = []

    def _save(self, entries):
        saved["count"] += 1
        saved["entries"] = entries

    fake_mm = type("MM", (), {
        "load": lambda self, owner=None: [{"text": "old"}],
        "find_duplicates": lambda self, text, entries: [],
        "add_entry": lambda self, text, source="user", category="fact", owner=None: added.append(text) or {"id": "n1", "text": text},
        "save": _save,
    })()
    fake_vec = type("V", (), {"healthy": True, "add": lambda self, mid, text: None})()

    distill_and_store(["a", "b"], owner="me", source="agent", session_id="sess-42",
                      memory_manager=fake_mm, memory_vector=fake_vec)
    assert saved["count"] == 1                      # saved exactly once
    new_entries = [e for e in saved["entries"] if e.get("id") == "n1"]
    assert new_entries and all(e.get("session_id") == "sess-42" for e in new_entries)


def test_empty_facts_is_noop():
    added = []
    fake_mm = type("MM", (), {
        "load": lambda self, owner=None: [],
        "find_duplicates": lambda self, text, entries: [],
        "add_entry": lambda self, text, source="user", category="fact", owner=None: added.append(text),
        "save": lambda self, allm: None,
    })()
    fake_vec = type("V", (), {"healthy": True, "add": lambda self, mid, text: None})()
    res = distill_and_store([], owner="me", source="agent", session_id=None,
                            memory_manager=fake_mm, memory_vector=fake_vec)
    assert added == []
    assert res == {"added": 0, "skipped": 0}

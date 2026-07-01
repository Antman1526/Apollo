from services.memory.chat_import import parse_chatgpt_export, parse_claude_export, parse_export


def test_parse_chatgpt_mapping_tree():
    obj = [{
        "title": "DB choices",
        "mapping": {
            "a": {"message": {"author": {"role": "user"}, "content": {"parts": ["Use Postgres?"]}}},
            "b": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["Yes."]}}},
        },
    }]
    convos = parse_chatgpt_export(obj)
    assert len(convos) == 1
    assert convos[0]["title"] == "DB choices"
    roles = [m["role"] for m in convos[0]["messages"]]
    assert "user" in roles and "assistant" in roles
    assert any("Postgres" in m["text"] for m in convos[0]["messages"])


def test_parse_claude_flat_items():
    obj = {"conversations": [{
        "name": "Trip",
        "chat_messages": [
            {"sender": "human", "text": "Book Berlin"},
            {"sender": "assistant", "text": "Done"},
        ],
    }]}
    convos = parse_claude_export(obj)
    assert convos[0]["title"] == "Trip"
    assert convos[0]["messages"][0]["role"] == "user"
    assert "Berlin" in convos[0]["messages"][0]["text"]


def test_parse_export_autodetects_and_tolerates_garbage():
    assert parse_export({"nonsense": 1}) == []          # unknown → empty, no raise


def test_parse_export_autodetects_chatgpt_list():
    obj = [{
        "title": "T",
        "mapping": {"a": {"message": {"author": {"role": "user"}, "content": {"parts": ["hi"]}}}},
    }]
    convos = parse_export(obj)
    assert len(convos) == 1 and convos[0]["messages"][0]["text"] == "hi"


def test_parse_export_autodetects_claude_dict():
    obj = {"conversations": [{"name": "N", "chat_messages": [{"sender": "human", "text": "yo"}]}]}
    convos = parse_export(obj)
    assert len(convos) == 1 and convos[0]["messages"][0]["role"] == "user"


def test_skips_empty_messages_and_tolerates_bad_conversation():
    obj = [
        {"title": "ok", "mapping": {
            "a": {"message": {"author": {"role": "user"}, "content": {"parts": ["real"]}}},
            "b": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["  "]}}},
            "c": {"message": None},
        }},
        "this-is-not-a-dict",  # bad conversation must not raise
    ]
    convos = parse_chatgpt_export(obj)
    assert len(convos) == 1
    assert [m["text"] for m in convos[0]["messages"]] == ["real"]


def test_returns_empty_on_non_json_shapes():
    assert parse_chatgpt_export("nope") == []
    assert parse_claude_export("nope") == []
    assert parse_export(None) == []

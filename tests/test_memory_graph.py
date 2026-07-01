from services.memory.graph import build_graph


MEMS = [
    {"id": "a", "text": "User uses Postgres 16", "category": "fact", "session_id": "s1"},
    {"id": "b", "text": "User prefers Postgres over MySQL", "category": "fact", "session_id": "s1"},
    {"id": "c", "text": "User lives in Berlin", "category": "fact", "session_id": "s2"},
]


def _neighbors(mem):
    # a<->b are similar; c is unrelated
    table = {
        "a": [{"memory_id": "b", "score": 0.82}, {"memory_id": "c", "score": 0.10}],
        "b": [{"memory_id": "a", "score": 0.82}, {"memory_id": "c", "score": 0.12}],
        "c": [{"memory_id": "a", "score": 0.10}, {"memory_id": "b", "score": 0.12}],
    }
    return table[mem["id"]]


def test_nodes_carry_fields_and_truncate():
    g = build_graph(MEMS, _neighbors, threshold=0.6, max_neighbors=4, max_nodes=100)
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"a", "b", "c"}
    a = next(n for n in g["nodes"] if n["id"] == "a")
    assert a["category"] == "fact" and a["session_id"] == "s1" and "Postgres" in a["label"]


def test_semantic_edges_thresholded_and_deduped():
    g = build_graph(MEMS, _neighbors, threshold=0.6, max_neighbors=4, max_nodes=100)
    sem = {frozenset((e["source"], e["target"])) for e in g["edges"] if e["type"] == "semantic"}
    assert frozenset(("a", "b")) in sem          # above threshold
    assert frozenset(("a", "c")) not in sem      # below threshold
    assert len([e for e in g["edges"] if e["type"] == "semantic"]) == 1   # symmetric a-b deduped


def test_session_edges_added():
    g = build_graph(MEMS, _neighbors, threshold=0.6, max_neighbors=4, max_nodes=100)
    ses = {frozenset((e["source"], e["target"])) for e in g["edges"] if e["type"] == "session"}
    assert frozenset(("a", "b")) in ses          # both in s1
    assert not any("c" in fs for fs in ses)      # c alone in s2


def test_max_nodes_caps_and_neighbor_fn_only_called_for_kept_nodes():
    calls = []
    def nf(mem):
        calls.append(mem["id"]); return []
    many = [{"id": str(i), "text": f"f{i}", "category": "fact", "session_id": None} for i in range(10)]
    g = build_graph(many, nf, threshold=0.6, max_neighbors=4, max_nodes=3)
    assert len(g["nodes"]) == 3
    assert set(calls) == {n["id"] for n in g["nodes"]}   # not called for dropped nodes

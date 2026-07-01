"""Build a knowledge graph over memories: nodes = facts, edges = semantic
similarity (injected neighbor lookup) + shared source-session. Pure — no DB."""

_LABEL_MAX = 80


def build_graph(memories, neighbor_fn, *, threshold=0.6, max_neighbors=4, max_nodes=300):
    # Newest first if a timestamp is present; cap node count to bound cost.
    mems = sorted(memories, key=lambda m: m.get("timestamp") or 0, reverse=True)[:max_nodes]
    kept = {m["id"] for m in mems}

    nodes = []
    for m in mems:
        text = (m.get("text") or "").strip()
        label = text if len(text) <= _LABEL_MAX else text[:_LABEL_MAX - 1].rstrip() + "…"
        nodes.append({
            "id": m["id"], "label": label, "text": text,
            "category": m.get("category") or "fact", "session_id": m.get("session_id"),
        })

    edges = []
    # Semantic edges (symmetric-deduped, thresholded, top-N per node).
    sem_seen = set()
    for m in mems:
        nbrs = [n for n in (neighbor_fn(m) or [])
                if n.get("memory_id") in kept and n.get("memory_id") != m["id"]
                and (n.get("score") or 0) >= threshold]
        nbrs.sort(key=lambda n: n.get("score") or 0, reverse=True)
        for n in nbrs[:max_neighbors]:
            key = frozenset((m["id"], n["memory_id"]))
            if key in sem_seen:
                continue
            sem_seen.add(key)
            edges.append({"source": m["id"], "target": n["memory_id"],
                          "weight": round(float(n.get("score") or 0), 3), "type": "semantic"})

    # Session-shared edges (chain within each session, deduped among themselves).
    ses_seen = set()
    by_session = {}
    for m in mems:
        sid = m.get("session_id")
        if sid:
            by_session.setdefault(sid, []).append(m["id"])
    for ids in by_session.values():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = frozenset((ids[i], ids[j]))
                if key in ses_seen:
                    continue
                ses_seen.add(key)
                edges.append({"source": ids[i], "target": ids[j], "weight": 0.5, "type": "session"})

    return {"nodes": nodes, "edges": edges}

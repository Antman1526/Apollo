# Memory Knowledge-Graph UI — Design + Plan

> REQUIRED SUB-SKILL for execution: superpowers:subagent-driven-development. Checkbox (`- [ ]`) steps.

**Goal:** A visual knowledge graph over the user's distilled memories: nodes = atomic facts, edges = semantic similarity (via the existing ChromaDB index) plus shared source-session. Rendered as a self-contained force-directed SVG in a new "Graph" tab of the Brain/memory modal; click a node to read the fact and jump to its source chat.

**Design decisions (approved):**
- **Edges = semantic similarity** from the existing `memory_vector` (ChromaDB) — for each memory, its nearest neighbors above a threshold — plus a secondary edge for memories sharing `session_id`. No new embedding infra.
- **Self-contained SVG** force-directed layout in vanilla JS (no CDN/D3) — robust under Apollo's nonce CSP and the no-build frontend.
- **New "Graph" tab** in `#memory-modal`, beside "Memories".

**Reused (verified):** `memory_manager.load(owner) -> [{id,text,category,source,session_id,timestamp}]`; `memory_vector.search(query, k) -> [{memory_id, score}]` (`services/memory/memory_vector.py:90`), `.healthy`; `setup_memory_routes(memory_manager, session_manager, memory_vector=None)` (`routes/memory_routes.py:37`, `GET ""` at :112); modal tabs use `data-memory-tab`/`data-memory-panel` (`static/index.html:245+`).

**Tests:** `/Users/Antman/Apollo/venv/bin/python -m pytest` (Python); `node --test tests/*.mjs` (JS). Worktree `/Users/Antman/Apollo-skills-wt`.

---

## Task 1: Pure graph builder (backend logic)

`build_graph(memories, neighbor_fn, *, threshold, max_neighbors, max_nodes)` →
`{"nodes":[...], "edges":[...]}`. `neighbor_fn(memory) -> [{"memory_id","score"}]`
is injected (so no ChromaDB in the test). Edges: semantic (score ≥ threshold,
top `max_neighbors`, self excluded, symmetric-deduped) + session-shared.

**Files:** Create `services/memory/graph.py`; Test `tests/test_memory_graph.py`

- [ ] **Step 1: Failing test**

```python
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
```

- [ ] **Step 2: Run → fails** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# services/memory/graph.py
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

    seen = set()
    edges = []
    # Semantic edges (symmetric-deduped, thresholded, top-N per node).
    for m in mems:
        nbrs = [n for n in (neighbor_fn(m) or [])
                if n.get("memory_id") in kept and n.get("memory_id") != m["id"]
                and (n.get("score") or 0) >= threshold]
        nbrs.sort(key=lambda n: n.get("score") or 0, reverse=True)
        for n in nbrs[:max_neighbors]:
            key = frozenset((m["id"], n["memory_id"]))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": m["id"], "target": n["memory_id"],
                          "weight": round(float(n.get("score") or 0), 3), "type": "semantic"})

    # Session-shared edges (chain within each session, deduped against above).
    by_session = {}
    for m in mems:
        sid = m.get("session_id")
        if sid:
            by_session.setdefault(sid, []).append(m["id"])
    for ids in by_session.values():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = frozenset((ids[i], ids[j]))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"source": ids[i], "target": ids[j], "weight": 0.5, "type": "session"})

    return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 4: Run → passes.**  **Step 5: Commit** `feat(graph): pure memory knowledge-graph builder`

---

## Task 2: `GET /api/memory/graph` route

Owner-scoped; loads memories, builds neighbor_fn from `memory_vector.search`, returns
the graph. Degrades to session-only edges when the vector store is unavailable.

**Files:** Modify `routes/memory_routes.py`; Test `tests/test_memory_graph_route.py`

- [ ] **Step 1** `grep -n "@router.get(\"\")\|_owner\|memory_vector\|def setup_memory_routes" routes/memory_routes.py` — mirror the existing `GET ""` (list) route's owner handling.
- [ ] **Step 2** Add:

```python
@router.get("/graph")
async def memory_graph(request: Request):
    from services.memory.graph import build_graph
    user = _owner(request)
    mems = memory_manager.load(owner=user)

    def neighbor_fn(mem):
        if not (memory_vector and getattr(memory_vector, "healthy", False)):
            return []
        try:
            return memory_vector.search(mem.get("text") or "", k=6)
        except Exception:
            return []

    return build_graph(mems, neighbor_fn, threshold=0.6, max_neighbors=4, max_nodes=300)
```

- [ ] **Step 3** Test: fake `memory_manager.load` returns a few memories, fake `memory_vector` (healthy + `search`), assert the route returns `{nodes,edges}`; and a second test with `memory_vector=None` returns session-only edges (no crash). Mirror `tests/test_brain_routes.py` harness.
- [ ] **Step 4** `pytest tests/test_memory_graph_route.py -q` passes; `import routes.memory_routes` clean. **Step 5** Commit `feat(graph): /api/memory/graph endpoint`

---

## Task 3: Pure force-layout step (frontend logic, node-tested)

`stepLayout(nodes, edges, opts)` mutates node `{x,y,vx,vy}` one tick (repulsion +
spring + centering + damping). Pure math → node-testable; deterministic given seeded
positions.

**Files:** Create `static/js/graphLayout.js`; Test `tests/test_graph_layout.mjs` (add to `package.json` `test:js`)

- [ ] **Step 1: Failing test**

```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { stepLayout, seedPositions } from '../static/js/graphLayout.js';

test('connected nodes move closer over iterations than unconnected', () => {
  const nodes = [{id:'a'},{id:'b'},{id:'c'}];
  seedPositions(nodes, 100, 100, 7);          // deterministic seed
  const edges = [{source:'a', target:'b'}];   // a,b spring together; c free
  const d = (p,q)=>Math.hypot(p.x-q.x, p.y-q.y);
  const before = d(nodes[0], nodes[1]);
  for (let i=0;i<200;i++) stepLayout(nodes, edges, {width:100, height:100});
  const after = d(nodes[0], nodes[1]);
  assert.ok(after < before, 'spring pulls connected nodes together');
  assert.ok(nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y)), 'stable coords');
  assert.ok(nodes.every(n => n.x>=0 && n.x<=100 && n.y>=0 && n.y<=100), 'stays in bounds');
});
```

- [ ] **Step 2: Run → fails.**
- [ ] **Step 3: Implement** `static/js/graphLayout.js` — `seedPositions(nodes,w,h,seed)` (deterministic LCG, no Math.random) and `stepLayout(nodes, edges, {width,height,repulsion=800,spring=0.02,springLen=40,damping=0.85,center=0.005})`: pairwise repulsion (O(n²), fine for ≤300), spring along edges toward `springLen`, mild pull to center, integrate velocity with damping, clamp to bounds. No DOM.
- [ ] **Step 4: Run → passes.** Add the test file to `package.json` `test:js`. **Step 5** Commit `feat(graph): deterministic force-layout step (unit-tested)`

---

## Task 4: Graph tab + SVG rendering (frontend glue)

**Files:** Modify `static/index.html` (new tab+panel in `#memory-modal`), `static/js/memory.js` (or new `static/js/memoryGraph.js` imported by it)

- [ ] **Step 1** `grep -n "data-memory-tab\|data-memory-panel\|memory-tab active\|switchMemoryTab\|fetch('/api/memory" static/index.html static/js/memory.js` — mirror the existing tab button + panel + tab-switch wiring.
- [ ] **Step 2** Add a "Graph" tab button + panel (`data-memory-tab="graph"` / `data-memory-panel="graph"`) containing an `<svg id="memory-graph-svg">` and a side detail box `#memory-graph-detail`.
- [ ] **Step 3** New `static/js/memoryGraph.js`: on graph-tab open (lazy), `fetch('/api/memory/graph')` → `seedPositions` → run `stepLayout` ~300 ticks (or animate) → render `<line>` edges (semantic vs session styled differently) + `<circle>` nodes colored by category, with labels. Click a node → highlight neighbors, show its full `text` in `#memory-graph-detail` with a "Go to source chat" link (its `session_id` → open that session, reusing the existing session-open function). Handle empty graph ("No memories yet — distill a chat first").
- [ ] **Step 4** `node --check static/js/memoryGraph.js`; cross-reference IDs HTML↔JS. Verify the graph tab opens and renders in-app is Task 5. **Step 5** Commit `feat(graph): knowledge-graph tab with SVG force render`

---

## Task 5: Manual verification
- [ ] Launch worktree app; distill a couple of chats to create memories; open Brain → Graph tab → confirm nodes appear, similar facts are linked, clicking a node shows the fact + jumps to its source chat; empty-state shows when no memories.

---

## Self-Review
Coverage: semantic edges via ChromaDB (Tasks 1-2) · session edges (Task 1) · self-contained SVG force layout (Tasks 3-4) · new modal tab (Task 4) · owner-scoped + vector-optional (Task 2) · click→fact+source (Task 4). Placeholders: none for pure tasks (full code); route/frontend carry grep anchors. Names: `build_graph`, `/api/memory/graph`, `seedPositions`/`stepLayout`, `memory-graph-svg` consistent. Deferred: none — this IS the deferred graph feature.

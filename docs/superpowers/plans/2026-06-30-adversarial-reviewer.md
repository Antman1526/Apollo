# Adversarial Reviewer — Design + Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** After the assistant answers, optionally get a *second model* to critique it (flag errors, gaps, needed context) — surfaced inline via a per-message "Review" button and an optional persisted "Review mode" that auto-reviews each answer. Non-blocking.

**Design decisions (autonomous — flagged ⚠ for review):**
- **Frontend-first + a new `/api/review` endpoint.** Review runs *after* the answer is saved (listens to the existing `apollo:assistant-complete` event), so it never blocks or risks chat persistence. ⚠ No hard "review gate" (blocking) in v1 — the verdict is advisory, shown inline. Blocking can be a follow-on.
- **Reviewer model = `resolve_endpoint("reviewer")`**, falling back to `utility` → `default`. A new `reviewer_endpoint_id`/`reviewer_model` settings pair lets the user point it at a *different* model (the whole value of a "second opinion"); unset = reuse utility.
- **Reuse, don't duplicate Compare.** Compare is a full multi-pane A/B UI; review is a lightweight inline verdict on a single answer.

**Tech Stack:** Python/FastAPI, `pytest`; vanilla-JS. Worktree `/Users/Antman/Apollo-skills-wt`; tests `/Users/Antman/Apollo/venv/bin/python -m pytest`.

**Reused (verified in grounding):** `apollo:assistant-complete` event (`static/js/chat.js:2494`), `addAITTSButton` pattern (`static/js/tts-ai.js:459`), TTS toggle + `loadToggleState/saveToggleState` (`static/app.js:2213`, `static/js/storage.js:91`), `llm_call(url,model,messages,...)->str` (`src/llm_core.py:805`), `resolve_endpoint(prefix, owner=...)->(url,model,headers)` (`src/endpoint_resolver.py:205`), role→settings map (`routes/model_routes.py:37`).

---

## Task 1: Pure reviewer logic

`build_review_prompt(question, answer)` and `parse_review(text) -> dict` — construct
the adversarial prompt and normalize the model's critique into
`{verdict, issues: [...], suggestion}`. Pure, no network.

**Files:** Create `services/review/__init__.py` (empty), `services/review/reviewer.py`; Test `tests/test_reviewer.py`

- [ ] **Step 1: Failing test**

```python
from services.review.reviewer import build_review_prompt, parse_review


def test_prompt_includes_qa_and_asks_for_verdict():
    msgs = build_review_prompt("Is the sky green?", "Yes, always.")
    assert msgs[0]["role"] == "system"
    u = msgs[-1]["content"]
    assert "Is the sky green?" in u and "Yes, always." in u
    assert "verdict" in u.lower()


def test_parse_review_extracts_verdict_and_issues():
    text = ("Verdict: incorrect\n"
            "Issues:\n- The sky is blue, not green\n- Overgeneralizes with 'always'\n"
            "Suggestion: Say the sky appears blue due to Rayleigh scattering")
    r = parse_review(text)
    assert r["verdict"].lower() == "incorrect"
    assert any("blue" in i for i in r["issues"])
    assert "Rayleigh" in r["suggestion"]
    assert r["raw"] == text


def test_parse_review_tolerates_freeform():
    r = parse_review("Looks accurate and complete.")
    assert r["raw"] == "Looks accurate and complete."
    assert isinstance(r["issues"], list)   # empty ok
```

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement**

```python
# services/review/reviewer.py
"""Adversarial review of an assistant answer by a second model.

Pure: builds the critique prompt and parses the model's output. The LLM call
lives in the route.
"""
import re

_SYSTEM = (
    "You are an adversarial reviewer. Critically check an assistant's answer for "
    "factual errors, missing caveats, unsupported claims, and gaps. Be concise and "
    "specific. Respond in this format:\n"
    "Verdict: <accurate|incomplete|incorrect|needs context>\n"
    "Issues:\n- <issue>\n- <issue>\n"
    "Suggestion: <one-line fix, or 'none'>"
)


def build_review_prompt(question: str, answer: str) -> list:
    user = (f"Question:\n{question}\n\nAssistant answer:\n{answer}\n\n"
            "Review it. Give your Verdict, Issues, and Suggestion.")
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def parse_review(text: str) -> dict:
    text = text or ""
    verdict, suggestion, issues = "", "", []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"(?i)^verdict\s*:\s*(.+)$", s)
        if m:
            verdict = m.group(1).strip(); continue
        m = re.match(r"(?i)^suggestion\s*:\s*(.+)$", s)
        if m:
            sug = m.group(1).strip()
            suggestion = "" if sug.lower() == "none" else sug
            continue
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", s)
        if m:
            issues.append(m.group(1).strip())
    return {"verdict": verdict, "issues": issues, "suggestion": suggestion, "raw": text}
```

- [ ] **Step 4: Run → passes.**  **Step 5: Commit** `feat(review): pure adversarial-review prompt + parser`

---

## Task 2: "reviewer" endpoint role

Make `resolve_endpoint("reviewer", ...)` fall back to `utility`→`default`, and add
the settings pair so the user can point it at a different model.

**Files:** Modify `src/endpoint_resolver.py`, `routes/model_routes.py`

- [ ] **Step 1** `grep -n "utility\|research\|task\|fallback\|setting_prefix\|_ENDPOINT_SETTING_FIELDS" src/endpoint_resolver.py routes/model_routes.py` — find how `utility`/`research`/`task` resolve and fall back to `default`.
- [ ] **Step 2** Add `reviewer` to the same resolution chain (reviewer → utility → default). Add `"reviewer_endpoint_id": ("reviewer_model", "Adversarial Reviewer")` to `_ENDPOINT_SETTING_FIELDS` (`model_routes.py:37`). Mirror exactly how `utility` is wired (both resolver + settings), no more.
- [ ] **Step 3** Verify: a small test or `python -c` that `resolve_endpoint("reviewer")` returns the utility/default model when `reviewer_*` unset (mirror an existing resolver test if present).
- [ ] **Step 4** Commit `feat(review): reviewer endpoint role (falls back to utility)`

---

## Task 3: `/api/review` route

`POST /api/review {question, answer}` → resolve reviewer model → `llm_call` with the
pure prompt → `parse_review` → return `{verdict, issues, suggestion, raw, model}`.

**Files:** Modify `routes/chat_routes.py` (add route inside `setup_chat_routes`); Test `tests/test_review_route.py`

- [ ] **Step 1** `grep -n "def setup_chat_routes\|@router.post(\"/api/\|resolve_endpoint\|_user\|require_" routes/chat_routes.py` — mirror an existing simple POST route's shape + owner handling.
- [ ] **Step 2** Add:

```python
@router.post("/api/review")
async def review_answer(request: Request):
    from services.review.reviewer import build_review_prompt, parse_review
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not answer:
        raise HTTPException(400, "answer required")
    owner = get_current_user(request)
    url, model, headers = resolve_endpoint("reviewer", owner=owner)
    if not url or not model:
        raise HTTPException(400, "No reviewer/utility model configured — set one in Settings")
    text = await llm_call_async(url, model, build_review_prompt(question, answer),
                                temperature=0.2, headers=headers, timeout=60)
    return {**parse_review(text), "model": model}
```

(Adjust `get_current_user` import/owner handling to match the file's convention found in Step 1.)

- [ ] **Step 3** `tests/test_review_route.py`: TestClient + `setup_chat_routes(...)`, monkeypatch `resolve_endpoint` → a fake and `llm_core.llm_call_async` → a canned critique; assert the route returns parsed `verdict`/`issues` and 400s on empty answer / missing model.
- [ ] **Step 4** `pytest tests/test_review_route.py -q` passes; `DATABASE_URL=sqlite:///./data/app.db python -c "import routes.chat_routes"` clean. **Step 5** Commit `feat(review): /api/review endpoint`

---

## Task 4: Frontend — Review button + Review mode toggle

- [ ] **Step 1** Add `static/js/review.js`: `addReviewButton(messageElement, question, answer)` mirroring `addAITTSButton` (`tts-ai.js:459`) — appends a "Review" button to `.msg-actions` that POSTs `/api/review {question, answer}` and renders `{verdict, issues, suggestion}` in a collapsible box under the message (verdict as a colored badge: accurate=green, incomplete/needs context=amber, incorrect=red).
- [ ] **Step 2** Wire auto-review: in the `apollo:assistant-complete` handler area (or a new listener in `review.js` imported by `app.js`), if `loadToggleState().reviewMode` is on, auto-call review for the just-finished message. Get the user question from the preceding user bubble; answer from `detail.text`.
- [ ] **Step 3** Add a "Review mode" toggle button in the overflow menu mirroring `overflow-tts-btn` (`static/index.html` + `static/app.js:2213`), persisted via `saveToggleState({...reviewMode})`.
- [ ] **Step 4** `node --check static/js/review.js`; confirm element IDs cross-reference. Commit `feat(review): inline Review button + persisted Review mode toggle`

---

## Task 5: Manual verification
- [ ] Launch worktree app; ask a question with a subtly wrong answer; click "Review" → a second model's critique appears (verdict + issues). Toggle "Review mode" on → next answer auto-reviews. Set a distinct `reviewer_model` in Settings and confirm the critique uses it.

---

## Self-Review
Coverage: second-model critique (Tasks 1-3) · on-demand + auto (Task 4) · reviewer role/model (Task 2) · non-blocking (frontend after save) · reuses event/button/toggle/llm_call. ⚠ Blocking review-gate deferred. Placeholders: none (pure + route code full; frontend + role tasks carry grep anchors). Names: `build_review_prompt`/`parse_review`, `resolve_endpoint("reviewer")`, `/api/review`, `addReviewButton`, `reviewMode` consistent.

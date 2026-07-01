# Apollo — Database Schema & Data Models

Scope: the data layer of the Apollo FastAPI app at `/Users/Antman/Apollo`.

Apollo stores state across **three** distinct backends, each chosen for a
different access pattern:

| Backend | What it holds | Where | Module |
|---|---|---|---|
| **SQLite (SQLAlchemy ORM)** | Structured relational domain data — sessions, messages, documents, tasks, gallery, email accounts, endpoints, calendars, memories | `data/app.db` | `core/database.py` |
| **ChromaDB (embedded)** | Vector embeddings for semantic recall — memory, RAG documents, tool index | `data/chroma/` | `src/chroma_client.py`, `src/memory_vector.py`, `src/rag_vector.py`, `src/tool_index.py` |
| **JSON "soft state" files** | Human-editable config & flags | `data/settings.json`, `data/features.json`, `data/sessions.json`, … | `src/settings.py`, `core/atomic_io.py` |

All paths are anchored at `DATA_DIR = os.path.join(BASE_DIR, "data")`
(`src/constants.py:10`).

---

## 1. SQLAlchemy engine & Base

`core/database.py:1-47`

```python
Base = declarative_base()                                   # core/database.py:18

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")   # :31

# A fresh checkout (CI, new clone) has no data/ directory — SQLite cannot
# create the DB file when its parent directory is missing.
if DATABASE_URL.startswith("sqlite:///"):
    _db_dir = os.path.dirname(os.path.abspath(DATABASE_URL[len("sqlite:///"):]))
    if _db_dir:
        os.makedirs(_db_dir, exist_ok=True)                 # :35-38  ← the data-dir creation fix

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}   # :41-44
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)   # :47
```

Key points:

- **Default DB**: `sqlite:///./data/app.db` (relative to the process CWD). The
  live file on disk is `data/app.db` (~536 KB at time of writing).
- **The data-dir creation fix** (`:33-38`): a fresh clone has no `data/`
  directory, and SQLite will not create the DB file when its parent dir is
  missing. The startup block `os.makedirs(_db_dir, exist_ok=True)` creates it.
  This is gated on the `sqlite:///` prefix, so a Postgres/MySQL `DATABASE_URL`
  is a no-op here.
- **`check_same_thread: False`** is required because FastAPI serves requests
  on a thread pool and the SQLite connection is shared across threads. It is
  applied only for SQLite URLs.
- **Override via env**: setting `DATABASE_URL` (e.g. to a Postgres DSN) swaps
  the backend; the `connect_args` and PRAGMA hook both fall back to no-ops for
  non-SQLite engines.

### SQLite `PRAGMA foreign_keys=ON`

`core/database.py:54-59`

```python
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

SQLite ships with foreign-key enforcement **off by default**, per-connection.
This listener fires on every new DBAPI connection and turns it on, so the
`ondelete="CASCADE"` / `SET NULL` rules declared on the models are actually
enforced. Two deliberate design notes from the source:

- It listens on the **`Engine` class**, not a specific engine instance, so it
  applies to every engine created in the process.
- The `isinstance(..., sqlite3.Connection)` guard makes it a no-op on
  non-SQLite backends (Postgres enforces FKs natively).

### `TimestampMixin`

`core/database.py:20-28` — adds `created_at` and `updated_at` (`DateTime`,
non-null, `updated_at` set `onupdate=_utcnow`). `_utcnow()` (`:13-14`) returns a
**naive** UTC datetime (`tzinfo` stripped) so all timestamps are stored as
naive-UTC consistently.

### `EncryptedText` — transparent at-rest encryption

`core/database.py:62-84`

```python
class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):     # write path
        if value is None: return None
        from src.secret_storage import encrypt
        return encrypt(value)                         # Fernet, "enc:" prefix

    def process_result_value(self, value, dialect):   # read path
        if value is None: return None
        from src.secret_storage import decrypt
        return decrypt(value)
```

A custom `TypeDecorator` that Fernet-encrypts on write (`enc:` prefix) and
decrypts on read, so callers use the column as plaintext. The key lives at
`data/.app_key` (mode `0o600`, gitignored — **not reproduced here**). Legacy
plaintext rows pass through unchanged until their next write; a startup
migration (`_migrate_encrypt_*`) backfills them. Threat model per the
docstring: protects a *stolen SQLite file / backup*, **not** a live process
that can already read the key. Columns using it: `ModelEndpoint.api_key`,
`Signature.data_png`, `Signature.svg`, and the email-password migration path.

---

## 2. ORM models (every `__tablename__`)

All models live in `core/database.py`. Summary table, then full definitions
for the load-bearing ones.

| Model | Table | Line | PK | Notable FKs / cascade |
|---|---|---|---|---|
| `Session` | `sessions` | 87 | `id` (str) | — (parent of messages) |
| `ChatMessage` | `chat_messages` | 166 | `id` (str) | `session_id` → sessions **CASCADE** |
| `Document` | `documents` | 195 | `id` (str) | `session_id` → sessions **SET NULL** |
| `DocumentVersion` | `document_versions` | 228 | `id` (str) | `document_id` → documents **CASCADE** |
| `GalleryAlbum` | `gallery_albums` | 243 | `id` (str) | — |
| `GalleryImage` | `gallery_images` | 256 | `id` (str) | `session_id`/`album_id` **SET NULL** |
| `EmailAccount` | `email_accounts` | 297 | `id` (str) | — (passwords encrypted) |
| `ModelEndpoint` | `model_endpoints` | 337 | `id` (str) | `api_key` = `EncryptedText` |
| `McpServer` | `mcp_servers` | 360 | `id` (str) | — |
| `Comparison` | `comparisons` | 376 | `id` (str) | — |
| `Signature` | `signatures` | 402 | `id` (str) | `data_png`/`svg` = `EncryptedText` |
| `ApiToken` | `api_tokens` | 423 | `id` (str) | — (token hashed) |
| `Webhook` | `webhooks` | 437 | `id` (str) | — |
| `UserTool` | `user_tools` | 452 | `id` (str) | `session_id` → sessions **SET NULL** |
| `UserToolData` | `user_tool_data` | 477 | `id` (int, autoinc) | `tool_id` → user_tools **CASCADE** |
| `CrewMember` | `crew_members` | 495 | `id` (str) | `session_id` → sessions **SET NULL** |
| `ScheduledTask` | `scheduled_tasks` | 519 | `id` (str) | `session_id` SET NULL, `then_task_id` self-FK |
| `EditorDraft` | `editor_drafts` | 567 | `id` (str) | — |
| `TaskRun` | `task_runs` | 599 | `id` (str) | `task_id` → scheduled_tasks **CASCADE** |
| `Memory` | `memories` | 622 | `id` (str) | `session_id` → sessions **SET NULL** |
| `Note` | `notes` | 1360 | `id` (str) | — |
| `CalendarCal` | `calendars` | 1390 | `id` (str) | — (parent of events) |
| `CalendarEvent` | `calendar_events` | 1403 | `uid` (str) | `calendar_id` → calendars |
| `Integration` | `integrations` | 1429 | `id` (str) | — |

> Note: a `character_id` column on `ScheduledTask` (`:553`) intentionally has
> **no** `ForeignKey` — it once referenced an `agent_characters` table that was
> never created; the column is kept for schema compatibility but the FK was
> dropped so SQLAlchemy's table-sort doesn't fail on flush.

### `Session` (chat session) — `sessions` — `core/database.py:87-164`

```python
class Session(TimestampMixin, Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    endpoint_url = Column(String, nullable=False)
    model = Column(String, nullable=False)
    owner = Column(String, nullable=True, index=True)   # username; null = legacy/shared
    rag = Column(Boolean, default=False)
    archived = Column(Boolean, default=False)
    folder = Column(String, nullable=True, default=None)
    headers = Column(JSON, default=dict)
    last_accessed = Column(DateTime, default=func.now(), onupdate=func.now())
    last_message_at = Column(DateTime, nullable=True, default=None)
    is_important = Column(Boolean, default=False)
    message_count = Column(Integer, default=0)
    total_input_tokens = Column(Integer, default=0)
    total_output_tokens = Column(Integer, default=0)
    mode = Column(String, nullable=True)                 # 'agent' | 'chat' | 'research'
    crew_member_id = Column(String, nullable=True)       # links to crew_members.id

    messages = relationship("ChatMessage", back_populates="session",
                            cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_sessions_active', 'archived', 'last_accessed'),   # active-list sort
        Index('ix_sessions_search', 'name', 'archived'),            # search
    )
```

Design notes from source: `last_message_at` is set **explicitly** only when a
message is persisted (not `onupdate`), giving a clean "last conversation"
signal immune to renames / model swaps / merely opening the chat. The
`messages` relationship uses `cascade="all, delete-orphan"` so deleting a
session purges its messages at the ORM level (and `ondelete="CASCADE"` on the
FK enforces it at the DB level under `PRAGMA foreign_keys=ON`).

### `ChatMessage` — `chat_messages` — `core/database.py:166-193`

```python
class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(String, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    meta_data = Column("metadata", Text, nullable=True)   # JSON string for metrics etc.
    timestamp = Column(DateTime, default=_utcnow)
    session = relationship("Session", back_populates="messages")

    __table_args__ = (
        Index('ix_messages_session_time', 'session_id', 'timestamp'),
    )
```

Note the column-name aliasing: the Python attribute is `meta_data` but the
**physical column is `metadata`** (`Column("metadata", ...)`) — `metadata` is
reserved on the declarative `Base`, so the attribute is renamed while the
stored column keeps the friendly name. The composite index
`(session_id, timestamp)` backs the message-history fetch.

### `Document` / `DocumentVersion` — `core/database.py:195-240`

`Document` is a living, AI-editable document; `DocumentVersion` is the
immutable per-edit snapshot. The version relationship cascades:

```python
versions = relationship("DocumentVersion", back_populates="document",
                        cascade="all, delete-orphan",
                        order_by="DocumentVersion.version_number")
```

`Document.session_id` uses `ondelete="SET NULL"` and the model owns an `owner`
column directly — per the source comment, this is robust against a session
delete orphaning the doc and making it vanish from the owner's Library/search.
`source_email_*` columns provide provenance back to an originating email so the
"sign and reply" flow can thread a response.

### `EmailAccount` — `email_accounts` — `core/database.py:297-334`

IMAP/SMTP credentials. **`imap_password` / `smtp_password` are stored
Fernet-encrypted** via `src/secret_storage.py` (migrated automatically on first
start by `_migrate_encrypt_email_passwords`). Defaults: IMAP port 993 +
STARTTLS, SMTP port 465 + `ssl`. Exactly one row per `owner` has
`is_default=True`. Composite index `(owner, is_default)`.

### `ModelEndpoint` — `model_endpoints` — `core/database.py:337-358`

This is where LLM/image **endpoints live (the SQLite table, not a JSON file)**:

```python
class ModelEndpoint(TimestampMixin, Base):
    __tablename__ = "model_endpoints"
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    base_url = Column(String, nullable=False)
    api_key = Column(EncryptedText, nullable=True)        # encrypted at rest
    is_enabled = Column(Boolean, default=True)
    hidden_models = Column(Text, nullable=True)           # JSON list of failed-probe model IDs
    cached_models = Column(Text, nullable=True)           # JSON list of last-known model IDs
    model_type = Column(String, nullable=True, default="llm")   # "llm" | "image"
    supports_tools = Column(Boolean, nullable=True, default=None)
    owner = Column(String, nullable=True, index=True)
```

`hidden_models` / `cached_models` are JSON serialized into `Text` columns (not
relational rows). `supports_tools` is tri-state: `None` = unknown, falls back to
a model-name keyword heuristic.

### `ScheduledTask` / `TaskRun` — `core/database.py:519-619`

`ScheduledTask` is the recurring/one-off task model (time- or event-triggered;
`task_type` `"llm"`|`"action"`; cron via `cron_expression`; chaining via the
self-FK `then_task_id`). `TaskRun` records a single execution and cascades from
its task:

```python
task = relationship("ScheduledTask", backref=backref("runs",
        cascade="all, delete-orphan", order_by="TaskRun.started_at.desc()"))
```

Indexes: `ix_scheduled_tasks_due (status, next_run)` for the scheduler poll and
`ix_scheduled_tasks_event (trigger_type, trigger_event, status)` for event
dispatch; `ix_task_runs_task (task_id, started_at)` for run history.

### `Memory` — `memories` — `core/database.py:622-646`

The **relational** half of memory (the vector half is ChromaDB, §3):

```python
class Memory(Base):
    __tablename__ = "memories"
    id = Column(String, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    category = Column(String, default='fact')
    source = Column(String, default='user')
    owner = Column(String, nullable=True, index=True)
    session_id = Column(String, ForeignKey("sessions.id", ondelete="SET NULL"),
                        nullable=True, index=True)
    timestamp = Column(Integer, default=lambda: int(_utcnow().timestamp()))
```

Each `Memory.id` is mirrored into the `apollo_memories` Chroma collection (the
vector entry's id == the SQL row id), so semantic search returns ids that map
back to these rows.

#### Second-brain features add NO new tables — they reuse `Memory`

The "second brain" additions (session distillation, chat-export import) are
built **entirely on the existing `memories` table** — a rebuilder must **not**
invent a `facts`/`distilled_facts` table. The orchestrator
`services/memory/brain.py:distill_and_store` calls the *same*
`MemoryManager.add_entry(...)` used by manual memory saves, only varying the
existing columns:

- **`source`** distinguishes provenance using values already allowed by the
  `Memory.source` column: `"agent"` for a distilled chat session
  (`brain.py:172`), `"import"` for a parsed ChatGPT/Claude export
  (`brain.py:104`). (Manual memories keep the default `"user"`.)
- **`category`** stays `"fact"` (`brain.py:56`).
- **`session_id`** backlinks a distilled fact to the originating chat session
  (set on the entry dict at `brain.py:58-59`, `172`); imports pass
  `session_id=None` (`brain.py:105`) since there's no local session. This is the
  existing `Memory.session_id` FK (`SET NULL` on session delete, §2).
- Distilled ids flow into the `apollo_memories` Chroma collection exactly like
  any other memory (only when the vector store is `healthy`; otherwise the row
  is still stored, just not indexed).

De-duplication reuses `MemoryManager.find_duplicates` before insert, so a
re-distill of the same session doesn't multiply rows. In short: distilled facts
are **ordinary `Memory` rows with `source in {"agent","import"}` and a
`session_id` backlink** — no schema change.

#### Skills are files on disk, NOT database rows

Agent Skills (including packs installed via the skill-pack installer) live as
**Markdown files**, not in SQLite or Chroma. The on-disk layout is
`<skills_root>/<category>/<name>/SKILL.md`
(`services/skills/pack_installer.py:144`), i.e. `data/skills/<category>/<name>/
SKILL.md` under the default data dir. Each `SKILL.md` carries YAML frontmatter
(`status`, `source`, `category`, `imported_from`/`imported_ref` provenance) plus
the skill body; script-tier skills may carry sibling files (`scripts/…`,
`.mcp.json`). A rebuilder should treat the skills store as a **filesystem**
concern — there is no skills table, no ORM model, and no migration for it.

### `Note`, `CalendarCal`, `CalendarEvent`, `Integration` — `:1360-1438`

- `Note` (Google-Keep-style): `items` is a JSON string of `[{text, done}]`;
  `ai_classification` cached as `Text`, gated by `ai_content_hash` to avoid
  re-spending LLM tokens on every save.
- `CalendarCal` → `CalendarEvent` (PK is `uid`) with
  `cascade="all, delete-orphan"`. `is_utc` flags whether `dtstart`/`dtend` are
  UTC instants vs legacy naive-local.
- `Integration.config` is a real `JSON` column (type-specific config blob).

---

## 3. ChromaDB vector store

`src/chroma_client.py` — a **singleton** client with two modes auto-selected:

```python
host = os.getenv("CHROMADB_HOST", "").strip()
if host:                                          # HTTP mode (Docker / remote)
    ...
    client = chromadb.HttpClient(host=host, port=port)
    client.heartbeat()
else:                                             # Embedded mode (DEFAULT)
    path = _persist_dir()                         # data/chroma (CHROMA_PERSIST_DIR override)
    os.makedirs(path, exist_ok=True)
    _client = chromadb.PersistentClient(path=path)
```

The native desktop app uses **embedded** mode (`data/chroma/`, no service).
HTTP mode is reached only when `CHROMADB_HOST` is explicitly set (Docker Compose
sets `CHROMADB_HOST=chromadb`). `chromadb` is an **optional dependency** — if
the import fails, `get_chroma_client()` raises a `RuntimeError` with a
`pip install chromadb` hint. A 2s connect probe (`CHROMADB_CONNECT_TIMEOUT`)
keeps an unreachable HTTP host from stalling startup.

### Collections

There are **three** Chroma collections, all created with cosine space
(`metadata={"hnsw:space": "cosine"}`):

| Collection | Purpose | Created in | Vector id = |
|---|---|---|---|
| `apollo_memories` | semantic recall over `memories` rows | `src/memory_vector.py:18,39` | `Memory.id` |
| `apollo_rag` | RAG document chunks (hybrid vector+keyword) | `src/rag_vector.py:27,61` | `doc_<sha256[:16]>` |
| `apollo_tool_index` | semantic tool routing (which tool fits a query) | `src/tool_index.py:56,137` | tool name |

`apollo_rag` weights hybrid retrieval `VECTOR_WEIGHT = 0.7`,
`KEYWORD_WEIGHT = 0.3` (`src/rag_vector.py:24-25`). The memory store stamps
`metadatas=[{"source": "memory"}]` (`memory_vector.py:78`) and converts Chroma's
cosine **distance** back to similarity via `1.0 - distance`
(`memory_vector.py:108-113`); near-duplicate detection uses a `0.92` similarity
threshold (`:116`).

> Note: `services/memory/memory_vector.py` is a parallel copy of
> `src/memory_vector.py` with identical collection name and logic.

### Embeddings (`fastembed` fallback)

`src/embeddings.py` — Apollo does **not** let Chroma compute embeddings; it
passes pre-computed vectors. The embedding client is chosen by
`get_embedding_client()` (`:216-253`) in priority order:

1. **HTTP API** (`EmbeddingClient`, `:38-98`) — Ollama / vLLM / llama.cpp at
   `EMBEDDING_URL`. Health-checked once; a process-level latch
   (`_http_embed_down`, `:203`) avoids re-paying the connect timeout on every
   probe after it's seen down.
2. **Local `fastembed`** (`FastEmbedClient`, `:101-183`) — ONNX, zero-config
   fallback. Default model `sentence-transformers/all-MiniLM-L6-v2`
   (`:35`), cached under `data/fastembed_cache/` (`:118-122`). `fastembed` is
   itself optional (`pip install fastembed`).

Both clients L2-normalize output. There's substantial Windows-specific
self-healing for broken HuggingFace-hub symlinks (`:23-25`, `:133-151`) because
ONNX symlinks fail on UNC/network-share cache dirs (`WinError 1463`).

---

## 4. JSON-file "soft state"

Human-editable config and live state live as JSON under `data/`, written
**atomically** (`core/atomic_io.py`: write to `<path>.tmp.<pid>`, `fsync`, then
`os.replace`). The docstring lists `auth.json`, `sessions.json`,
`settings.json`, `integrations.json`, `cookbook_state.json` as the live-state
files where a torn write would be a data-loss event.

### `data/settings.json` via `src/settings.py`

Single source of truth for app config, merged with `DEFAULT_SETTINGS`
(`src/settings.py:31-176`). `load_settings()` always returns a complete dict
(`{**DEFAULT_SETTINGS, **saved}`); on any parse error it falls back to defaults.
Writes go through `save_settings()` → `atomic_write_json` and then invalidate
the cache. A small **2-second TTL cache** sits in front of reads (covered in
depth in `13-performance-optimization-caching.md`):

```python
_CACHE_TTL = 2.0                                  # src/settings.py:20
_settings_cache: tuple[float, dict] | None = None
```

`get_user_setting(key, owner)` (`:254-270`) resolves a small whitelist of
per-user keys (`_PER_USER_KEYS`, `:242-251` — vision/image/default-model keys)
from `routes/prefs_routes._load_for_user(owner)` first, falling back to the
global setting. `is_setting_overridden(key)` (`:222-235`) reads the **raw**
saved file to distinguish "explicitly set" from "equals default".

### `data/features.json`

Feature flags, defaulted by `DEFAULT_FEATURES` (`src/settings.py:178-187`):
`web_search`, `web_fetch`, `deep_research`, `memory`, `document_editor`, `rag`,
`sensitive_filter`, `gallery`. Same merge-with-defaults + 2s-cache pattern via
`load_features()` / `save_features()`. The live file currently contains just an
override: `{"deep_research": true}`.

### `data/sessions.json`

Referenced from `src/constants.py:13` (`SESSIONS_FILE`), `src/config.py:25,160`,
and `core/auth.py:73` (auth session store, sibling to `auth.json`). This is
file-based session/auth state separate from the SQLite `sessions` table (the
SQLite table holds chat sessions; `sessions.json` holds auth/login sessions).
The file is created lazily on first write and may not exist on a fresh checkout
(it is absent in the current `data/` snapshot).

### `data/endpoints.json` — not present

The task brief lists `data/endpoints.json`, but **no such file or code path
exists** in this codebase. Model endpoints are stored in the SQLite
**`model_endpoints`** table (§2, `core/database.py:337`), not a JSON file. Other
JSON state files that *do* exist in `data/`: `auth.json`, `presets.json`,
`memory.json`, `embedding_endpoint.json` (referenced by
`src/embeddings.py:186-200`).

---

## 5. Schema initialization & migrations

`init_db()` (`core/database.py:1516-1557`, invoked at import via
`init_db()` on `:1886`) runs `Base.metadata.create_all(bind=engine)` and then a
long chain of idempotent, hand-rolled migrations
(`_migrate_add_*`, `_migrate_encrypt_*`). These are raw-SQLite `ALTER TABLE`
steps that check `PRAGMA table_info(<table>)` for the column before adding it —
e.g. `_migrate_add_email_smtp_security` (`:1559+`). There is **no Alembic**; the
migration system is a bespoke forward-only sequence of guarded column adds and
data backfills/encryption passes run on every startup.

---

## 6. Scalability notes (data layer)

- **SQLite single-writer.** `data/app.db` serializes all writes; concurrent
  writers block. Fine for a single-user desktop app, the deliberate target
  here, but it's the hard ceiling on write throughput. Swapping `DATABASE_URL`
  to Postgres is the supported escape hatch (the PRAGMA/connect-args code is
  already SQLite-guarded).
- **Migrations run on every boot.** The `init_db()` chain is O(number of
  migrations) of `PRAGMA`/`ALTER` probes at startup; cheap today but grows
  unbounded.
- **JSON state has no concurrency control beyond atomic replace.** Last writer
  wins; `atomic_write_json` guarantees no *torn* file, not serializability.
- **ChromaDB embedded** is an on-disk HNSW index in-process — no network, but
  also no horizontal scaling; it shares the process's memory and the `data/`
  disk.

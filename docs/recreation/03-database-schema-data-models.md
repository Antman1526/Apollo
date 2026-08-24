# 03 — Database Schema & Data Models

Source of truth: `core/database.py` (1949 lines). Verified line-by-line against
the current worktree. All 27 SQLAlchemy models live in this **one file** — no
other `Base`/`Table()` definitions exist anywhere else in the production
codebase (confirmed by repo-wide grep for `declarative_base`, `class.*Base`,
`= Table(`; the only other hits are two isolated mock `Base`s inside
`tests/test_scheduler_restart_doublefire.py` and
`tests/test_task_scheduler_cancel.py`, unrelated to the app's schema).

There is **no Alembic / formal migration framework**. Schema evolution is done
by ~25 hand-written, idempotent Python functions that probe
`PRAGMA table_info(<table>)` before `ALTER TABLE ... ADD COLUMN`, run in a
fixed order from `init_db()`, which itself executes unconditionally at import
time (`core/database.py:1948`, the last line of the file).

---

## 1. ORM setup

```python
# core/database.py:5-20
from sqlalchemy import event, create_engine, Column, String, Text, Boolean, DateTime, Integer, ForeignKey, JSON, Index, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import declarative_base, declared_attr, relationship, sessionmaker, backref
from src.observability import report_exception
from src.runtime_paths import data_path
...
Base = declarative_base()

class TimestampMixin:
    """Mixin that adds timestamp fields to models"""
    @declared_attr
    def created_at(cls):
        return Column(DateTime, default=_utcnow, nullable=False)
    @declared_attr
    def updated_at(cls):
        return Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
```

```python
# core/database.py:33-46
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{data_path('app.db')}")

if DATABASE_URL.startswith("sqlite:///"):
    _db_dir = os.path.dirname(os.path.abspath(DATABASE_URL[len("sqlite:///"):]))
    if _db_dir:
        os.makedirs(_db_dir, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

`DATABASE_URL` is a full override escape hatch — any SQLAlchemy URL works, not
just SQLite (the engine only special-cases `"sqlite" in DATABASE_URL` for
`connect_args` and the PRAGMA below). Default is `<data_root>/app.db`.

Foreign-key enforcement is turned on for every SQLite connection globally via
an `Engine`-level event listener:

```python
# core/database.py:56-61
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

### `EncryptedText` — transparent at-rest encryption

```python
# core/database.py:64-86
class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True
    def process_bind_param(self, value, dialect):
        if value is None: return None
        from src.secret_storage import encrypt
        return encrypt(value)
    def process_result_value(self, value, dialect):
        if value is None: return None
        from src.secret_storage import decrypt
        return decrypt(value)
```

Fernet-encrypted (`enc:` prefix) via `src/secret_storage.py`; the key lives at
`<data_root>/.app_key` (mode `0o600`). Applied to `model_endpoints.api_key`,
`signatures.data_png`, `signatures.svg`. **Not** applied to
`email_accounts.imap_password`/`smtp_password` (those stay plain `String` —
encryption there is done separately by a raw-SQL startup migration,
`_migrate_encrypt_email_passwords`, §3) — a real inconsistency between two
"encrypted" columns worth knowing before extending either path.

---

## 2. Tables

Every model, in file order, with every column, FK/relationship, index, and
JSON-shape notes.

### `sessions` — `Session(TimestampMixin, Base)` (database.py:89-166)

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | String | PK, index | — | |
| name | String | NOT NULL | — | |
| endpoint_url | String | NOT NULL | — | |
| model | String | NOT NULL | — | |
| owner | String | nullable, index | — | username; NULL = legacy/shared |
| rag | Boolean | | False | |
| archived | Boolean | | False | |
| folder | String | nullable | None | |
| headers | **JSON** | | `dict` | request headers dict |
| created_at / updated_at | DateTime | NOT NULL | `_utcnow` / onupdate `_utcnow` | via `TimestampMixin` |
| last_accessed | DateTime | | `func.now()`, onupdate `func.now()` | |
| last_message_at | DateTime | nullable | None | set ONLY when a message is persisted (not onupdate) — clean "last conversation" signal, used for "Last active" sort |
| is_important | Boolean | | False | |
| message_count | Integer | | 0 | |
| total_input_tokens / total_output_tokens | Integer | | 0 | |
| mode | String | nullable | — | `'agent'` \| `'chat'` \| `'research'` |
| crew_member_id | String | nullable | — | links to `crew_members.id`, **no FK constraint** |

`__table_args__`: `Index('ix_sessions_active','archived','last_accessed')`,
`Index('ix_sessions_search','name','archived')`.
Relationship: `messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")`.

### `chat_messages` — `ChatMessage(Base)` (168-195)

| Column | Type | Notes |
|---|---|---|
| id | String | PK, index |
| session_id | String | FK → `sessions.id` ON DELETE CASCADE, NOT NULL, index |
| role | String | NOT NULL |
| content | Text | NOT NULL |
| meta_data | Text, physical column name `"metadata"` | nullable — **JSON string** for metrics etc. |
| timestamp | DateTime | default `_utcnow` |

Index: `Index('ix_messages_session_time','session_id','timestamp')`.

### `documents` — `Document(TimestampMixin, Base)` (197-227)

| Column | Type | Notes |
|---|---|---|
| id | String | PK, index |
| session_id | String | FK → `sessions.id` ON DELETE SET NULL, nullable, index |
| title | String | NOT NULL, default `"Untitled"` |
| language | String | nullable |
| current_content | Text | NOT NULL, default `""` |
| version_count | Integer | default 1 |
| is_active | Boolean | default True — "open in a session" |
| archived | Boolean | default False — soft-archive, hidden from Library; **distinct** from `is_active` |
| owner | String | nullable, index — owned directly (not derived from session, so a session delete via SET NULL doesn't orphan the doc from search) |
| tidy_verdict | String | nullable — `"keep"` \| `"junk"` \| None |
| source_email_uid / source_email_folder / source_email_account_id | String | nullable — provenance for "Sign and reply" flow |
| source_email_message_id | String | nullable, index |

Relationships: `session` (backref `documents`, cascade `save-update, merge`);
`versions = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version_number")`.

### `document_versions` — `DocumentVersion(Base)` (230-242)

Not `TimestampMixin` (only `created_at`, no `updated_at` — immutable snapshot).

| Column | Type | Notes |
|---|---|---|
| id | String | PK, index |
| document_id | String | FK → `documents.id` ON DELETE CASCADE, NOT NULL, index |
| version_number | Integer | NOT NULL |
| content | Text | NOT NULL |
| summary | String | nullable — edit description |
| source | String | default `"ai"` — `"ai"` \| `"user"` |
| created_at | DateTime | default `_utcnow` |

### `gallery_albums` — `GalleryAlbum(TimestampMixin, Base)` (245-255)

id (String PK, index), name (String NOT NULL), description (Text default `""`),
cover_id (String nullable — a `GalleryImage.id`, no FK), owner (String nullable, index).
`images = relationship("GalleryImage", back_populates="album")`.

### `gallery_images` — `GalleryImage(TimestampMixin, Base)` (258-296)

| Column | Type | Notes |
|---|---|---|
| id | String | PK, index |
| filename | String | NOT NULL, **unique** — join key to the on-disk file under `generated_images/` |
| prompt | Text | NOT NULL, default `""` |
| model, size, quality | String | nullable |
| tags | String | nullable, default `""` |
| ai_tags | Text | nullable, default `""` — comma-separated |
| session_id | String | FK → `sessions.id` SET NULL, nullable, index |
| album_id | String | FK → `gallery_albums.id` SET NULL, nullable, index |
| owner | String | nullable, index |
| is_active | Boolean | default True |
| favorite | Boolean | default False |
| file_hash | String(64) | nullable, index — SHA-256 |
| taken_at | DateTime | nullable, index — EXIF `DateTimeOriginal` |
| camera_make, camera_model | String | nullable |
| gps_lat, gps_lng | String | nullable — stored as string for precision |
| width, height, file_size | Integer | nullable |

`__table_args__`: `Index('ix_gallery_images_tags','tags')`,
`Index('ix_gallery_images_model','model')`,
`Index('ix_gallery_images_active','is_active','created_at')`.

### `email_accounts` — `EmailAccount(TimestampMixin, Base)` (299-336)

Supports multiple accounts per user; exactly one row per owner has
`is_default=True`. Passwords encrypted at rest via `src/secret_storage.py` —
see the `EncryptedText` caveat above (this table uses plain `String` + a
migration, not the type decorator).

| Column | Type | Default | Notes |
|---|---|---|---|
| id | String | PK, index | |
| owner | String | nullable, index | |
| name | String | NOT NULL | display name |
| is_default | Boolean | NOT NULL, False | |
| enabled | Boolean | NOT NULL, True | |
| imap_host | String | `""` | |
| imap_port | Integer | 993 | |
| imap_user | String | `""` | |
| imap_password | String | `""` | Fernet-encrypted by migration, not by column type |
| imap_starttls | Boolean | True | |
| smtp_host | String | `""` | |
| smtp_port | Integer | 465 | |
| smtp_security | String | `"ssl"` | `ssl` \| `starttls` \| `none` |
| smtp_user | String | `""` | |
| smtp_password | String | `""` | same encryption caveat |
| from_address | String | `""` | |

Index: `Index('ix_email_accounts_owner_default','owner','is_default')`.

### `model_endpoints` — `ModelEndpoint(TimestampMixin, Base)` (339-360)

Admin-configured LLM/image endpoints; models are auto-discovered via `/v1/models`.

| Column | Type | Default | Notes |
|---|---|---|---|
| id | String | PK, index | |
| name | String | NOT NULL | display label |
| base_url | String | NOT NULL | e.g. `http://localhost:8002/v1` |
| api_key | **EncryptedText** | nullable | real encryption via type decorator |
| is_enabled | Boolean | True | |
| hidden_models | Text | nullable | JSON array of model IDs that failed probing |
| cached_models | Text | nullable | JSON array of last-known model IDs (avoids probe on list) |
| model_type | String | `"llm"` | `"llm"` \| `"image"` |
| supports_tools | Boolean | nullable, None | NULL = unknown → falls back to name-keyword heuristic; auto-detected from `--enable-auto-tool-choice` at Cookbook auto-register time |
| owner | String | nullable, index | NULL = shared/legacy (visible to all); non-null = only that user's picker (admins always see everything) |

No explicit indexes beyond PK/owner.

### `mcp_servers` — `McpServer(TimestampMixin, Base)` (362-375)

id (PK), name (NOT NULL), transport (default `"stdio"` — `"stdio"`\|`"sse"`),
command (nullable, stdio executable path), args (Text nullable — **JSON array**
of command args), env (Text nullable — **JSON object** of env vars), url
(String nullable, for SSE), is_enabled (default True), oauth_config (Text
nullable — **JSON**: `{provider, keys_file, token_file, scopes}`),
disabled_tools (Text nullable — **JSON array** of tool names hidden from LLM).

### `comparisons` — `Comparison(TimestampMixin, Base)` (378-401)

A/B model comparison results. id (PK), session_id (String nullable, **no FK**),
owner (nullable, index), prompt (Text NOT NULL), model_a/model_b (String
NOT NULL), endpoint_a/endpoint_b (String NOT NULL), response_a/response_b
(Text nullable), metrics_a/metrics_b (Text nullable — **JSON string**),
winner (String nullable — `"a"`\|`"b"`\|`"tie"`\|None), is_blind (default
True), blind_mapping (Text nullable — **JSON** `{"left":"a"/"b","right":"a"/"b"}`),
voted_at (DateTime nullable). Index: `Index('ix_comparisons_voted_at','voted_at')`.

### `signatures` — `Signature(TimestampMixin, Base)` (404-422)

User-saved visual signatures. id (PK), owner (nullable, index), name (NOT
NULL, default `"Signature"`), data_png (**EncryptedText**, NOT NULL — base64
PNG, no `data:` prefix), width/height (Integer nullable), svg (**EncryptedText**,
nullable — reserved for future vector storage).

### `api_tokens` — `ApiToken(TimestampMixin, Base)` (425-436)

External-integration bearer tokens (n8n, Make, etc.). id (PK), owner (nullable,
index), name (NOT NULL), token_hash (NOT NULL — bcrypt), token_prefix (NOT
NULL — first 8 chars, used as a DB lookup shard), scopes (String NOT NULL,
default `"chat"` — comma-separated, currently only `chat` is a supported
scope), is_active (default True), last_used_at (DateTime nullable).

### `activity_events` — `ActivityEvent(TimestampMixin, Base)` (439-463)

Append-only ledger of agent tool executions ("computer history"); rows are
only ever inserted or marked undone, never edited.

| Column | Type | Notes |
|---|---|---|
| id | String | PK, index |
| session_id | String | nullable, index, **no FK** |
| owner | String | nullable, index |
| tool | String | NOT NULL, index |
| summary | String | NOT NULL, default `""` |
| input_preview / output_preview | Text | NOT NULL, default `""` |
| exit_code | Integer | nullable |
| duration_ms | Integer | nullable |
| path | String | nullable, index — file-write audit/undo (write_file only) |
| before_content | Text | nullable — prior file contents (capped); None = not captured |
| before_existed | Boolean | nullable — False = undo deletes the file |
| undone | Boolean | NOT NULL, default False |

### `reference_entries` — `ReferenceEntry(TimestampMixin, Base)` (466-484)

A fourth knowledge store alongside memory/skills/documents: third-party
catalogs (free APIs, tutorials, books) the agent consults via
`reference_search`, kept separate so bulk listings don't dilute memory recall.
Rows are owned by their `source` and replaced wholesale on reinstall.

id (PK), source (String NOT NULL, index — SOURCES key), kind (String NOT
NULL, index — `api`\|`tutorial`\|`book`\|`roadmap`), category (String
nullable, index), title (NOT NULL), url (NOT NULL), description (Text
nullable), meta (**JSON**, default `dict` — arbitrary: auth/https/cors,
language, etc.).

### `webhooks` — `Webhook(TimestampMixin, Base)` (487-499)

id (PK), name (NOT NULL), url (NOT NULL), secret (String nullable —
HMAC-SHA256 signing secret), events (String NOT NULL — comma-separated event
types), is_active (default True), last_triggered_at (DateTime nullable),
last_status_code (Integer nullable), last_error (String nullable).

### `user_tools` — `UserTool(TimestampMixin, Base)` (502-524)

User-created sandboxed mini-apps. id (PK), name (NOT NULL), description
(Text nullable), icon (String nullable, default `""`), html_content (Text
NOT NULL), scope (String NOT NULL, default `"global"` — `"global"` or a
session id), session_id (FK → `sessions.id` SET NULL, nullable — **not
indexed**, unlike most other FKs here), owner (nullable, index), is_pinned
(default False), is_active (default True), version (default 1), author
(String nullable, default `"ai"`).
`__table_args__`: `Index('ix_user_tools_scope','scope')`,
`Index('ix_user_tools_active','is_active')`.

### `user_tool_data` — `UserToolData(Base)` (527-542)

Key-value store for user-tool persistent state. **The only Integer
autoincrement PK in the schema.**

id (Integer, PK, `autoincrement=True`), tool_id (FK → `user_tools.id` ON
DELETE CASCADE, NOT NULL), key (String NOT NULL), value (Text nullable),
created_at/updated_at (DateTime, own fields — not `TimestampMixin`).
Index: `Index('ix_user_tool_data_tool_key','tool_id','key', unique=True)`.

### `crew_members` — `CrewMember(TimestampMixin, Base)` (545-566)

A custom AI persona with its own personality/model/tools/memory scope.

id (PK), owner (nullable, index), name (NOT NULL), avatar (String nullable),
user_name (String nullable — what they call the user), personality (Text
nullable — system prompt), model/endpoint_url (String nullable), greeting
(Text nullable), enabled_tools (Text nullable — **JSON array** or the literal
string `"all"`), session_id (FK → `sessions.id` SET NULL, nullable),
is_active (default True), sort_order (default 0), is_default_assistant
(Boolean default False — singleton per-owner "personal assistant" flag),
timezone (String nullable — IANA tz name for scheduled check-ins).

### `scheduled_tasks` — `ScheduledTask(TimestampMixin, Base)` (569-614)

Recurring or one-off task, LLM-powered or direct action, time- or
event-triggered.

| Column | Type | Notes |
|---|---|---|
| id | String | PK, index |
| owner | String | nullable, index |
| name | String | NOT NULL, default `"Untitled Task"` |
| prompt | Text | nullable — LLM prompt (`task_type="llm"`) |
| task_type | String | default `"llm"` — `"llm"` \| `"action"` |
| action | String | nullable — builtin action name (`task_type="action"`) |
| schedule | String | nullable — `"once"`/`"daily"`/`"weekly"`/`"monthly"` |
| scheduled_time | String | nullable — `"HH:MM"` 24h, stored UTC |
| scheduled_day | Integer | nullable — day-of-week (0=Mon) or day-of-month |
| scheduled_date | DateTime | nullable — exact datetime for "once" |
| trigger_type | String | default `"schedule"` — `"schedule"` \| `"event"` |
| trigger_event | String | nullable — e.g. `"session_created"`, `"message_sent"` |
| trigger_count | Integer | nullable — fire every N events |
| trigger_counter | Integer | default 0 |
| next_run / last_run | DateTime | `next_run` indexed, nullable |
| status | String | default `"active"` — `active`/`paused`/`completed` |
| output_target | String | default `"session"` |
| session_id | String | FK → `sessions.id` SET NULL, nullable |
| model, endpoint_url | String | nullable |
| run_count | Integer | default 0 |
| cron_expression | String | nullable — e.g. `"*/5 * * * *"` |
| then_task_id | String | FK → `scheduled_tasks.id` (self-referential) SET NULL, nullable — chained "then run this task" |
| webhook_token | String | nullable, **unique** |
| crew_member_id | String | nullable — optional link, no FK |
| character_id | String | nullable — **FK intentionally dropped**; see comment below |
| max_steps | Integer | nullable — max agent loop iterations, NULL = unlimited |
| email_results | Boolean | default True |
| notifications_enabled | Boolean | default True |

```python
# database.py:600-603
# character_id historically referenced an agent_characters table that was
# never actually created. Keep the column for schema compatibility but
# drop the ForeignKey so SQLAlchemy table sort doesn't fail on flush.
```

Relationships: `session` (backref `scheduled_tasks`, cascade `save-update,
merge`); `then_task = relationship("ScheduledTask", remote_side=[id],
foreign_keys=[then_task_id])`.
`__table_args__`: `Index('ix_scheduled_tasks_due','status','next_run')`,
`Index('ix_scheduled_tasks_event','trigger_type','trigger_event','status')`.

### `editor_drafts` — `EditorDraft(TimestampMixin, Base)` (617-646)

Persisted in-progress gallery-editor session state.

id (PK), owner (nullable, index), name (NOT NULL, default `"Untitled"`),
source_image_id (String nullable, index — a `GalleryImage.id`, no FK), width/
height (Integer nullable), payload (Text NOT NULL, default `""` — **full
layer-state JSON**: layer pixels as base64 PNG dataURLs, offsets, opacities,
visibility, active id, next id), thumbnail (Text nullable — small preview
data URL), is_active (default True).
Index: `Index('ix_editor_drafts_owner_updated','owner','is_active','updated_at')`.

### `task_runs` — `TaskRun(Base)` (649-669)

Record of one `ScheduledTask` execution. Not `TimestampMixin`.

id (PK), task_id (FK → `scheduled_tasks.id` ON DELETE CASCADE, NOT NULL),
started_at (DateTime NOT NULL, default `_utcnow`), finished_at (DateTime
nullable), status (default `"running"` — `running`/`success`/`error`),
result (Text nullable), error (Text nullable), tokens_used (Integer
nullable), steps (Text nullable — **JSON log** of agent tool calls), model
(String nullable — model that actually ran, resolved at execution time).
Relationship: `task` backref `runs`, cascade `all, delete-orphan`, ordered
by `TaskRun.started_at.desc()`.
Index: `Index('ix_task_runs_task','task_id','started_at')`.

### `memories` — `Memory(Base)` (672-708)

Not `TimestampMixin`. id (PK), text (Text NOT NULL), category (String
default `'fact'`), source (String default `'user'`), owner (String nullable,
index), session_id (FK → `sessions.id` SET NULL, nullable, index), timestamp
(**Integer**, default `lambda: int(_utcnow().timestamp())` — Unix epoch
seconds). A code comment (696-698) explicitly flags this as a "schema smell":
every other table uses a naive-UTC `DateTime`; `memories.timestamp` is the
one outlier, left as-is to avoid a risky migration.
Relationship: `session` (backref `memories`).
`__table_args__`: `Index('ix_memories_lookup','category','timestamp')`,
`Index('ix_memories_session','session_id','timestamp')`.

### `notes` — `Note(TimestampMixin, Base)` (1417-1444)

Google Keep-style note/checklist. Defined much later in the file (after the
block of migration functions) but is a normal top-level table.

id (PK), owner (nullable, index), title (String default `""`), content
(Text nullable), items (Text nullable — **JSON string** of
`[{text, done}]`), note_type (default `"note"` — `"note"`\|`"checklist"`),
color/label (String nullable), pinned/archived (Boolean default False),
due_date (String nullable), source (default `"user"` — `"user"`\|`"agent"`),
session_id (String nullable, **no FK**), sort_order (Integer default 0),
image_url (String nullable — uploaded image relative path), repeat (default
`"none"` — none/daily/weekly/monthly/yearly), ai_classification (Text
nullable — **JSON**: `{ kind, solvable, confidence, task_prompt, tools,
items?: [...] }`, populated by `POST /api/notes/{id}/classify`),
ai_content_hash (String nullable — gates re-classification so the LLM isn't
re-run on every save), agent_session_id (String nullable — chat session
spawned by the note's "Agent" button).

### `calendars` — `CalendarCal(TimestampMixin, Base)` (1447-1457)

id (PK), owner (nullable, index), name (NOT NULL), color (default
`"#5b8abf"`), source (default `"local"` — `"local"`\|`"timetree"`).
`events = relationship("CalendarEvent", back_populates="calendar", cascade="all, delete-orphan")`.

### `calendar_events` — `CalendarEvent(TimestampMixin, Base)` (1460-1483)

**Primary key is `uid`, not `id`.**

| Column | Type | Notes |
|---|---|---|
| uid | String | PK, index |
| calendar_id | String | FK → `calendars.id`, NOT NULL, index — no explicit `ondelete` |
| summary | String | NOT NULL, default `""` |
| description, location | Text/String | default `""` |
| dtstart | DateTime | NOT NULL, index |
| dtend | DateTime | NOT NULL |
| all_day | Boolean | default False |
| is_utc | Boolean | NOT NULL, default False — True when `dtstart`/`dtend` are stored as UTC instants (import paths that preserve source TZID); drives the `Z`-suffix on serialization |
| rrule | String | default `""` |
| color | String | nullable — per-event override |
| status | String | default `"confirmed"` — confirmed/cancelled |
| importance | String | default `"normal"` — low/normal/high/critical |
| event_type | String | nullable — work/personal/health/travel/meal/social/admin/other |
| last_pinged | DateTime | nullable — last assistant ping about this event |

### `integrations` — `Integration(TimestampMixin, Base)` (1486-1495)

External service connections. id (PK), owner (nullable, index), name (NOT
NULL), type (String NOT NULL — `"email"`\|`"rss"`\|`"webhook"`), config
(**JSON**, nullable — type-specific shape, varies by `type`), enabled
(default True).

---

## 3. Migration / schema-evolution mechanism

No Alembic, no `schema_version` table or column anywhere (repo-wide grep for
`schema_version` returns nothing). Every migration function follows the same
idiom: connect directly (raw `sqlite3`, or later functions via the
SQLAlchemy `engine` + `text()`), probe `PRAGMA table_info(<table>)`, and only
`ALTER TABLE` if the column is missing — safe to run on every process start.

```python
# core/database.py:769-786 — representative example
def _migrate_add_owner_column():
    """Add owner column to sessions table if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "owner" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN owner TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_owner ON sessions(owner)")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'owner' column to sessions")
        conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"Migration check failed: {e}")
```

All ~29 migration steps run in this fixed order from `init_db()`:

```python
# core/database.py:1578-1619
def init_db():
    _migrate_model_endpoints()
    Base.metadata.create_all(bind=engine)
    _migrate_add_hidden_models_column()
    _migrate_add_cached_models_column()
    _migrate_add_notes_sort_order()
    _migrate_add_model_type_column()
    _migrate_add_model_endpoint_owner_column()
    _migrate_add_supports_tools_column()
    _migrate_add_task_run_model_column()
    _migrate_add_owner_column()
    _migrate_add_document_archived_column()
    _migrate_add_last_message_at_column()
    _migrate_add_folder_column()
    _migrate_add_token_columns()
    _migrate_add_mode_column()
    _migrate_add_multiuser_owner_columns()
    _migrate_add_api_token_scopes_column()
    _migrate_backfill_document_owner_from_session()
    _migrate_assign_legacy_owner()
    _migrate_add_tidy_verdict()
    _migrate_add_doc_source_email_cols()
    _migrate_add_oauth_config()
    _migrate_add_task_automation_columns()
    _migrate_add_disabled_tools()
    _migrate_add_task_v2_columns()
    _migrate_add_notifications_enabled()
    _migrate_drop_ping_notes_tasks()
    _migrate_add_crew_member_id()
    _migrate_add_assistant_columns()
    _migrate_add_email_smtp_security()
    _migrate_seed_email_account()
    _migrate_add_calendar_metadata()
    _migrate_add_calendar_is_utc()
    _migrate_encrypt_email_passwords()
    _migrate_encrypt_signatures()
    _migrate_encrypt_endpoint_keys()

# core/database.py:1945-1948
# Initialize the database by creating all tables
init_db()
```

Notable non-additive migrations:

- **`_migrate_model_endpoints`** (788-804): if `model_endpoints` exists but
  lacks `base_url`, the whole table is **dropped** (`DROP TABLE IF EXISTS`),
  not altered — a destructive bump for the pre-rename (`url`→`base_url`) schema.
- **`_migrate_add_task_automation_columns`** (1229-1300): full table
  **rebuild** (`RENAME TO _old_...` → `CREATE TABLE` with relaxed `NOT NULL`
  → `INSERT ... SELECT` → `DROP _old_...`) because SQLite can't
  `ALTER COLUMN` to drop `NOT NULL`. Triggered only when
  `prompt`/`schedule`/`scheduled_time` are still `NOT NULL` on `scheduled_tasks`.
- **`_migrate_drop_ping_notes_tasks`** (1346-1364): one-time data cleanup —
  deletes rows (not columns) for two obsolete built-in task actions
  (`ping_notes`, `ping_events`) that moved to pure background scanners.
- Three **encryption migrations** run on every startup, idempotent via
  `src.secret_storage.is_encrypted(value)`: `_migrate_encrypt_email_passwords`,
  `_migrate_encrypt_signatures`, `_migrate_encrypt_endpoint_keys`.
- **`_migrate_assign_legacy_owner`** (1059-1162): sweeps every owner-bearing
  table (hardcoded list at 1108-1115) plus `memory.json` and `user_prefs.json`,
  assigning NULL-owner rows to the admin user resolved from `auth.json`'s
  `is_admin: True` flag — closes the "data created while auth is bypassed via
  localhost sits world-visible" gap. **UNCERTAIN:** the hardcoded table list
  includes `"gallery_people"`, but no `GalleryPeople`/`gallery_people` model
  or `CREATE TABLE` exists anywhere in the repo — the migration silently
  no-ops for that table name (wrapped in its own try/except) rather than
  erroring; looks like a dead reference to a feature that was removed or
  never shipped.
- **`_migrate_backfill_document_owner_from_session`** (1165-1189) must run
  *before* the blanket legacy-owner sweep so session-linked documents get
  their true owner and only genuinely orphaned docs fall through to admin.
- **`_migrate_seed_email_account`** (1501-1571): one-time backfill — converts
  legacy flat `imap_host`/`smtp_host` keys in `settings.json` into a single
  `email_accounts` row, only when the table is empty.

---

## 4. Non-ORM tables (raw SQLite, separate database files)

Not every table lives in `app.db` under SQLAlchemy. `routes/email_helpers.py`
maintains its **own** SQLite file with hand-rolled `CREATE TABLE IF NOT
EXISTS` + ad-hoc `ALTER TABLE` migrations, run from `_init_scheduled_db()`
at import time (`_init_scheduled_db()` is called unconditionally at
`routes/email_helpers.py:470`):

```python
# routes/email_helpers.py:240
SCHEDULED_DB = DATA_DIR / "scheduled_emails.db"
```

i.e. `<data_root>/scheduled_emails.db` — a **second SQLite file** alongside
`app.db`. Tables created there (`routes/email_helpers.py:257-467`):

| Table | Key columns | Purpose |
|---|---|---|
| `scheduled_emails` | `id` PK, `to_addr`, `body`, `send_at`, `status`, `owner`, `account_id`, `apollo_kind` | Delayed/scheduled outgoing sends |
| `email_summaries` | `message_id` PK, `summary`, `model_used` | AI summary cache, keyed by Message-ID |
| `email_ai_replies` | `message_id` PK, `reply`, `model_used` | Pre-generated AI draft replies |
| `email_tags` | composite PK `(message_id, owner)` | Tag/spam classification cache — **owner-scoped** on purpose: Message-IDs are global (a newsletter reaches many users with the same Message-ID), so an unscoped PK let one user's tag write clobber another's row and leak UIDs cross-tenant (see the security-review comment at line 302-306) |
| `email_calendar_extractions` | `message_id` PK | Tracks which emails already had calendar events extracted |
| `email_urgency_alerts` | `message_id` PK, `urgency`, `alerted` | Urgency-classifier cache |
| `email_event_seen` | composite PK `(owner, account_key, folder, message_key)` | De-dupe cache for the `email_received` event bus trigger (also created defensively in `routes/email_routes.py:128` with `CREATE TABLE IF NOT EXISTS`, same schema, same DB file) |
| `email_boundaries` | `message_id` PK, `sig_start`, `quote_start`, `turns_json` | LLM-detected signature/quote-block offsets, so folding doesn't re-call the LLM |
| `sender_signatures` | `from_address` PK, `signature_text`, `sample_count` | Per-sender learned signature blocks |

`email_tags` has its own rebuild-in-place migration (335-356: add `owner`
column → rebuild with composite PK → copy → drop → rename) because SQLite
can't add a column to an existing PK.

Other ad-hoc raw-SQL tables found in the repo (outside `app.db` and outside
`scheduled_emails.db`): `scripts/demo_email/seed_demo_emails.py` creates its
own `email_ai_replies`/`email_summaries` tables in a demo-seeding script (not
part of the runtime path). `scripts/update_database.py` is a generic
ad-hoc-migration CLI helper, not itself a schema definition.

---

## 5. ChromaDB

### Client singleton — `src/chroma_client.py`

Two modes, chosen automatically:

```python
# src/chroma_client.py:43-48
def _persist_dir() -> str:
    """Absolute path of the embedded ChromaDB store."""
    configured = os.getenv("CHROMA_PERSIST_DIR", "").strip()
    if configured:
        return configured if os.path.isabs(configured) else os.path.join(_REPO_ROOT, configured)
    return str(data_path("chroma"))
```

```python
# src/chroma_client.py:92-97
# ── Embedded mode (default): local on-disk store, no service required ──
path = _persist_dir()
os.makedirs(path, exist_ok=True)
_client = chromadb.PersistentClient(path=path)
```

HTTP mode (`chromadb.HttpClient`) only activates when `CHROMADB_HOST` is set
(e.g. Docker Compose); it fast-fail-probes the port (`CHROMADB_CONNECT_TIMEOUT`,
default 2.0s) instead of blocking on the OS's ~30-60s connect timeout.
Env vars: `CHROMA_PERSIST_DIR`, `CHROMADB_HOST`, `CHROMADB_PORT` (default
8000), `CHROMADB_CONNECT_TIMEOUT`.

### Collections

All three use `client.get_or_create_collection(name=<NAME>, metadata={"hnsw:space": "cosine"})`
— identical cosine distance metric.

| Collection | Defined | Purpose | ids / documents / metadatas |
|---|---|---|---|
| `apollo_rag` | `src/rag_vector.py:27` | RAG over uploaded/personal documents | id = `doc_{sha256(text)[:16]}` (`_generate_doc_id`); documents = raw text chunk; metadatas = caller-supplied dict, typically `{source, filename, directory, type, chunk_id, owner?}` (`index_personal_documents`, rag_vector.py:320-374) |
| `apollo_memories` | `src/memory_vector.py:20` (also duplicated **byte-identical** in `services/memory/memory_vector.py`) | Semantic index over `Memory` rows | id = `Memory.id`; documents = memory text; metadatas = `{"source": "memory"}` |
| `apollo_tool_index` | `src/tool_index.py:58` | RAG-based tool selection for agent mode (avoids injecting every tool description into every prompt) | id = `builtin_{tool_name}` or `mcp_{tool_name}`; documents = `"Tool: {name}\n{description}"`; metadatas = `{"tool_name":..., "tool_type": "builtin"/"mcp"}` |

`src/memory_vector.py` and `services/memory/memory_vector.py` are verified
byte-identical (`diff` returns nothing) — two copies of the same class
imported from different call sites (`mcp_servers/memory_server.py`,
`src/app_initializer.py` use `src.memory_vector`; `src/builtin_actions.py`
uses `services.memory.memory_vector`). No embedding function is registered
on any collection (`embedding_function=` is never passed) — embeddings are
computed explicitly and passed to `.add()`/`.query()`.

### Embedding client — `src/embeddings.py`

Priority order, both branches normalize embeddings (L2, so ChromaDB cosine
distance = `1 - similarity`):

1. **HTTP API** — default `EMBEDDING_URL=http://{LLM_HOST:-localhost}:11434/v1/embeddings`
   (Ollama-compatible), default model `all-minilm:l6-v2` (`EMBEDDING_MODEL` override).
2. **Local fallback** — `fastembed` ONNX, default model
   `sentence-transformers/all-MiniLM-L6-v2`, cache at `<repo>/data/fastembed_cache`
   (override `FASTEMBED_CACHE_PATH`).

### Known issue — memory wipe doesn't clear the vector store

```python
# routes/admin_wipe_routes.py:99 (paraphrased call site)
from src.memory_vector import get_memory_vector_store
```

**UNCERTAIN / confirmed bug:** `get_memory_vector_store` does not exist
anywhere in `src/memory_vector.py` (grep across the repo finds no
`def get_memory_vector_store`). The call is wrapped in a bare
`try/except Exception` that logs at `info` level and no-ops, so
`DELETE /api/admin/wipe/memory` never actually clears the `apollo_memories`
Chroma collection today — stale vectors survive a "wipe."

### `rag/` vs `chroma/` — two paths, one is vestigial

`src/rag_singleton.py` instantiates `VectorRAG(persist_directory=data_path("rag"))`,
but `VectorRAG` never opens ChromaDB at that path — it always goes through
`chroma_client._persist_dir()` → `data_path("chroma")`. `persist_directory`
is only used for `Path(...).mkdir()` and a display value in `get_stats()`.
So `<data_root>/rag/` is created but effectively unused; the real vector data
is under `<data_root>/chroma/`.

---

## 6. On-disk data directory layout

### Resolution — `src/runtime_paths.py`

```python
# src/runtime_paths.py:63-87
def data_root(*, env=None, repo=None, platform=None, home=None) -> Path:
    env = os.environ if env is None else env
    for key in ("APOLLO_DATA_DIR", "DATA_DIR"):
        value = env.get(key)
        if value:
            return _configured_path(value)
    platform_root = platform_data_root(platform=platform, env=env, home=home)
    if _platform_root_is_activated(platform_root):
        return platform_root
    legacy = legacy_data_root(repo)
    if legacy.exists():
        return legacy
    return platform_root
```

Precedence, exactly as coded:

1. **`APOLLO_DATA_DIR`** env var (checked first) — absolute path, expanded via
   `Path(value).expanduser().resolve()`.
2. **`DATA_DIR`** env var — same handling, fallback name.
3. The **platform-standard per-user directory**, but *only if* an activation
   receipt JSON exists at `<platform_root>.parent/apollo-data-migration.json`
   with `status == "activated"` and `target == str(platform_root)`.
4. The **legacy checkout-local `<repo_root>/data`** directory, if it exists
   on disk (keeps existing installs working unchanged).
5. Fallback: the platform-standard directory even if not yet "activated"
   (fresh installs with no legacy `data/`).

```python
# src/runtime_paths.py:25-39
def platform_data_root(*, platform=None, env=None, home=None) -> Path:
    platform = platform or sys.platform
    ...
    if platform.startswith("darwin"):
        return home / "Library" / "Application Support" / APP_NAME
    if platform.startswith("win"):
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / APP_NAME
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / APP_NAME.lower()
```

Default per-OS root (`APP_NAME = "Apollo"`), no env override, no legacy dir:

| OS | Default data root |
|---|---|
| macOS | `~/Library/Application Support/Apollo` |
| Windows | `%LOCALAPPDATA%\Apollo` |
| Linux | `$XDG_DATA_HOME/apollo` or `~/.local/share/apollo` |

`data_path(*parts)` = `data_root().joinpath(*parts)` — every subsystem in the
app builds its path off this one function.

### Directory tree (all rooted at `<data_root>`)

| Path | Contents | Source |
|---|---|---|
| `app.db` | SQLite — all 27 ORM tables (§2) | `core/database.py:33` |
| `scheduled_emails.db` | Raw-SQL email tables (§4) | `routes/email_helpers.py:240` |
| `chroma/` | Embedded ChromaDB `PersistentClient` store — the 3 real collections (§5) | `src/chroma_client.py:48` |
| `rag/` | Vestigial — `VectorRAG.persist_directory`, not the real Chroma path | `src/rag_singleton.py:18` |
| `uploads/` | Chat/document attachments | `src/constants.py:20` |
| `generated_images/` | Gallery image files (`gallery_images.filename` join key) | `app.py`, `routes/gallery_routes.py` |
| `personal_docs/` (+ `personal_docs/runbook/`) | Personal-document RAG source tree | `src/constants.py:18-19` |
| `mail-attachments/` (override `APOLLO_MAIL_ATTACHMENTS_DIR`) | Extracted email attachments | `routes/email_helpers.py:236` |
| `fastembed_cache/` | Local ONNX embedding model cache (override `FASTEMBED_CACHE_PATH`) | `src/embeddings.py` |
| `.app_key` | Fernet key for `EncryptedText` + email-password encryption, mode `0o600` | `src/secret_storage.py` |
| `sessions.json`, `memory.json`, `memory_doc.md`, `settings.json`, `features.json`, `auth.json`, `user_prefs.json`, `vault.json`, `integrations.json` | Parallel JSON-file stores (not SQL) | `src/constants.py`, `core/database.py:1073-1150` |
| `deep_research/`, `search_cache/`, `search/`, `email_urgency_cache/`, `emoji_cache/` | Feature-specific caches | grep of `data_path(...)` call sites repo-wide |

### Env-var overrides summary

| Env var | Effect |
|---|---|
| `APOLLO_DATA_DIR` | Overrides the entire data root (highest precedence) |
| `DATA_DIR` | Same, secondary name |
| `DATABASE_URL` | Overrides the SQLite URL entirely (any SQLAlchemy dialect) |
| `CHROMA_PERSIST_DIR` | Overrides just the embedded Chroma store path |
| `CHROMADB_HOST` / `CHROMADB_PORT` | Switches Chroma to HTTP client mode |
| `APOLLO_MAIL_ATTACHMENTS_DIR` | Overrides the mail-attachments extraction dir |
| `FASTEMBED_CACHE_PATH` | Overrides the local ONNX embedding cache dir |
| `DATA_*` (e.g. `DATA_UPLOADS_DIR`) | A **second, parallel** override layer via `src/config.py`'s pydantic-settings `DataConfig` (`env_prefix="DATA_"`) — only affects the 4 fields that class re-derives (`data_dir`, `uploads_dir`, `personal_dir`, `runbook_dir`); largely redundant with `runtime_paths.py` and worth treating as legacy/secondary, not the primary override mechanism |

### Logs — not under `data_root()`, and inconsistent between subsystems

The main FastAPI process logs to **stdout only** — `logging.basicConfig(level=logging.INFO, ...)`
in `app.py`, no `FileHandler`/`RotatingFileHandler` found anywhere in
`src/`, `core/`, `routes/`, `services/` for the primary app. Two sidecar
services keep their own, differently-rooted log files:

```python
# services/paperclip/runtime.py:111-120
def runtime_log_path(env=None) -> Path:
    configured = env.get("PAPERCLIP_LOG_PATH")
    if configured:
        return Path(configured).expanduser()
    data_dir = env.get("APOLLO_DATA_DIR") or env.get("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().parent / "logs" / "paperclip.log"
    return Path.home() / ".apollo" / "paperclip.log"
```

Paperclip logs to `<data_root's parent>/logs/paperclip.log` (note: reads the
two raw env vars directly, not the full `runtime_paths.data_root()`
precedence chain) or `~/.apollo/paperclip.log`, override `PAPERCLIP_LOG_PATH`.
SearXNG logs to `<repo_root>/logs/searxng.log` (`services/searxng/runtime.py`)
— the **source checkout**, not the data root at all. There is no unified
application log directory.

---

## 7. Summary of genuine uncertainties / smells (flagged inline above, collected here)

- **UNCERTAIN:** `gallery_people` appears in `_migrate_assign_legacy_owner`'s
  hardcoded table list (`core/database.py:1113`) but no such table/model
  exists anywhere — dead reference, silently swallowed.
- **Confirmed bug:** `routes/admin_wipe_routes.py` imports
  `get_memory_vector_store` from `src.memory_vector`, which does not exist;
  the memory-wipe route silently fails to clear the `apollo_memories`
  ChromaDB collection.
- `memories.timestamp` is a Unix `Integer`, the sole exception to every other
  table's naive-UTC `DateTime` convention (explicitly called out in a code
  comment as a known smell, left alone to avoid a risky migration).
- `email_accounts.imap_password`/`smtp_password` and `model_endpoints.api_key`/
  `signatures.data_png` both claim to be "encrypted at rest" but use two
  different mechanisms (raw-SQL migration vs. `EncryptedText` type decorator)
  — functionally equivalent today, but a future column added to
  `email_accounts` would NOT get encryption automatically the way a new
  `EncryptedText` column would.
- `src/memory_vector.py` and `services/memory/memory_vector.py` are
  byte-identical duplicate files — a refactor risk (a fix applied to one
  silently doesn't apply to the other).
- `<data_root>/rag/` is created on disk but not actually used as a Chroma
  store path — only `<data_root>/chroma/` is real.
- **UNCERTAIN / significant:** the `memories` SQL table (§2) appears to be
  effectively unused by the live memory feature. `services/memory/memory.py`'s
  `MemoryManager` (used throughout `routes/memory_routes.py`) reads/writes
  `<data_root>/memory.json` directly (`self.memory_file = os.path.join(data_dir,
  "memory.json")`, `services/memory/memory.py:37`), not the `Memory` ORM
  model. The `memories` table and its two indexes may be dead weight from an
  earlier implementation, or used by a narrower code path not covered by this
  pass — worth confirming with a repo-wide grep for `SessionLocal` +
  `Memory` usage before assuming either the table or the JSON file is safe to
  remove.

# Apollo — Database Schema & Data Models

This document specifies Apollo's complete persistence layer so the system can be rebuilt from scratch. The single source of truth for the relational schema is `core/database.py` (SQLAlchemy declarative models on SQLite). Secondary JSON/file stores hold auth, prefs, and vectors. All paths are relative to the repo root `/Users/Antman/Apollo` unless absolute.

## 1. Engine, Session Factory, and Base

```python
# core/database.py
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
```

- Default DB file: `data/app.db` (SQLite); `DATABASE_URL` env var overrides. Foreign keys are enforced globally via an `Engine`-class event listener:

```python
# core/database.py
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

- Timestamps: `TimestampMixin` adds `created_at` / `updated_at` (`DateTime`, `nullable=False`, default `_utcnow()` = naive UTC; `updated_at` has `onupdate=_utcnow`). `core/database.py` ends with a module-level `init_db()` call, so importing the module creates/migrates the schema.

### EncryptedText column type

```python
# core/database.py
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

Values are Fernet-encrypted with an `enc:` prefix (`src/secret_storage.py`, §5). Used by `ModelEndpoint.api_key` and `Signature.data_png`/`svg`; email passwords are plain `String` columns encrypted by a startup migration with the same scheme.

## 2. SQLAlchemy Models (CREATE-TABLE equivalents)

All `id` primary keys are `VARCHAR` (UUIDs / short ids), indexed. `TS` below means the TimestampMixin pair `created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL`.

### 2.1 Session — `sessions`

```sql
CREATE TABLE sessions (
    id VARCHAR PRIMARY KEY,              -- indexed
    name VARCHAR NOT NULL,
    endpoint_url VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    owner VARCHAR,                       -- indexed; username, NULL = legacy/shared
    rag BOOLEAN DEFAULT 0, archived BOOLEAN DEFAULT 0,
    folder VARCHAR, headers JSON DEFAULT '{}',     -- headers = endpoint auth headers
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    last_accessed DATETIME,              -- default func.now(), onupdate func.now()
    last_message_at DATETIME,            -- set ONLY when a message is persisted
    is_important BOOLEAN DEFAULT 0, message_count INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0, total_output_tokens INTEGER DEFAULT 0,
    mode VARCHAR,                        -- 'agent' | 'chat' | 'research' | 'research_pending'
    crew_member_id VARCHAR               -- links to crew_members.id (no FK)
);
CREATE INDEX ix_sessions_active ON sessions(archived, last_accessed);
CREATE INDEX ix_sessions_search ON sessions(name, archived);
CREATE INDEX ix_sessions_last_message_at ON sessions(archived, last_message_at); -- migration-created
```

Relationships: `messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")`. `to_dict()` serializes id/name/model/endpoint_url/rag/archived/timestamps/message_count/is_important/folder/token totals/crew_member_id. `last_message_at` is deliberately NOT `onupdate` — renames and model swaps bump only `updated_at`.

### 2.2 ChatMessage — `chat_messages`

```sql
CREATE TABLE chat_messages (
    id VARCHAR PRIMARY KEY,
    session_id VARCHAR NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,  -- indexed
    role VARCHAR NOT NULL, content TEXT NOT NULL,
    metadata TEXT,                       -- python attr: meta_data; JSON string (metrics etc.)
    timestamp DATETIME                   -- default _utcnow
);
CREATE INDEX ix_messages_session_time ON chat_messages(session_id, timestamp);
```

### 2.3 Document — `documents` / DocumentVersion — `document_versions`

```sql
CREATE TABLE documents (
    id VARCHAR PRIMARY KEY,
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,  -- indexed
    title VARCHAR NOT NULL DEFAULT 'Untitled',
    language VARCHAR,                    -- "python", "markdown", "text", ...
    current_content TEXT NOT NULL DEFAULT '', version_count INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT 1,         -- "open in a session"
    archived BOOLEAN DEFAULT 0,          -- soft-archive (hidden from Library)
    owner VARCHAR,                       -- indexed; owned directly (robust vs session SET NULL)
    tidy_verdict VARCHAR,                -- "keep" | "junk" | NULL
    source_email_uid VARCHAR, source_email_folder VARCHAR,      -- provenance for Sign-and-Reply
    source_email_account_id VARCHAR,
    source_email_message_id VARCHAR,     -- indexed (ix_documents_source_email_message_id)
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);

CREATE TABLE document_versions (
    id VARCHAR PRIMARY KEY,
    document_id VARCHAR NOT NULL REFERENCES documents(id) ON DELETE CASCADE, -- indexed
    version_number INTEGER NOT NULL, content TEXT NOT NULL,
    summary VARCHAR,                     -- edit description
    source VARCHAR DEFAULT 'ai',         -- "ai" | "user"
    created_at DATETIME
);
```

`Document.versions` cascades `all, delete-orphan` (ordered by `version_number`); `Document.session` uses `backref("documents", cascade="save-update, merge")` — deleting a session does NOT delete its documents (FK SET NULL).

### 2.4 Gallery — `gallery_albums`, `gallery_images`

```sql
CREATE TABLE gallery_albums (
    id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
    description TEXT DEFAULT '', cover_id VARCHAR, -- cover_id = GalleryImage.id of cover
    owner VARCHAR,                       -- indexed
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);

CREATE TABLE gallery_images (
    id VARCHAR PRIMARY KEY,
    filename VARCHAR NOT NULL UNIQUE,
    prompt TEXT NOT NULL DEFAULT '',
    model VARCHAR, size VARCHAR, quality VARCHAR,
    tags VARCHAR DEFAULT '', ai_tags TEXT DEFAULT '',          -- comma-separated
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,      -- indexed
    album_id VARCHAR REFERENCES gallery_albums(id) ON DELETE SET NULL,  -- indexed
    owner VARCHAR,                       -- indexed
    is_active BOOLEAN DEFAULT 1, favorite BOOLEAN DEFAULT 0,
    file_hash VARCHAR(64),               -- SHA-256, indexed
    taken_at DATETIME,                   -- EXIF DateTimeOriginal, indexed
    camera_make VARCHAR, camera_model VARCHAR,
    gps_lat VARCHAR, gps_lng VARCHAR,    -- strings for precision
    width INTEGER, height INTEGER, file_size INTEGER,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX ix_gallery_images_tags   ON gallery_images(tags);
CREATE INDEX ix_gallery_images_model  ON gallery_images(model);
CREATE INDEX ix_gallery_images_active ON gallery_images(is_active, created_at);
```

Note: the legacy-owner sweep also lists a `gallery_people` table, but no model for it exists in `core/database.py` (sweep is guarded by a `PRAGMA table_info` existence check, so it is a no-op).

### 2.5 EmailAccount — `email_accounts`

```sql
CREATE TABLE email_accounts (
    id VARCHAR PRIMARY KEY,
    owner VARCHAR,                       -- indexed; one is_default=1 row per owner
    name VARCHAR NOT NULL,               -- "Work", "Personal", ...
    is_default BOOLEAN NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT 1,
    imap_host VARCHAR DEFAULT '', imap_port INTEGER DEFAULT 993, imap_starttls BOOLEAN DEFAULT 1,
    imap_user VARCHAR DEFAULT '', imap_password VARCHAR DEFAULT '',  -- enc: Fernet at rest
    smtp_host VARCHAR DEFAULT '', smtp_port INTEGER DEFAULT 465,
    smtp_security VARCHAR DEFAULT 'ssl', -- ssl | starttls | none
    smtp_user VARCHAR DEFAULT '', smtp_password VARCHAR DEFAULT '',  -- enc: Fernet at rest
    from_address VARCHAR DEFAULT '',
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX ix_email_accounts_owner_default ON email_accounts(owner, is_default);
```

### 2.6 ModelEndpoint — `model_endpoints`

```sql
CREATE TABLE model_endpoints (
    id VARCHAR PRIMARY KEY,              -- str(uuid4())[:8]
    name VARCHAR NOT NULL,               -- "Local vLLM", "OpenRouter"
    base_url VARCHAR NOT NULL,           -- "http://localhost:8002/v1"
    api_key TEXT,                        -- EncryptedText (enc: prefix)
    is_enabled BOOLEAN DEFAULT 1,
    hidden_models TEXT,                  -- JSON list of model IDs that failed probing
    cached_models TEXT,                  -- JSON list of last-known model IDs
    model_type VARCHAR DEFAULT 'llm',    -- "llm" | "image"
    supports_tools BOOLEAN,              -- NULL = unknown (fallback heuristic in agent_loop.py)
    owner VARCHAR,                       -- indexed; NULL = shared (visible to all)
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
```

### 2.7 McpServer — `mcp_servers`

```sql
CREATE TABLE mcp_servers (
    id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
    transport VARCHAR NOT NULL DEFAULT 'stdio',   -- "stdio" | "sse"
    command VARCHAR,                     -- stdio: executable path
    args TEXT,                           -- JSON array of args
    env TEXT,                            -- JSON object of env vars
    url VARCHAR, is_enabled BOOLEAN DEFAULT 1,     -- url for SSE transport
    oauth_config TEXT,                   -- JSON: provider, keys_file, token_file, scopes
    disabled_tools TEXT,                 -- JSON array of tool names hidden from LLM
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
```

### 2.8 Comparison — `comparisons` / Signature — `signatures`

```sql
CREATE TABLE comparisons (
    id VARCHAR PRIMARY KEY,
    session_id VARCHAR, owner VARCHAR,   -- owner indexed
    prompt TEXT NOT NULL,
    model_a VARCHAR NOT NULL, model_b VARCHAR NOT NULL,
    endpoint_a VARCHAR NOT NULL, endpoint_b VARCHAR NOT NULL,
    response_a TEXT, response_b TEXT,
    metrics_a TEXT, metrics_b TEXT,      -- JSON strings
    winner VARCHAR,                      -- "a" | "b" | "tie" | NULL
    is_blind BOOLEAN DEFAULT 1,
    blind_mapping TEXT,                  -- JSON {"left": "a"/"b", "right": "a"/"b"}
    voted_at DATETIME,                   -- indexed (ix_comparisons_voted_at)
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);

CREATE TABLE signatures (
    id VARCHAR PRIMARY KEY, owner VARCHAR,         -- owner indexed
    name VARCHAR NOT NULL DEFAULT 'Signature',
    data_png TEXT NOT NULL,              -- EncryptedText: base64 PNG (no data: prefix)
    width INTEGER, height INTEGER,
    svg TEXT,                            -- EncryptedText: vector signature (reserved)
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
```

### 2.9 ApiToken — `api_tokens` / Webhook — `webhooks`

```sql
CREATE TABLE api_tokens (
    id VARCHAR PRIMARY KEY, owner VARCHAR,         -- owner indexed
    name VARCHAR NOT NULL,
    token_hash VARCHAR NOT NULL,         -- bcrypt of full "ody_..." token
    token_prefix VARCHAR NOT NULL,       -- first 8 chars for display + cache lookup
    scopes VARCHAR NOT NULL DEFAULT 'chat',
    is_active BOOLEAN DEFAULT 1, last_used_at DATETIME,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);

CREATE TABLE webhooks (
    id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, url VARCHAR NOT NULL,
    secret VARCHAR,                      -- HMAC-SHA256 signing secret
    events VARCHAR NOT NULL,             -- comma-separated event types
    is_active BOOLEAN DEFAULT 1, last_triggered_at DATETIME,
    last_status_code INTEGER, last_error VARCHAR,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
```

### 2.10 UserTool — `user_tools` / UserToolData — `user_tool_data`

```sql
CREATE TABLE user_tools (
    id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
    description TEXT, icon VARCHAR DEFAULT '',
    html_content TEXT NOT NULL,
    scope VARCHAR NOT NULL DEFAULT 'global',       -- "global" or a session_id
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    owner VARCHAR,                       -- indexed
    is_pinned BOOLEAN DEFAULT 0, is_active BOOLEAN DEFAULT 1,
    version INTEGER DEFAULT 1, author VARCHAR DEFAULT 'ai',
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX ix_user_tools_scope  ON user_tools(scope);
CREATE INDEX ix_user_tools_active ON user_tools(is_active);

CREATE TABLE user_tool_data (                      -- key-value persistence per tool
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id VARCHAR NOT NULL REFERENCES user_tools(id) ON DELETE CASCADE,
    key VARCHAR NOT NULL, value TEXT, created_at DATETIME, updated_at DATETIME
);
CREATE UNIQUE INDEX ix_user_tool_data_tool_key ON user_tool_data(tool_id, key);
```

### 2.11 CrewMember — `crew_members`

```sql
CREATE TABLE crew_members (
    id VARCHAR PRIMARY KEY, owner VARCHAR,         -- owner indexed
    name VARCHAR NOT NULL, avatar VARCHAR,
    user_name VARCHAR,                   -- what they call the user
    personality TEXT,                    -- system prompt
    model VARCHAR, endpoint_url VARCHAR, greeting TEXT,
    enabled_tools TEXT,                  -- JSON array or "all"
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT 1, sort_order INTEGER DEFAULT 0,
    is_default_assistant BOOLEAN DEFAULT 0,        -- singleton per-owner assistant
    timezone VARCHAR,                    -- IANA tz (e.g. "America/New_York") for check-ins
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
```

### 2.12 ScheduledTask — `scheduled_tasks` / TaskRun — `task_runs`

```sql
CREATE TABLE scheduled_tasks (
    id VARCHAR PRIMARY KEY, owner VARCHAR,         -- owner indexed
    name VARCHAR NOT NULL DEFAULT 'Untitled Task',
    prompt TEXT,                         -- LLM prompt (task_type="llm")
    task_type VARCHAR DEFAULT 'llm',     -- "llm" | "action"
    action VARCHAR,                      -- builtin action name (task_type="action")
    schedule VARCHAR,                    -- "once" | "daily" | "weekly" | "monthly"
    scheduled_time VARCHAR,              -- "HH:MM" 24h UTC
    scheduled_day INTEGER, scheduled_date DATETIME,  -- 0=Mon/day-of-month; exact dt for "once"
    trigger_type VARCHAR DEFAULT 'schedule',  -- "schedule" | "event"
    trigger_event VARCHAR,               -- "session_created", "message_sent", ...
    trigger_count INTEGER, trigger_counter INTEGER DEFAULT 0,
    next_run DATETIME, last_run DATETIME,          -- next_run indexed
    status VARCHAR DEFAULT 'active',     -- "active" | "paused" | "completed"
    output_target VARCHAR DEFAULT 'session',
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    model VARCHAR, endpoint_url VARCHAR, run_count INTEGER DEFAULT 0,
    cron_expression VARCHAR,             -- e.g. "*/5 * * * *"
    then_task_id VARCHAR REFERENCES scheduled_tasks(id) ON DELETE SET NULL,  -- chaining
    webhook_token VARCHAR UNIQUE,        -- path-embedded credential for /webhook routes
    crew_member_id VARCHAR, character_id VARCHAR,  -- character_id legacy; FK dropped
    max_steps INTEGER,                   -- max agent loop iterations (NULL=unlimited)
    email_results BOOLEAN DEFAULT 1, notifications_enabled BOOLEAN DEFAULT 1,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX ix_scheduled_tasks_due   ON scheduled_tasks(status, next_run);
CREATE INDEX ix_scheduled_tasks_event ON scheduled_tasks(trigger_type, trigger_event, status);

CREATE TABLE task_runs (
    id VARCHAR PRIMARY KEY,
    task_id VARCHAR NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    started_at DATETIME NOT NULL, finished_at DATETIME,
    status VARCHAR DEFAULT 'running',    -- "running" | "success" | "error"
    result TEXT, error TEXT, tokens_used INTEGER,
    steps TEXT,                          -- JSON log of agent tool calls
    model VARCHAR                        -- model that actually ran (resolved at execution)
);
CREATE INDEX ix_task_runs_task ON task_runs(task_id, started_at);
```

`ScheduledTask.runs` backref cascades `all, delete-orphan`, ordered `started_at.desc()`. `then_task` is a self-referential relationship via `remote_side=[id]`.

### 2.13 Memory — `memories`

```sql
CREATE TABLE memories (
    id VARCHAR PRIMARY KEY, text TEXT NOT NULL,
    category VARCHAR DEFAULT 'fact',     -- fact|contact|task|preference|identity|project|goal
    source VARCHAR DEFAULT 'user',
    owner VARCHAR,                       -- indexed
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,  -- indexed
    timestamp INTEGER                    -- Unix epoch, default int(_utcnow().timestamp())
);
CREATE INDEX ix_memories_lookup  ON memories(category, timestamp);
CREATE INDEX ix_memories_session ON memories(session_id, timestamp);
```

### 2.14 Note — `notes`

```sql
CREATE TABLE notes (
    id VARCHAR PRIMARY KEY, owner VARCHAR,         -- owner indexed
    title VARCHAR DEFAULT '', content TEXT,
    items TEXT,                          -- JSON [{text, done}] for checklists
    note_type VARCHAR DEFAULT 'note',    -- "note" | "checklist"
    color VARCHAR, label VARCHAR,
    pinned BOOLEAN DEFAULT 0, archived BOOLEAN DEFAULT 0,
    due_date VARCHAR,
    source VARCHAR DEFAULT 'user',       -- "user" | "agent"
    session_id VARCHAR, sort_order INTEGER DEFAULT 0,   -- session_id has no FK
    image_url VARCHAR,                   -- relative upload path
    repeat VARCHAR DEFAULT 'none',       -- none|daily|weekly|monthly|yearly
    ai_classification TEXT,              -- JSON {kind, solvable, confidence, task_prompt, tools, items?}
    ai_content_hash VARCHAR,             -- gates re-classification
    agent_session_id VARCHAR,            -- session spawned by the note's "Agent" button
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
```

### 2.15 Calendar — `calendars`, `calendar_events`

```sql
CREATE TABLE calendars (
    id VARCHAR PRIMARY KEY, owner VARCHAR,         -- owner indexed
    name VARCHAR NOT NULL, color VARCHAR DEFAULT '#5b8abf',
    source VARCHAR DEFAULT 'local',      -- "local" | "timetree"
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);

CREATE TABLE calendar_events (
    uid VARCHAR PRIMARY KEY,             -- NOTE: pk column is `uid`, not `id`
    calendar_id VARCHAR NOT NULL REFERENCES calendars(id),  -- indexed; NO ondelete (ORM cascade)
    summary VARCHAR NOT NULL DEFAULT '', description TEXT DEFAULT '',
    location VARCHAR DEFAULT '',
    dtstart DATETIME NOT NULL,           -- indexed
    dtend DATETIME NOT NULL,
    all_day BOOLEAN DEFAULT 0, rrule VARCHAR DEFAULT '',
    is_utc BOOLEAN NOT NULL DEFAULT 0,   -- True = stored as UTC instants (Z-suffix on wire)
    color VARCHAR,                       -- per-event override
    status VARCHAR DEFAULT 'confirmed',  -- confirmed | cancelled
    importance VARCHAR DEFAULT 'normal', -- low | normal | high | critical
    event_type VARCHAR,                  -- work|personal|health|travel|meal|social|admin|other
    last_pinged DATETIME,                -- assistant ping de-dup
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
```

`CalendarCal.events` cascades `all, delete-orphan` (delete a calendar → its events go via ORM).

### 2.16 Integration — `integrations` / EditorDraft — `editor_drafts`

```sql
CREATE TABLE integrations (
    id VARCHAR PRIMARY KEY, owner VARCHAR,         -- owner indexed
    name VARCHAR NOT NULL,
    type VARCHAR NOT NULL,               -- "email" | "rss" | "webhook"
    config JSON, enabled BOOLEAN DEFAULT 1,        -- config is type-specific
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);

CREATE TABLE editor_drafts (             -- persisted gallery-editor projects
    id VARCHAR PRIMARY KEY, owner VARCHAR,         -- owner indexed
    name VARCHAR NOT NULL DEFAULT 'Untitled',
    source_image_id VARCHAR,             -- indexed; gallery photo the draft was opened from
    width INTEGER, height INTEGER,
    payload TEXT NOT NULL DEFAULT '',    -- full layer state JSON (base64 PNG dataURLs)
    thumbnail TEXT,                      -- ~128px data URL preview
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX ix_editor_drafts_owner_updated ON editor_drafts(owner, is_active, updated_at);
```

## 3. Migration Approach (ad-hoc, idempotent, startup-run)

There is no Alembic. `init_db()` runs `Base.metadata.create_all(bind=engine)` then ~30 guarded migration functions, in this exact order:

```python
# core/database.py (semicolon-joined here for brevity; one call per line in source)
def init_db():
    _migrate_model_endpoints()           # BEFORE create_all: drops old url-schema table
    Base.metadata.create_all(bind=engine)
    _migrate_add_hidden_models_column(); _migrate_add_cached_models_column()
    _migrate_add_notes_sort_order(); _migrate_add_model_type_column()
    _migrate_add_model_endpoint_owner_column(); _migrate_add_supports_tools_column()
    _migrate_add_task_run_model_column(); _migrate_add_owner_column()
    _migrate_add_document_archived_column(); _migrate_add_last_message_at_column()
    _migrate_add_folder_column(); _migrate_add_token_columns(); _migrate_add_mode_column()
    _migrate_add_multiuser_owner_columns(); _migrate_add_api_token_scopes_column()
    _migrate_backfill_document_owner_from_session()   # must precede legacy sweep
    _migrate_assign_legacy_owner(); _migrate_add_tidy_verdict()
    _migrate_add_doc_source_email_cols(); _migrate_add_oauth_config()
    _migrate_add_task_automation_columns(); _migrate_add_disabled_tools()
    _migrate_add_task_v2_columns(); _migrate_add_notifications_enabled()
    _migrate_drop_ping_notes_tasks(); _migrate_add_crew_member_id()
    _migrate_add_assistant_columns(); _migrate_add_email_smtp_security()
    _migrate_seed_email_account(); _migrate_add_calendar_metadata()
    _migrate_add_calendar_is_utc(); _migrate_encrypt_email_passwords()
    _migrate_encrypt_signatures(); _migrate_encrypt_endpoint_keys()
```

The canonical pattern — check `PRAGMA table_info`, `ALTER TABLE ... ADD COLUMN` only if missing — e.g. `_migrate_add_mode_column()` connects via `sqlite3`, reads `PRAGMA table_info(sessions)`, and executes `ALTER TABLE sessions ADD COLUMN mode TEXT` only when `"mode"` is absent. All migrations swallow exceptions with a `logger.warning` so a failed migration degrades rather than blocking startup.

Special cases:
- `_migrate_model_endpoints()` drops the whole table if it still has the old `url` column (recreated by `create_all`).
- `_migrate_add_task_automation_columns()` performs a full table rebuild (`RENAME TO _old_scheduled_tasks` → `CREATE TABLE` → `INSERT INTO ... SELECT` → `DROP`) when `prompt`/`schedule`/`scheduled_time` are still NOT NULL.
- `_migrate_assign_legacy_owner()` reads `data/auth.json`, picks the `is_admin: true` user (else first user), and sets `owner` on NULL-owner rows across 19 tables; it also migrates `data/memory.json` owners and converts a flat `data/user_prefs.json` into `{"_users": {<admin>: {...}}}`. It re-runs hourly via `_null_owner_sweep_loop()` in `app.py`.
- `_migrate_encrypt_email_passwords/_signatures/_endpoint_keys()` rewrite plaintext rows to `enc:`-prefixed Fernet ciphertext using raw SQL (so `EncryptedText` is not applied twice). Idempotent via `is_encrypted()`.

## 4. Helper Functions in core/database.py

`get_db()` (FastAPI dependency, yield/close); `get_db_session()` (context manager: commit/rollback/close); `bulk_insert_messages(session_id, messages)`; `cleanup_old_sessions(days=30)` (deletes archived, not-important sessions past cutoff); `get_session_stats()` / `get_detailed_stats()` (counts + DB file size MB); `update_session_last_accessed(session_id)`; `get_session_mode` / `set_session_mode` (best-effort, never raise); `get_session_by_id`; `archive_session`; `get_upcoming_events(owner, horizon_days=60, limit=40)` (joins CalendarEvent→CalendarCal, owner-scoped unless `owner is None`).

## 5. Secondary Stores (non-SQL)

| Store | Path | Format / contents |
|---|---|---|
| Users / auth config | `data/auth.json` | `{"users": {"<name>": {"password_hash": bcrypt, "created": ts, "is_admin": bool, "privileges": {...}, "totp_secret"?, "totp_enabled"?, "totp_backup_codes"?}}, "signup_enabled": bool}` (written atomically via `core/atomic_io.py`) |
| Browser session tokens | `data/sessions.json` | `{<token_hex64>: {"username": str, "expiry": epoch}}` — pruned on load |
| Per-user prefs | `data/user_prefs.json` | `{"_users": {"<name>": {...prefs...}}}` (`routes/prefs_routes.py`, `PREFS_FILE = os.path.join("data", "user_prefs.json")`) |
| Legacy memory text | `data/memory.json` | list of memory dicts with `owner` (`src/memory.py: MemoryManager.memory_file`) |
| Memory vectors + RAG | `data/chroma/` | Embedded ChromaDB `PersistentClient` (`src/chroma_client.py:_persist_dir()`; `CHROMA_PERSIST_DIR` or `CHROMADB_HOST` overrides; `src/memory_vector.py` keeps a collection for memories) |
| App settings | `data/settings.json` | flat JSON managed by `src/settings.py` (admin-edited via `/api/auth/settings`) |
| Fernet master key | `data/.app_key` | raw Fernet key, `chmod 0o600`, generated on first use (`src/secret_storage.py:_load_or_create_key`) |
| Paperclip auth secret | `~/.apollo/paperclip_secret` | hex token, 0600 (`services/paperclip/config.py:resolve_auth_secret`) |
| lmproxy shared token | `~/.apollo/paperclip_proxy_token` | hex token, 0600 (`resolve_proxy_token`) |
| Per-agent lmproxy tokens | `~/.apollo/paperclip_agent_tokens.json` | `{"pa-<hex48>": {"agent_id": str, "name": str}}`, 0600 (`services/paperclip/agent_tokens.py`) |
| Pinned Node runtime | `~/.apollo/.node/` | auto-provisioned for native Paperclip (`services/paperclip/node_bootstrap`) |
| Generated images | `data/generated_images/` | content-hash-named PNG/JPG/video files served by `/api/generated-image/{filename}` |

Encryption threat model (from `src/secret_storage.py` docstring): protects against SQLite-file exfiltration (stolen backup, leaked container layer); does **not** protect against process compromise — anyone who can read `data/.app_key` has plaintext. `decrypt()` returns `""` on `InvalidToken` so a rotated/corrupt key degrades to "unconfigured" instead of a 500.

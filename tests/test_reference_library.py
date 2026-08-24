"""Reference Library: parsers, guards, storage, search ranking, routes, tool.

The sponsor-row test is a real regression: the first parse of the live
public-apis README leaked 27 APILayer ad rows as if they were free APIs.
"""
import importlib.util
import sys
from unittest.mock import MagicMock

import pytest


def _has_real(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "sqlalchemy", "httpx"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi+sqlalchemy+httpx installed"
)


def _evict_stubs():
    for mod in list(sys.modules):
        if (mod in ("core.database", "services.reference_library", "routes.hub_routes")
                or mod.startswith("sqlalchemy")) and isinstance(sys.modules[mod], MagicMock):
            sys.modules.pop(mod)


# ── Parsers ──────────────────────────────────────────────────────────

# Mirrors the live README shape: an h3 sponsor block whose rows have only 3
# columns and carry campaign tags, then real category tables with 5 columns.
PUBLIC_APIS_MD = """
## APILayer APIs
### APIs Covered Under APILayer Suite!
| [IPstack](https://ipstack.com/?utm_source=Github&utm_campaign=Public-apis-repo) | Locate visitors by IP | [<img src="https://run.pstmn.io/button.svg">](https://god.gw.postman.com/run) |
| [Marketstack](https://marketstack.com/?utm_campaign=Public-apis-repo) | Stock data | [<img src="https://run.pstmn.io/button.svg">](https://god.gw.postman.com/run) |

### Animals
API | Description | Auth | HTTPS | CORS |
|---|---|---|---|---|
| [AdoptAPet](https://www.adoptapet.com/public/apis/pet_list.html) | Resource to help get pets adopted | `apiKey` | Yes | Yes |
| [Axolotl](https://theaxolotlapi.netlify.app/) | Collection of axolotl pictures | No | Yes | No |

### Weather
| [AccuWeather](https://developer.accuweather.com/apis) | Weather forecasts | `apiKey` | No | Unknown |
"""

BYOX_MD = """
#### Build your own `Database`
* [**C**: _Let's Build a Simple Database_](https://cstack.github.io/db_tutorial/)
* [**Python**: _DBDB: Dog Bed Database_](http://aosabook.org/en/500L/dbdb.html)
"""

BOOKS_MD = """
### Python
* [Automate the Boring Stuff](https://automatetheboringstuff.com) - Al Sweigart (HTML)
* [Think Python](https://greenteapress.com/thinkpython) - Allen B. Downey (PDF)
"""

ROADMAP_MD = """
### [View all Roadmaps](https://roadmap.sh) &nbsp;&middot;&nbsp; [Questions](https://roadmap.sh/questions)
- [Frontend Roadmap](https://roadmap.sh/frontend) / [Frontend Beginner Roadmap](https://roadmap.sh/frontend?r=frontend-beginner)
- [DevOps Roadmap](https://roadmap.sh/devops)
"""


def test_public_apis_parser_excludes_sponsor_rows():
    _evict_stubs()
    from services.reference_library import parse_source

    entries = parse_source("public-apis", [PUBLIC_APIS_MD])
    urls = " ".join(e["url"].lower() for e in entries)
    assert "apilayer" not in urls and "ipstack" not in urls and "marketstack" not in urls, \
        "sponsor/ad rows must never be served to the agent as free APIs"
    titles = {e["title"] for e in entries}
    assert titles == {"AdoptAPet", "Axolotl", "AccuWeather"}


def test_public_apis_parser_extracts_auth_metadata():
    _evict_stubs()
    from services.reference_library import parse_source

    by_title = {e["title"]: e for e in parse_source("public-apis", [PUBLIC_APIS_MD])}
    assert by_title["AdoptAPet"]["category"] == "Animals"
    assert by_title["AdoptAPet"]["meta"] == {"auth": "apiKey", "https": "Yes", "cors": "Yes"}
    # "No" auth is normalized to "none" so the agent can filter keyless APIs.
    assert by_title["Axolotl"]["meta"]["auth"] == "none"
    assert by_title["AccuWeather"]["category"] == "Weather"


def test_byox_parser():
    _evict_stubs()
    from services.reference_library import parse_source

    entries = parse_source("build-your-own-x", [BYOX_MD])
    assert len(entries) == 2
    assert entries[0]["category"] == "Database"
    assert entries[0]["meta"]["language"] == "C"


def test_book_parser():
    _evict_stubs()
    from services.reference_library import parse_source

    entries = parse_source("free-programming-books", [BOOKS_MD])
    assert {e["title"] for e in entries} == {"Automate the Boring Stuff", "Think Python"}
    assert entries[0]["category"] == "Python"
    assert "Al Sweigart" in entries[0]["description"]


def test_roadmap_parser_captures_every_link_on_a_line():
    _evict_stubs()
    from services.reference_library import parse_source

    entries = parse_source("developer-roadmap", [ROADMAP_MD])
    urls = {e["url"] for e in entries}
    # Beginner variant shares a line with the main roadmap — taking only the
    # first link per line silently halved the catalog.
    assert "https://roadmap.sh/frontend" in urls
    assert "https://roadmap.sh/frontend?r=frontend-beginner" in urls
    assert "https://roadmap.sh/devops" in urls
    # The nav heading is not a list item, so its links stay out.
    assert "https://roadmap.sh/questions" not in urls
    assert all(e["category"] == "Roadmaps" for e in entries)


def test_parser_dedupes_by_url():
    _evict_stubs()
    from services.reference_library import parse_source

    doubled = BOOKS_MD + BOOKS_MD
    assert len(parse_source("free-programming-books", [doubled])) == 2


def test_unknown_source_rejected():
    _evict_stubs()
    from services.reference_library import parse_source

    with pytest.raises(ValueError):
        parse_source("not-a-source", ["# x"])


# ── URL guards ───────────────────────────────────────────────────────

def test_raw_url_validation():
    _evict_stubs()
    from services.reference_library import _raw_url

    ok = _raw_url("public-apis/public-apis", "master", "README.md")
    assert ok == "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
    with pytest.raises(ValueError):
        _raw_url("no-slash", "master", "README.md")
    with pytest.raises(ValueError):
        _raw_url("org/repo", "master", "../../etc/passwd.md")
    with pytest.raises(ValueError):
        _raw_url("org/repo", "master", "script.sh")
    # A ref cannot smuggle path segments.
    assert "//" not in _raw_url("org/repo", "../evil", "README.md").removeprefix("https://")


# ── Storage + search ─────────────────────────────────────────────────

@pytest.fixture
def store(monkeypatch):
    _evict_stubs()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from core.database import Base
    import services.reference_library as rl

    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    import core.database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(rl, "fetch_markdown",
                        lambda repo, ref, path, timeout=30: PUBLIC_APIS_MD)
    return rl


def test_install_search_and_remove(store):
    res = store.install_source("public-apis")
    assert res["installed"] == 3

    hits = store.search("weather")
    assert hits and hits[0]["title"] == "AccuWeather"
    assert hits[0]["meta"]["auth"] == "apiKey"

    # kind filter and miss behavior
    assert store.search("weather", kind="book") == []
    assert store.search("") == []

    status = {s["id"]: s for s in store.source_status()}
    assert status["public-apis"]["installed"] == 3
    assert status["public-apis"]["agent_actionable"] is True

    # Reinstall replaces rather than duplicates.
    store.install_source("public-apis")
    assert store.source_status()[0]["installed"] == 3

    assert store.remove_source("public-apis")["removed"] == 3
    assert store.search("weather") == []


def test_search_ranks_title_matches_first(store):
    store.install_source("public-apis")
    # "pets" appears only in AdoptAPet's description; "Axolotl" is a title.
    assert store.search("adoptapet")[0]["title"] == "AdoptAPet"
    desc_hit = store.search("adopted")
    assert desc_hit and desc_hit[0]["title"] == "AdoptAPet"


def test_install_rejects_empty_parse(store, monkeypatch):
    monkeypatch.setattr(store, "fetch_markdown",
                        lambda repo, ref, path, timeout=30: "# nothing here")
    with pytest.raises(ValueError, match="no entries parsed"):
        store.install_source("public-apis")


def test_format_for_agent():
    _evict_stubs()
    from services.reference_library import format_for_agent

    empty = format_for_agent([], "weather")
    assert "No reference entries" in empty and "Reference Library" in empty
    text = format_for_agent([{
        "source": "public-apis", "kind": "api", "category": "Weather",
        "title": "AccuWeather", "url": "https://x.test", "description": "Forecasts",
        "meta": {"auth": "apiKey", "https": "Yes", "cors": "No"},
    }], "weather")
    assert "AccuWeather" in text and "https://x.test" in text and "apiKey" in text


# ── Routes ───────────────────────────────────────────────────────────

def test_reference_routes(monkeypatch):
    _evict_stubs()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.hub_routes as routes_mod

    monkeypatch.setattr(routes_mod, "require_admin", lambda r: None)
    monkeypatch.setattr(routes_mod, "ref_sources",
                        lambda: [{"id": "public-apis", "installed": 3}])
    monkeypatch.setattr(routes_mod, "ref_install",
                        lambda s: {"ok": True, "source": s, "installed": 3})
    monkeypatch.setattr(routes_mod, "ref_remove", lambda s: {"ok": True, "removed": 3})
    monkeypatch.setattr(routes_mod, "ref_search",
                        lambda q, source=None, kind=None, limit=20: [{"title": "AccuWeather"}])

    app = FastAPI()
    app.include_router(routes_mod.setup_hub_routes())
    c = TestClient(app)

    assert c.get("/api/hub/reference/sources").json()["sources"][0]["installed"] == 3
    assert c.post("/api/hub/reference/install", json={"source": "public-apis"}).json()["installed"] == 3
    assert c.post("/api/hub/reference/remove", json={"source": "public-apis"}).json()["removed"] == 3
    r = c.get("/api/hub/reference/search", params={"q": "weather"})
    assert r.json()["count"] == 1


def test_reference_install_bad_source_is_400(monkeypatch):
    _evict_stubs()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.hub_routes as routes_mod

    def boom(source):
        raise ValueError("unknown source: nope")
    monkeypatch.setattr(routes_mod, "require_admin", lambda r: None)
    monkeypatch.setattr(routes_mod, "ref_install", boom)

    app = FastAPI()
    app.include_router(routes_mod.setup_hub_routes())
    assert TestClient(app).post("/api/hub/reference/install",
                                json={"source": "nope"}).status_code == 400


# ── Agent tool ───────────────────────────────────────────────────────

def test_reference_search_tool_dispatch(monkeypatch):
    _evict_stubs()
    import asyncio
    import src.tool_execution as te
    import services.reference_library as rl

    monkeypatch.setattr(rl, "search",
                        lambda q, kind=None, limit=15: [{
                            "source": "public-apis", "kind": "api", "category": "Weather",
                            "title": "AccuWeather", "url": "https://x.test",
                            "description": "Forecasts", "meta": {"auth": "apiKey"},
                        }])

    class Block:
        tool_type = "reference_search"
        content = "weather"

    desc, result = asyncio.run(te.execute_tool_block(Block()))
    assert result["exit_code"] == 0
    assert "AccuWeather" in result["output"]
    assert "reference_search" in desc


def test_reference_search_tool_accepts_json_and_rejects_empty(monkeypatch):
    _evict_stubs()
    import asyncio
    import src.tool_execution as te
    import services.reference_library as rl

    seen = {}
    monkeypatch.setattr(rl, "search",
                        lambda q, kind=None, limit=15: seen.update(q=q, kind=kind) or [])

    class JsonBlock:
        tool_type = "reference_search"
        content = '{"query": "rust", "kind": "book"}'

    asyncio.run(te.execute_tool_block(JsonBlock()))
    assert seen == {"q": "rust", "kind": "book"}

    class EmptyBlock:
        tool_type = "reference_search"
        content = "   "

    _desc, result = asyncio.run(te.execute_tool_block(EmptyBlock()))
    assert result["exit_code"] == 1


def test_reference_search_is_not_gated_as_mutating():
    _evict_stubs()
    from src.tool_execution import _MUTATING_TOOLS
    # Read-only lookup: observe autonomy mode must not block it.
    assert "reference_search" not in _MUTATING_TOOLS


def test_tool_schema_registered():
    _evict_stubs()
    # Import through agent_tools: src.tool_schemas and src.agent_tools import
    # each other, and the cycle only resolves when entered from this side
    # (the order app.py uses). Importing tool_schemas directly first raises.
    import src.agent_tools  # noqa: F401
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {t["function"]["name"] for t in FUNCTION_TOOL_SCHEMAS if "function" in t}
    assert "reference_search" in names
    schema = next(t for t in FUNCTION_TOOL_SCHEMAS
                  if t.get("function", {}).get("name") == "reference_search")
    props = schema["function"]["parameters"]["properties"]
    assert "query" in props and "kind" in props
    assert schema["function"]["parameters"]["required"] == ["query"]

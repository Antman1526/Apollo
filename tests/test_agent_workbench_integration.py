import asyncio
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

from routes.integration_routes import setup_integration_routes
from services.integrations import agent_workbench
from src import ralph_loop


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


def _load_apollo_integrations():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "apollo-integrations"
    loader = SourceFileLoader("apollo_integrations", str(script_path))
    spec = importlib.util.spec_from_loader("apollo_integrations", loader)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_agent_workbench_status_composes_agent_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_workbench.browser_use_verifier, "status", lambda app_base_url=None: {
        "available": True,
        "python": "python",
        "llm_provider": "local",
        "local_model": {"model": "openai/llama3", "base_url": f"{app_base_url}/lmproxy/v1", "api_key_present": True},
    })
    monkeypatch.setattr(agent_workbench.crawl4ai_adapter, "status", lambda: {
        "available": True,
        "package": "crawl4ai",
    })
    monkeypatch.setattr(agent_workbench.embedded_browser, "status", lambda: {
        "available": True,
        "package": "playwright",
        "engine": "chromium",
        "headless": True,
    })

    paths = ralph_loop.init_workspace(tmp_path / "ralph")
    out = agent_workbench.status(
        app_base_url="http://127.0.0.1:7000",
        ralph_root=paths.root,
        paperclip_status={"enabled": True, "reachable": True, "mode": "native", "browser_url": "http://pc"},
    )

    assert out["ok"] is True
    assert out["ready_count"] == out["total"] == 5
    assert out["components"]["paperclip"]["state"] == "ready"
    assert out["components"]["embedded_browser"]["engine"] == "chromium"
    assert out["components"]["browser_use"]["local_model"]["model"] == "openai/llama3"
    assert out["components"]["ralph"]["summary"]["next"] == "story-1"


def test_agent_workbench_status_reports_setup_next_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_workbench.browser_use_verifier, "status", lambda app_base_url=None: {
        "available": False,
        "python": "python",
        "llm_provider": "local",
        "local_model": {"model": "openai/local"},
    })
    monkeypatch.setattr(agent_workbench.crawl4ai_adapter, "status", lambda: {"available": False})
    monkeypatch.setattr(agent_workbench.embedded_browser, "status", lambda: {
        "available": False,
        "install_hint": "pip install playwright && python -m playwright install chromium",
    })

    out = agent_workbench.status(
        app_base_url="http://127.0.0.1:7000",
        ralph_root=tmp_path / "missing-ralph",
        paperclip_status={"enabled": False, "reachable": None},
    )

    assert out["ok"] is False
    assert out["components"]["paperclip"]["state"] == "needs_setup"
    assert out["components"]["embedded_browser"]["next_step"].startswith("pip install playwright")
    assert out["components"]["browser_use"]["next_step"] == "Run scripts/setup-browser-use-env"
    assert out["components"]["ralph"]["next_step"] == "Run scripts/apollo-ralph init"


def test_agent_workbench_route_uses_request_base_url(monkeypatch):
    async def fake_paperclip_status():
        return {"enabled": True, "reachable": True, "mode": "native"}

    captured = {}

    def fake_status(*, app_base_url=None, paperclip_status=None):
        captured["app_base_url"] = app_base_url
        captured["paperclip_status"] = paperclip_status
        return {"ok": True}

    monkeypatch.setattr("routes.integration_routes.agent_workbench.status", fake_status)
    router = setup_integration_routes(fake_paperclip_status)
    target = _route(router, "/api/integrations/agent-workbench/status", "GET")
    request = SimpleNamespace(base_url="http://127.0.0.1:7002/")

    out = asyncio.run(target(request=request))

    assert out == {"ok": True}
    assert captured == {
        "app_base_url": "http://127.0.0.1:7002",
        "paperclip_status": {"enabled": True, "reachable": True, "mode": "native"},
    }


def test_apollo_integrations_cli_checks_enabled_paperclip(monkeypatch):
    cli = _load_apollo_integrations()

    monkeypatch.setattr(cli, "load_paperclip_config", lambda: SimpleNamespace(
        enabled=True,
        mode="native",
        url="http://paperclip",
        browser_url="http://localhost:3100",
        model_endpoint="apollo",
    ))
    monkeypatch.setattr(cli, "_paperclip_reachable", lambda url, timeout: url == "http://paperclip" and timeout == 0.2)

    out = cli._paperclip_status(0.2)

    assert out["enabled"] is True
    assert out["reachable"] is True


def test_apollo_integrations_cli_skips_disabled_paperclip_probe(monkeypatch):
    cli = _load_apollo_integrations()

    monkeypatch.setattr(cli, "load_paperclip_config", lambda: SimpleNamespace(
        enabled=False,
        mode="docker",
        url="http://paperclip",
        browser_url="http://localhost:3100",
        model_endpoint="ollama",
    ))
    monkeypatch.setattr(cli, "_paperclip_reachable", lambda url, timeout: (_ for _ in ()).throw(AssertionError("should not probe disabled Paperclip")))

    out = cli._paperclip_status(0.2)

    assert out["enabled"] is False
    assert out["reachable"] is None

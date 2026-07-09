"""Web/loopback tools: api_call, browser, and the internal app_api bridge."""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from src.tools._common import _parse_tool_args, _internal_headers
from src.tools.cookbook import _COOKBOOK_BASE

logger = logging.getLogger(__name__)




# ---------------------------------------------------------------------------
# API call tool
# ---------------------------------------------------------------------------

async def do_api_call(content: str) -> Dict:
    """Execute an API call to a registered integration."""
    from src.integrations import execute_api_call, load_integrations
    try:
        args = json.loads(content)
    except json.JSONDecodeError:
        # Try line-based format: integration\nmethod path\nbody
        lines = content.strip().split("\n")
        args = {"integration": lines[0].strip() if lines else ""}
        if len(lines) > 1:
            parts = lines[1].strip().split(" ", 1)
            args["method"] = parts[0] if parts else "GET"
            args["path"] = parts[1] if len(parts) > 1 else "/"
        if len(lines) > 2:
            try:
                args["body"] = json.loads("\n".join(lines[2:]))
            except json.JSONDecodeError:
                pass

    integration_name = args.get("integration", "")
    integrations = load_integrations()
    intg = next((i for i in integrations if i["id"] == integration_name
                 or i["name"].lower() == integration_name.lower()), None)
    if not intg:
        available = ", ".join(i["name"] for i in integrations if i.get("enabled", True))
        return {"error": f"No integration matching '{integration_name}'. Available: {available or 'none configured'}", "exit_code": 1}

    return await execute_api_call(
        intg["id"],
        args.get("method", "GET"),
        args.get("path", "/"),
        params=args.get("params"),
        body=args.get("body"),
        extra_headers=args.get("headers"),
    )



async def do_browser(content: str, owner: Optional[str] = None) -> Dict:
    """Control Apollo's shared browser session.

    JSON contract:
      {"action": "navigate", "url": "http://localhost:3000"}
      {"action": "getVisibleText"}
      {"action": "click", "selector": "button[type=submit]"}
      {"action": "type", "selector": "input[name=q]", "text": "Apollo"}
    """
    try:
        args = _parse_tool_args(content)
    except ValueError as exc:
        return {"error": f"browser: invalid JSON: {exc}", "exit_code": 1}

    from services.browser import embedded_browser

    action = str(args.get("action") or args.get("method") or "").strip()
    action_key = action.replace("_", "").replace("-", "").lower()
    try:
        if action_key == "navigate":
            result = await embedded_browser.session.navigate(str(args.get("url") or args.get("target") or ""))
        elif action_key in {"getcurrenturl", "current", "url"}:
            result = await embedded_browser.session.get_current_url()
        elif action_key in {"getpagehtml", "html"}:
            result = await embedded_browser.session.get_page_html()
        elif action_key in {"getvisibletext", "text"}:
            result = await embedded_browser.session.get_visible_text()
        elif action_key in {"executescript", "execute", "script"}:
            result = await embedded_browser.session.execute_script(str(args.get("script") or args.get("js") or ""))
        elif action_key == "screenshot":
            result = await embedded_browser.session.screenshot(full_page=bool(args.get("full_page") or args.get("fullPage")))
        elif action_key in {"waitforselector", "wait"}:
            result = await embedded_browser.session.wait_for_selector(
                str(args.get("selector") or args.get("sel") or ""),
                int(args.get("timeout_ms") or args.get("timeoutMs") or 15_000),
            )
        elif action_key == "click":
            result = await embedded_browser.session.click(str(args.get("selector") or args.get("sel") or ""))
        elif action_key in {"type", "fill"}:
            result = await embedded_browser.session.type(
                str(args.get("selector") or args.get("sel") or ""),
                str(args.get("text") or ""),
            )
        elif action_key in {"events", "console"}:
            result = {"events": embedded_browser.session.events()}
        else:
            return {
                "error": (
                    "browser: action must be one of navigate, getCurrentURL, getPageHTML, "
                    "getVisibleText, executeScript, screenshot, waitForSelector, click, type"
                ),
                "exit_code": 1,
            }
    except embedded_browser.BrowserSecurityError as exc:
        return {"error": f"browser: {exc}", "exit_code": 1}
    except embedded_browser.BrowserUnavailable as exc:
        return {"error": f"browser unavailable: {exc}", "exit_code": 1}
    except Exception as exc:
        return {"error": f"browser: {str(exc)[:240]}", "exit_code": 1}

    return {"response": "Browser action completed.", "browser": result, "exit_code": 0}



# Paths the generic `app_api` tool will refuse to call. Auth/token/user
# administration is too risky to route through an agent surface even
# when the agent is admin-context — accidental "delete account"
# style mistakes have permanent blast radius.
_APP_API_BLOCKLIST_PREFIXES = (
    "/api/auth/",          # login/logout/password
    "/api/users/",         # user CRUD
    "/api/tokens/",        # api token mgmt
    "/api/admin/",         # admin one-shots (wipe etc.)
    "/api/backup/restore", # destructive restore
)


# (method, prefix) pairs to refuse specifically. Used for endpoints
# where GET is fine but writes are destructive — saw the agent wipe
# cookbook_state.json (presets + tasks) by POSTing {"tasks": []} to
# /api/cookbook/state, which overwrote the whole file. Use the
# dedicated preset/task tools instead.
_APP_API_BLOCKLIST_METHOD_PATH = (
    ("GET",    "/api/email/accounts"),  # owner-filtered in tool context; use list_email_accounts MCP tool
    ("POST",   "/api/cookbook/state"),   # whole-file overwrite — agent must use serve_preset/serve_model instead
    ("DELETE", "/api/cookbook/state"),
    # Use the named tools (download_model / serve_model) — they handle
    # host-name resolution, per-host env_prefix, AND register the task
    # in cookbook state so it shows in the UI + list_downloads. Hitting
    # the raw endpoint via app_api skips all of that → orphan task.
    ("POST",   "/api/model/download"),
    ("POST",   "/api/model/serve"),
    # Use trigger_research — it returns a UI hint so the Deep Research
    # sidebar surfaces the session. Raw start works but the agent
    # fumbles the payload + the session doesn't reliably show up.
    ("POST",   "/api/research/start"),
    # Use the named tools — they handle owner attribution, natural-
    # language due_date parsing, timezone, dedup, and tag/category
    # normalization. Hitting the raw endpoint via app_api saves a
    # note/event with the wrong fields, no reminder, or the wrong tz.
    ("POST",   "/api/notes"),
    ("PUT",    "/api/notes"),
    ("DELETE", "/api/notes"),
    ("POST",   "/api/calendar/events"),
    ("PUT",    "/api/calendar/events"),
    ("DELETE", "/api/calendar/events"),
)



async def do_app_api(content: str, owner: Optional[str] = None) -> Dict:
    """Generic loopback to any internal Apollo API endpoint. Lets the
    agent reach the full UI-button surface (cookbook, email, notes,
    calendar, skills, sessions, gallery, research, etc.) without us
    landing a named tool wrapper for every one.

    Args (JSON):
      action: "call" (default) | "endpoints"
      path:   "/api/cookbook/gpus"     # required for call
      method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" (default GET)
      body:   <object>                 # JSON body for POST/PUT/PATCH
      query:  <object>                 # querystring params

    The `endpoints` action returns the OpenAPI surface (method + path +
    summary) so the agent can discover what's reachable. A blocklist
    refuses auth/user/admin paths to keep blast radius bounded.
    """
    import httpx
    try:
        args = _parse_tool_args(content) if content.strip() else {}
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = (args.get("action") or "call").lower()
    base = _COOKBOOK_BASE

    if action == "endpoints":
        # Fetch FastAPI's OpenAPI schema so the agent can discover any
        # endpoint without us pre-listing them. Filter by an optional
        # `filter` keyword (substring match on path or summary).
        kw = (args.get("filter") or "").lower()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{base}/openapi.json",
                                        headers=_internal_headers())
                data = resp.json()
        except Exception as e:
            return {"error": f"OpenAPI fetch failed: {e}", "exit_code": 1}
        rows: List[Dict[str, Any]] = []
        for path, methods in (data.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            if any(path.startswith(p) for p in _APP_API_BLOCKLIST_PREFIXES):
                continue
            for method, op in methods.items():
                if method.lower() not in ("get", "post", "put", "patch", "delete"):
                    continue
                if any(method.upper() == m and path.startswith(p) for m, p in _APP_API_BLOCKLIST_METHOD_PATH):
                    continue
                summary = (op or {}).get("summary") or (op or {}).get("description") or ""
                if isinstance(summary, str):
                    summary = summary.strip().split("\n")[0][:140]
                if kw and kw not in path.lower() and kw not in (summary or "").lower():
                    continue
                rows.append({"method": method.upper(), "path": path, "summary": summary})
        rows.sort(key=lambda r: (r["path"], r["method"]))
        if not rows:
            return {"output": f"No endpoints match filter {kw!r}." if kw else "No endpoints found.", "exit_code": 0}
        lines = [f"{len(rows)} endpoint(s)" + (f" matching {kw!r}" if kw else "") + ":"]
        for r in rows[:200]:
            line = f"  {r['method']:6s} {r['path']}"
            if r["summary"]:
                line += f"  — {r['summary']}"
            lines.append(line)
        if len(rows) > 200:
            lines.append(f"  ...({len(rows) - 200} more — filter to narrow)")
        return {"output": "\n".join(lines), "endpoints": rows, "exit_code": 0}

    # action == "call"
    path = args.get("path") or ""
    if not path:
        return {"error": "path is required (e.g. '/api/cookbook/gpus')", "exit_code": 1}
    if not path.startswith("/"):
        path = "/" + path
    if any(path.startswith(p) for p in _APP_API_BLOCKLIST_PREFIXES):
        return {"error": f"Path blocked for safety: {path}. Auth/user/admin endpoints are off-limits via app_api.", "exit_code": 1}

    method = (args.get("method") or "GET").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return {"error": f"Unsupported method: {method}", "exit_code": 1}
    if any(method == m and path.startswith(p) for m, p in _APP_API_BLOCKLIST_METHOD_PATH):
        if "/api/email/accounts" in path:
            return {"error": "Don't use /api/email/accounts via app_api — it is owner-filtered in tool context and may return empty. Use the `list_email_accounts` email tool, then pass `account` to list_emails/read_email.", "exit_code": 1}
        if "/api/model/download" in path:
            return {"error": "Don't POST /api/model/download directly — use the `download_model` tool (it resolves the server name, sets the venv env_prefix, and registers the task so it shows in the UI).", "exit_code": 1}
        if "/api/model/serve" in path:
            return {"error": "Don't POST /api/model/serve directly — use the `serve_model` or `serve_preset` tool (handles host resolution, env_prefix, and cookbook tracking).", "exit_code": 1}
        if "/api/research/start" in path:
            return {"error": "Don't POST /api/research/start directly — use the `trigger_research` tool (it surfaces the session in the Deep Research sidebar).", "exit_code": 1}
        if "/api/notes" in path:
            return {"error": "Don't hit /api/notes via app_api — use the `manage_notes` tool. It accepts natural-language due_date ('11pm today', 'tomorrow at 9am'), fires reminders from the due_date itself (no separate calendar event), and uses the caller's timezone. The raw endpoint requires ISO-UTC + a separate calendar event, both of which the agent tends to get wrong.", "exit_code": 1}
        if "/api/calendar/events" in path:
            return {"error": "Don't hit /api/calendar/events via app_api — use the `manage_calendar` tool. It handles tz-aware natural-language datetimes and reminder_minutes correctly. If the user wants a note + reminder, prefer `manage_notes` with due_date — it bundles both.", "exit_code": 1}
        return {"error": f"{method} {path} is blocked — it overwrites the whole cookbook state file. Use list_serve_presets / serve_preset / serve_model instead.", "exit_code": 1}

    body = args.get("body")
    query = args.get("query") or None
    # Pass owner so the backend impersonates the user — without this,
    # POSTs (notes, calendar, todos, ...) get owner="internal-tool"
    # and the user that asked for them can't see the result.
    headers = {**_internal_headers(owner=owner), "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method, f"{base}{path}",
                json=body if body is not None and method in ("POST", "PUT", "PATCH") else None,
                params=query,
                headers=headers,
            )
        # Try to parse JSON; fall back to raw text.
        try:
            payload = resp.json()
            preview = json.dumps(payload, indent=2, default=str)
            if len(preview) > 4000:
                preview = preview[:4000] + "\n... (truncated)"
        except Exception:
            payload = None
            preview = (resp.text or "")[:4000]
        if resp.status_code >= 400:
            return {
                "error": f"{method} {path} -> HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "body": preview,
                "exit_code": 1,
            }
        return {
            "output": f"{method} {path} -> {resp.status_code}\n{preview}",
            "status_code": resp.status_code,
            "json": payload,
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": f"{method} {path} failed: {e}", "exit_code": 1}

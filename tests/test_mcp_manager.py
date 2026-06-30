from src.mcp_manager import _format_mcp_connection_error, _stdio_env


def test_playwright_mcp_connection_error_includes_install_hint():
    msg = _format_mcp_connection_error(
        "Browser (Playwright)",
        "npx",
        ["-y", "@playwright/mcp@latest", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@latest --version" in msg
    assert "restart Apollo" in msg


def test_generic_mcp_connection_error_preserves_original_error():
    msg = _format_mcp_connection_error(
        "Custom MCP",
        "python",
        ["server.py"],
        RuntimeError("boom"),
    )

    assert msg == "boom"


def test_stdio_env_quiets_npx_without_overriding_user_values(monkeypatch):
    monkeypatch.setenv("EXISTING", "1")

    env = _stdio_env("npx", {"NPM_CONFIG_LOGLEVEL": "warn", "CUSTOM": "yes"})

    # Arbitrary host env is NO LONGER inherited (secret-leak fix, P1 #2).
    assert "EXISTING" not in env
    # Explicitly-configured server env IS preserved.
    assert env["CUSTOM"] == "yes"
    # npx quieting is applied without overriding caller-provided values.
    assert env["NPM_CONFIG_LOGLEVEL"] == "warn"
    assert env["NPM_CONFIG_FUND"] == "false"
    assert env["NPM_CONFIG_AUDIT"] == "false"
    assert env["NO_UPDATE_NOTIFIER"] == "1"


def test_stdio_env_keeps_non_npm_commands_simple(monkeypatch):
    monkeypatch.setenv("EXISTING", "1")

    env = _stdio_env("uvx", {})

    # Arbitrary host env is NOT inherited (secret-leak fix); allowlisted vars
    # like PATH still flow so the command can actually run.
    assert "EXISTING" not in env
    assert env.get("PATH")
    assert "NPM_CONFIG_LOGLEVEL" not in env

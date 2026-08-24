"""Lean config security scanner — audit configured MCP servers and skills.

Inspired by ECC's AgentShield, scaled down to fit what Apollo already has:
no new rule engine, no red-team pipeline. It re-reads the same MCP-server
and skill data the app already exposes and flags a small set of concrete,
explainable risk patterns. Never re-displays secret VALUES — only whether a
secret-shaped env var name is present, matching the audit's own rule that
a scanner should not become a new way to leak credentials.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)$", re.IGNORECASE)
_SHELL_PIPE_RE = re.compile(r"\bcurl\b.{0,40}\|\s*(sh|bash|zsh)\b", re.IGNORECASE)
_EVAL_RE = re.compile(r"\b(eval|exec)\s*\(", re.IGNORECASE)
_ENCODED_EXEC_RE = re.compile(r"base64\s+-d.{0,20}\|\s*(sh|bash)", re.IGNORECASE)
_SUSPICIOUS_RE = re.compile(r"rm\s+-rf\s+/|:(){ ?:|:&};:", re.IGNORECASE)


def _finding(severity: str, category: str, target: str, message: str) -> Dict[str, Any]:
    return {"severity": severity, "category": category, "target": target, "message": message}


def scan_mcp_servers(servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`servers`: rows shaped like GET /api/mcp/servers already returns —
    id/name/transport/command/args/env (env as a dict of name->value)."""
    findings = []
    for srv in servers:
        name = srv.get("name") or srv.get("id") or "unknown"
        args = srv.get("args") or []
        args_text = " ".join(str(a) for a in args)
        command = str(srv.get("command") or "")
        combined = f"{command} {args_text}"

        if _SHELL_PIPE_RE.search(combined):
            findings.append(_finding("high", "mcp", name,
                "Command pipes a remote download straight into a shell (curl … | sh) — "
                "review before enabling; this pattern can run arbitrary code from a URL you don't control."))
        if _SUSPICIOUS_RE.search(combined):
            findings.append(_finding("high", "mcp", name,
                "Command contains a destructive/fork-bomb-shaped pattern."))
        if _ENCODED_EXEC_RE.search(combined):
            findings.append(_finding("medium", "mcp", name,
                "Command decodes base64 and pipes it to a shell — obscured execution, hard to audit."))

        env = srv.get("env") or {}
        if isinstance(env, str):
            try:
                env = json.loads(env)
            except (ValueError, TypeError):
                env = {}
        secret_names = [k for k in env if _SECRET_NAME_RE.search(str(k))]
        if secret_names:
            findings.append(_finding("info", "mcp", name,
                f"Stores {len(secret_names)} secret-shaped env var(s) ({', '.join(sorted(secret_names))}) "
                "— values are never shown here; make sure only trusted admins can reach Settings → MCP."))
        if srv.get("transport") == "sse" and str(srv.get("url") or "").startswith("http://"):
            findings.append(_finding("medium", "mcp", name,
                "SSE server URL is plain http:// — traffic (including any bearer token) is unencrypted."))
    return findings


def scan_skills(skills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`skills`: rows shaped like SkillsManager.load_all() output."""
    findings = []
    for sk in skills:
        name = sk.get("name") or sk.get("id") or "unknown"
        status = sk.get("status") or "draft"
        source = sk.get("source") or ""
        body = " ".join(sk.get("procedure") or []) + " " + " ".join(sk.get("pitfalls") or [])

        if status == "draft" and source in ("learned", "imported"):
            findings.append(_finding("info", "skill", name,
                "Draft skill, not yet reviewed — the agent will not use it until published."))
        if _SHELL_PIPE_RE.search(body):
            findings.append(_finding("high", "skill", name,
                "Procedure text includes a curl-pipe-to-shell pattern — review before publishing."))
        if _EVAL_RE.search(body):
            findings.append(_finding("medium", "skill", name,
                "Procedure text references eval()/exec() — review what it's evaluating."))
    return findings


def summarize(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"high": 0, "medium": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "info")
        out[sev] = out.get(sev, 0) + 1
    return out

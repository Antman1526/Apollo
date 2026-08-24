#!/usr/bin/env python3
"""Persistent Python worker for the agent's `python_session` tool.

Protocol: one JSON object per line on stdin ({"code": "..."}), one JSON
object per line back on stdout ({"stdout", "stderr", "error"}). Code runs in
a single persistent `globals()` dict for the worker's lifetime, so variables,
imports, and loaded objects survive across calls — the gap the one-shot
`python` tool can't close (inspired by prime-agent's persistent IPython
approach, without pulling in ipykernel/jupyter_client as new dependencies).

Not meant to be run by a human — spawned by services/python_kernel.py with a
minimal env (src.subproc_env.build_agent_env), one process per chat session.
"""
import contextlib
import io
import json
import sys
import traceback

_NAMESPACE = {"__name__": "__apollo_session__"}


def _run_one(code: str) -> dict:
    out, err = io.StringIO(), io.StringIO()
    error = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # exec, not eval: sessions run statements (imports, assignments,
            # loops), not just expressions. compile() first so a syntax
            # error reports cleanly instead of surfacing as exec()'s own.
            compiled = compile(code, "<session>", "exec")
            exec(compiled, _NAMESPACE)
    except BaseException:
        # BaseException (not Exception): a stray SystemExit/KeyboardInterrupt
        # from user code must not kill the worker — it's caught and reported
        # like any other error, and the kernel keeps running.
        error = traceback.format_exc()
    return {"stdout": out.getvalue(), "stderr": err.getvalue(), "error": error}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"stdout": "", "stderr": "", "error": "malformed request"}) + "\n")
            sys.stdout.flush()
            continue
        if req.get("cmd") == "shutdown":
            return
        result = _run_one(req.get("code", ""))
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

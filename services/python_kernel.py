"""Manages one persistent Python worker subprocess per chat session.

Backs the `python_session` agent tool: unlike the one-shot `python` tool
(a fresh `python -c` subprocess every call), state — variables, imports,
loaded dataframes/models — survives across calls within a session. Inspired
by prime-agent's persistent-IPython-REPL approach, implemented as a small
JSON-line worker (scripts/apollo_kernel_worker.py) instead of pulling in
ipykernel/jupyter_client as new dependencies.

Every failure mode (spawn error, protocol desync, timeout, crash) returns a
clean error dict — mirrors the "tools never raise" contract the audit
hardened at the agent-loop level, so a wedged kernel degrades to "start a
fresh one" rather than breaking the tool call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from src.subproc_env import build_agent_env

logger = logging.getLogger(__name__)

_WORKER_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "apollo_kernel_worker.py",
)
MAX_KERNELS = 8            # LRU-evict beyond this many concurrent sessions
DEFAULT_IDLE_TIMEOUT_S = 1800.0  # 30 min — matches typical session activity gaps
DEFAULT_EXEC_TIMEOUT_S = 60.0
MAX_OUTPUT_CHARS = 20_000


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… [truncated at {MAX_OUTPUT_CHARS} chars]"


@dataclass
class _Kernel:
    proc: asyncio.subprocess.Process
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)


class PythonSessionManager:
    def __init__(self):
        self._kernels: Dict[str, _Kernel] = {}
        self._map_lock = asyncio.Lock()

    async def _spawn(self) -> asyncio.subprocess.Process:
        env = build_agent_env()
        return await asyncio.create_subprocess_exec(
            sys.executable or "python3", "-I", _WORKER_SCRIPT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )

    async def _evict_lru_if_full(self) -> None:
        if len(self._kernels) < MAX_KERNELS:
            return
        oldest_id = min(self._kernels, key=lambda k: self._kernels[k].last_used)
        await self._kill(oldest_id)

    async def _kill(self, session_id: str) -> None:
        kernel = self._kernels.pop(session_id, None)
        if not kernel:
            return
        try:
            kernel.proc.kill()
            await asyncio.wait_for(kernel.proc.wait(), timeout=5)
        except Exception:
            pass  # best-effort — process may already be gone

    async def run(
        self, session_id: str, code: str, timeout: float = DEFAULT_EXEC_TIMEOUT_S
    ) -> Dict[str, object]:
        """Execute `code` in the session's persistent kernel. Never raises."""
        try:
            async with self._map_lock:
                kernel = self._kernels.get(session_id)
                if kernel is None or kernel.proc.returncode is not None:
                    await self._evict_lru_if_full()
                    proc = await self._spawn()
                    kernel = _Kernel(proc=proc)
                    self._kernels[session_id] = kernel

            async with kernel.lock:
                kernel.last_used = time.monotonic()
                try:
                    kernel.proc.stdin.write(
                        (json.dumps({"code": code}) + "\n").encode("utf-8")
                    )
                    await kernel.proc.stdin.drain()
                    line = await asyncio.wait_for(
                        kernel.proc.stdout.readline(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    await self._kill(session_id)
                    return {
                        "error": f"python_session: timed out after {timeout}s — "
                                 "session restarted (previous variables are lost)",
                        "exit_code": 124,
                    }
                except (BrokenPipeError, ConnectionResetError):
                    await self._kill(session_id)
                    return {"error": "python_session: worker died — session restarted", "exit_code": 1}

                if not line:
                    await self._kill(session_id)
                    return {"error": "python_session: worker exited — session restarted", "exit_code": 1}

                try:
                    result = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    await self._kill(session_id)
                    return {"error": "python_session: protocol error — session restarted", "exit_code": 1}

            out = _truncate(result.get("stdout") or "")
            err = _truncate(result.get("stderr") or "")
            py_error = result.get("error")
            if py_error:
                combined = (out + "\n" if out else "") + _truncate(py_error)
                return {"output": combined.strip() or "(no output)", "exit_code": 1}
            combined = out + (f"\nSTDERR: {err}" if err else "")
            return {"output": combined.strip() or "(no output)", "exit_code": 0}
        except Exception as e:
            logger.exception("python_session: unexpected manager error")
            return {"error": f"python_session: internal error: {e}", "exit_code": 1}

    async def stop_session(self, session_id: str) -> bool:
        existed = session_id in self._kernels
        await self._kill(session_id)
        return existed

    async def reap_idle(self, idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S) -> int:
        now = time.monotonic()
        stale = [sid for sid, k in self._kernels.items() if now - k.last_used > idle_timeout]
        for sid in stale:
            await self._kill(sid)
        return len(stale)

    async def stop_all(self) -> None:
        for sid in list(self._kernels):
            await self._kill(sid)

    def active_count(self) -> int:
        return len(self._kernels)


_manager: Optional[PythonSessionManager] = None


def get_manager() -> PythonSessionManager:
    global _manager
    if _manager is None:
        _manager = PythonSessionManager()
    return _manager

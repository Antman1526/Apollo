"""Private credential sidecars used by long-running Cookbook runners."""

from __future__ import annotations

import shlex
from pathlib import Path

from src.secure_temp import ensure_private_dir, write_private_text


def write_hf_token_sidecar(directory: Path, session_id: str, token: str, *, shell: str) -> Path:
    """Write an owner-only HF token sidecar for one runner session."""
    directory = ensure_private_dir(Path(directory))
    path = directory / f".{session_id}.hf-token"
    if shell == "bash":
        content = f"export HF_TOKEN='{token}'\n"
    elif shell == "powershell":
        content = f"HF_TOKEN={token}\n"
    else:
        raise ValueError(f"unsupported runner shell: {shell}")
    return write_private_text(path, content)


def bash_secret_loader(path: Path) -> list[str]:
    """Return bash lines that source and immediately remove a token sidecar."""
    quoted = shlex.quote(str(path))
    return [
        f"if [ -f {quoted} ]; then",
        f"  . {quoted}",
        f"  rm -f {quoted}",
        "fi",
    ]


def powershell_secret_loader(path: Path) -> list[str]:
    """Return PowerShell lines that load and immediately remove a sidecar."""
    quoted = str(path).replace("'", "''")
    return [
        f"$apolloTokenFile = '{quoted}'",
        "if (Test-Path -LiteralPath $apolloTokenFile) {",
        "  $apolloTokenLine = Get-Content -LiteralPath $apolloTokenFile -Raw",
        "  Remove-Item -LiteralPath $apolloTokenFile -Force -ErrorAction SilentlyContinue",
        "  if ($apolloTokenLine -match '^HF_TOKEN=(.+)\\s*$') { $env:HF_TOKEN = $matches[1] }",
        "}",
    ]

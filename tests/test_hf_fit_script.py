"""scripts/hf-fit.ps1: GGUF models on Hugging Face that fit this machine.

Runs under PowerShell when one is available (Windows CI; pwsh elsewhere)
and skips otherwise. The network call is real, so an unreachable Hugging
Face skips rather than fails.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hf-fit.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def test_script_documents_its_parameters():
    src = SCRIPT.read_text(encoding="utf-8")
    for p in ("-Search", "-Limit", "-ContextK", "-Json", "-Download", "-Dest"):
        assert p.lstrip("-") in src
    assert "nvidia-smi" in src and "Win32_ComputerSystem" in src
    assert "huggingface.co/api/models" in src


@pytest.mark.skipif(PWSH is None, reason="no PowerShell on this machine")
def test_exact_repo_lists_gguf_files_with_fit_verdicts():
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", str(SCRIPT), "-Search", "unsloth/Qwen3.5-9B-GGUF", "-Json"],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0 and ("huggingface.co" in r.stderr or "remote name" in r.stderr.lower()):
        pytest.skip(f"Hugging Face unreachable: {r.stderr[-200:]}")
    assert r.returncode == 0, r.stderr[-800:]
    rows = json.loads(r.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    assert rows, "no GGUF files listed"
    names = {row["File"] for row in rows}
    assert any(n.endswith(".gguf") for n in names)
    assert not any("mmproj" in n.lower() for n in names)
    for row in rows:
        assert row["Fits"] in ("GPU", "RAM (partial GPU offload)", "RAM (CPU)", "no")
        assert row["NeedsGB"] >= row["SizeGB"] > 0

import os
from pathlib import Path

from routes.cookbook_runner_files import (
    bash_secret_loader,
    powershell_secret_loader,
    write_hf_token_sidecar,
)


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_bash_runner_loads_and_removes_private_token_sidecar(tmp_path):
    token = "hf_not_in_the_runner"
    sidecar = write_hf_token_sidecar(tmp_path, "cookbook-abc", token, shell="bash")
    loader = "\n".join(bash_secret_loader(sidecar))

    assert sidecar.read_text(encoding="utf-8") == f"export HF_TOKEN='{token}'\n"
    assert token not in loader
    assert str(sidecar) in loader
    assert "rm -f" in loader
    if os.name != "nt":
        assert _mode(sidecar) == 0o600


def test_powershell_runner_loads_and_removes_private_token_sidecar(tmp_path):
    token = "hf_not_in_the_runner"
    sidecar = write_hf_token_sidecar(tmp_path, "serve-abc", token, shell="powershell")
    loader = "\n".join(powershell_secret_loader(sidecar))

    assert sidecar.read_text(encoding="utf-8") == f"HF_TOKEN={token}\n"
    assert token not in loader
    assert str(sidecar) in loader
    assert "Remove-Item" in loader


def test_cookbook_routes_do_not_interpolate_hf_tokens_into_runners():
    source = (Path(__file__).resolve().parents[1] / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")

    assert "export HF_TOKEN='{_bash_squote(req.hf_token)}'" not in source
    assert "$env:HF_TOKEN = '{_ps_squote(req.hf_token)}'" not in source

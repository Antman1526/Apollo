import subprocess
import sys
from pathlib import Path

import pytest


def _run_guard(root: Path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_runtime_paths.py"
    return subprocess.run([sys.executable, str(script), "--root", str(root)], capture_output=True, text=True)


def test_guard_rejects_relative_data_paths(tmp_path):
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "bad.py").write_text(
        'from pathlib import Path\nvalue = Path("data/reports")\n', encoding="utf-8"
    )

    result = _run_guard(tmp_path)

    assert result.returncode == 1
    assert "routes/bad.py:2" in result.stdout


def test_guard_allows_runtime_path_implementation(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "runtime_paths.py").write_text(
        'from pathlib import Path\nvalue = Path("data")\n', encoding="utf-8"
    )

    result = _run_guard(tmp_path)

    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("artifact_dir", ["dist", "build"])
def test_guard_ignores_generated_package_artifacts(tmp_path, artifact_dir):
    (tmp_path / artifact_dir).mkdir()
    (tmp_path / artifact_dir / "bundled.py").write_text(
        'from pathlib import Path\nvalue = Path("data/cache")\n', encoding="utf-8"
    )

    result = _run_guard(tmp_path)

    assert result.returncode == 0, result.stdout

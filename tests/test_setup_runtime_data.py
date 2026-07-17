import os
import subprocess
import sys


def test_setup_uses_explicit_runtime_data_directory(tmp_path):
    root = tmp_path / "state"
    env = {**os.environ, "APOLLO_DATA_DIR": str(root)}
    code = "import setup; assert setup.DATA_DIR == r'" + str(root) + "'"

    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr

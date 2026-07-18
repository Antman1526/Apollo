"""Modules that persist user state must honor APOLLO_DATA_DIR at import time."""

from __future__ import annotations

import os
import subprocess
import sys


def test_data_owning_modules_follow_configured_root(tmp_path):
    root = tmp_path / "application-state"
    env = {**os.environ, "APOLLO_DATA_DIR": str(root)}
    code = (
        "from pathlib import Path; "
        "import routes.contacts_routes as contacts; "
        "import routes.emoji_routes as emoji; "
        "import routes.email_helpers as email; "
        "import src.integrations as integrations; "
        "root = Path(r'" + str(root) + "'); "
        "assert contacts.DATA_DIR == root; "
        "assert emoji._CACHE_DIR == root / 'emoji_cache'; "
        "assert email.DATA_DIR == root; "
        "assert Path(integrations.DATA_FILE) == root / 'integrations.json'"
    )

    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr

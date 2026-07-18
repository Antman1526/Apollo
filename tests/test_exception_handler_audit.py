import subprocess
import sys
from pathlib import Path

from scripts.audit_exception_handlers import audit


def test_audit_reports_silent_broad_handler(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("try:\n    raise RuntimeError()\nexcept Exception:\n    pass\n", encoding="utf-8")

    findings = audit(tmp_path, ["sample.py"])

    assert len(findings) == 1
    assert findings[0].line == 3
    assert findings[0].handler_type == "Exception"


def test_audit_accepts_logged_and_reraised_handlers(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text(
        "try:\n    raise RuntimeError()\nexcept Exception:\n    logger.warning('failed')\n"
        "try:\n    raise RuntimeError()\nexcept Exception:\n    raise\n",
        encoding="utf-8",
    )

    assert audit(tmp_path, ["sample.py"]) == []


def test_audit_script_returns_nonzero_for_unclassified_handler(tmp_path: Path):
    (tmp_path / "app.py").write_text("try:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_exception_handlers.py"

    result = subprocess.run([sys.executable, str(script), "--root", str(tmp_path), "app.py"], capture_output=True, text=True)

    assert result.returncode == 1
    assert "app.py:3: unclassified broad bare handler" in result.stdout

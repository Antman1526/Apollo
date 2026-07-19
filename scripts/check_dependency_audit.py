#!/usr/bin/env python3
"""Run pip-audit while enforcing narrow, expiring documented exceptions.

An audit exception is not an ignored finding: the underlying vulnerability must
still be emitted by pip-audit, match an exact package/version record, and stay
inside its expiry. Any new, changed, or stale finding fails the command.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


def _load_exceptions(path: Path) -> dict[tuple[str, str, str], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    exceptions = payload.get("exceptions")
    if not isinstance(exceptions, list):
        raise ValueError("exception file must contain an exceptions list")

    result: dict[tuple[str, str, str], dict] = {}
    for entry in exceptions:
        if not isinstance(entry, dict):
            raise ValueError("every audit exception must be an object")
        key = (str(entry.get("id", "")), str(entry.get("package", "")).lower(), str(entry.get("version", "")))
        if not all(key) or not entry.get("reason") or not entry.get("tracking"):
            raise ValueError(f"invalid audit exception: {entry!r}")
        try:
            expires = date.fromisoformat(str(entry.get("expires", "")))
        except ValueError as error:
            raise ValueError(f"audit exception {key[0]} has an invalid expiry") from error
        if expires < date.today():
            raise ValueError(f"audit exception {key[0]} expired on {expires.isoformat()}")
        if key in result:
            raise ValueError(f"duplicate audit exception for {key}")
        result[key] = entry
    return result


def _find_vulnerabilities(payload: dict) -> set[tuple[str, str, str]]:
    findings: set[tuple[str, str, str]] = set()
    for dependency in payload.get("dependencies", []):
        package = str(dependency.get("name", "")).lower()
        version = str(dependency.get("version", ""))
        for vulnerability in dependency.get("vulns", []):
            finding = (str(vulnerability.get("id", "")), package, version)
            if not all(finding):
                raise ValueError(f"pip-audit returned malformed finding: {vulnerability!r}")
            findings.add(finding)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default="requirements.txt")
    parser.add_argument("--exceptions", default="security/dependency-audit-exceptions.json")
    args = parser.parse_args()

    exceptions = _load_exceptions(Path(args.exceptions))
    command = [sys.executable, "-m", "pip_audit", "-r", args.requirements, "--format", "json"]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode not in (0, 1):
        sys.stderr.write(completed.stderr)
        print("dependency audit could not run", file=sys.stderr)
        return completed.returncode

    try:
        findings = _find_vulnerabilities(json.loads(completed.stdout))
    except (json.JSONDecodeError, ValueError) as error:
        print(f"could not parse dependency audit output: {error}", file=sys.stderr)
        return 2

    unexpected = findings - set(exceptions)
    stale = set(exceptions) - findings
    if unexpected or stale:
        if unexpected:
            print("unapproved dependency findings:", file=sys.stderr)
            for finding in sorted(unexpected):
                print(f"  {finding[0]}: {finding[1]}=={finding[2]}", file=sys.stderr)
        if stale:
            print("stale dependency audit exceptions (remove or update them):", file=sys.stderr)
            for finding in sorted(stale):
                print(f"  {finding[0]}: {finding[1]}=={finding[2]}", file=sys.stderr)
        return 1

    if findings:
        for finding in sorted(findings):
            exception = exceptions[finding]
            print(
                "mitigated dependency finding: "
                f"{finding[0]} {finding[1]}=={finding[2]} "
                f"(expires {exception['expires']}; {exception['tracking']})"
            )
    else:
        print("dependency-audit-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

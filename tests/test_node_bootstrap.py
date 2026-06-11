import os

from services.paperclip import node_bootstrap as nb


def test_dist_filename_mac_arm():
    assert nb.dist_filename("22.13.0", "darwin", "arm64") == "node-v22.13.0-darwin-arm64.tar.gz"


def test_dist_filename_mac_intel():
    assert nb.dist_filename("22.13.0", "darwin", "x86_64") == "node-v22.13.0-darwin-x64.tar.gz"


def test_dist_filename_linux():
    assert nb.dist_filename("22.13.0", "linux", "aarch64") == "node-v22.13.0-linux-arm64.tar.gz"


def test_dist_filename_windows():
    assert nb.dist_filename("22.13.0", "windows", "amd64") == "node-v22.13.0-win-x64.zip"


def test_dist_url():
    assert nb.dist_url("22.13.0", "darwin", "arm64") == (
        "https://nodejs.org/dist/v22.13.0/node-v22.13.0-darwin-arm64.tar.gz")


def test_bin_paths_unix():
    node, npx = nb.bin_paths("/x/node-v22.13.0-darwin-arm64", "darwin")
    assert node == "/x/node-v22.13.0-darwin-arm64/bin/node"
    assert npx == "/x/node-v22.13.0-darwin-arm64/bin/npx"


def test_bin_paths_windows():
    node, npx = nb.bin_paths("/x/node-v22.13.0-win-x64", "windows")
    assert node.endswith("node-v22.13.0-win-x64/node.exe")
    assert npx.endswith("node-v22.13.0-win-x64/npx.cmd")


def test_pick_lts_picks_highest_lts():
    index = [
        {"version": "v23.5.0", "lts": False},
        {"version": "v22.13.0", "lts": "Jod"},
        {"version": "v22.12.0", "lts": "Jod"},
        {"version": "v20.18.0", "lts": "Iron"},
    ]
    assert nb.pick_lts(index) == "22.13.0"


def test_ensure_node_returns_existing_without_download(tmp_path, monkeypatch):
    # Pre-create the expected extracted layout so ensure_node short-circuits.
    home = tmp_path / ".node" / "node-v22.13.0-darwin-arm64"
    (home / "bin").mkdir(parents=True)
    node = home / "bin" / "node"
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    (home / "bin" / "npx").write_text("#!/bin/sh\n")

    called = {"downloaded": False}

    def _no_download(url, dest):
        called["downloaded"] = True
        raise AssertionError("should not download when node already present")

    got_node, got_npx = nb.ensure_node(
        str(tmp_path), version="22.13.0", system="darwin", machine="arm64",
        download_extract=_no_download)
    assert got_node == str(node)
    assert called["downloaded"] is False


def test_ensure_node_reuses_installed_without_network(tmp_path):
    # Unpinned (version=None): must reuse the installed dir and never call fetch_index.
    home = tmp_path / ".node" / "node-v22.13.0-darwin-arm64"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "node").write_text("#!/bin/sh\n")
    (home / "bin" / "npx").write_text("#!/bin/sh\n")

    def _boom():
        raise AssertionError("must not hit the network when Node is installed")

    got = nb.ensure_node(str(tmp_path), system="darwin", machine="arm64",
                         fetch_index=_boom,
                         download_extract=lambda u, d: (_ for _ in ()).throw(AssertionError("no dl")))
    assert got == (str(home / "bin" / "node"), str(home / "bin" / "npx"))


def test_expected_sha256_parses_shasums_lines():
    text = (
        "abc123  node-v22.13.0-darwin-arm64.tar.gz\n"
        "def456 *node-v22.13.0-win-x64.zip\n"
        "ignored line\n"
    )
    assert nb._expected_sha256(text, "node-v22.13.0-darwin-arm64.tar.gz") == "abc123"
    assert nb._expected_sha256(text, "node-v22.13.0-win-x64.zip") == "def456"
    assert nb._expected_sha256(text, "node-v0.0.0-none.tar.gz") is None


def test_verify_download_accepts_matching_checksum(tmp_path, monkeypatch):
    artifact = tmp_path / "node-v22.13.0-darwin-arm64.tar.gz"
    artifact.write_bytes(b"node bits")
    good = nb._sha256_of(str(artifact))

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout=None):
        assert url.endswith("/SHASUMS256.txt")
        return _Resp(f"{good}  node-v22.13.0-darwin-arm64.tar.gz\n".encode())

    monkeypatch.setattr(nb.urllib.request, "urlopen", fake_urlopen)
    nb._verify_download("https://nodejs.org/dist/v22.13.0/node-v22.13.0-darwin-arm64.tar.gz",
                        str(artifact))


def test_verify_download_rejects_mismatch_and_missing_entry(tmp_path, monkeypatch):
    import pytest

    artifact = tmp_path / "node-v22.13.0-darwin-arm64.tar.gz"
    artifact.write_bytes(b"tampered bits")

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        nb.urllib.request, "urlopen",
        lambda url, timeout=None: _Resp(b"0" * 64 + b"  node-v22.13.0-darwin-arm64.tar.gz\n"))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        nb._verify_download("https://nodejs.org/dist/v22.13.0/node-v22.13.0-darwin-arm64.tar.gz",
                            str(artifact))

    monkeypatch.setattr(
        nb.urllib.request, "urlopen",
        lambda url, timeout=None: _Resp(b"unrelated  other-file.tar.gz\n"))
    with pytest.raises(RuntimeError, match="no SHASUMS256 entry"):
        nb._verify_download("https://nodejs.org/dist/v22.13.0/node-v22.13.0-darwin-arm64.tar.gz",
                            str(artifact))

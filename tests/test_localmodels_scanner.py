import os


def _touch(path, size=16):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


def test_scan_classifies_chat_and_embedding(tmp_path):
    from services.localmodels.scanner import scan_dirs
    d = tmp_path / "GGUF"
    _touch(str(d / "Qwen3.5-9B-Q4_K_M.gguf"))
    _touch(str(d / "nomic-embed-text-v1.5.f16.gguf"))
    models = scan_dirs([str(tmp_path)])
    by_name = {m.name: m for m in models}
    assert by_name["Qwen3.5-9B-Q4_K_M"].kind == "chat"
    assert by_name["Qwen3.5-9B-Q4_K_M"].quant == "Q4_K_M"
    assert by_name["nomic-embed-text-v1.5.f16"].kind == "embedding"


def test_scan_skips_sidecars_projectors_and_extra_split_parts(tmp_path):
    from services.localmodels.scanner import scan_dirs
    d = tmp_path / "GGUF"
    _touch(str(d / "._Qwen3.5-9B-Q4_K_M.gguf"))      # AppleDouble sidecar
    _touch(str(d / "mmproj-model-f16.gguf"))          # multimodal projector
    _touch(str(d / "Big-Model-2-of-3.gguf"))          # non-first split part
    _touch(str(d / "Big-Model-1-of-3.gguf"))          # first split part (kept)
    names = {m.name for m in scan_dirs([str(tmp_path)])}
    assert names == {"Big-Model-1-of-3"}


def test_scan_tolerates_missing_dir():
    from services.localmodels.scanner import scan_dirs
    assert scan_dirs(["/nonexistent/path/xyz"]) == []

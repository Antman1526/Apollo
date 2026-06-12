import os
import struct


def _touch(path, size=16):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


def _write_gguf(path: str, arch: str) -> None:
    """Write a minimal syntactically-valid GGUF file with general.architecture set."""
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def encode_str(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    arch_kv = (
        encode_str("general.architecture") +
        struct.pack("<I", 8) +   # STRING type
        encode_str(arch)
    )
    n_kv = struct.pack("<Q", 1)
    data = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + n_kv + arch_kv
    with open(path, "wb") as f:
        f.write(data)


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


def test_scan_skips_cache_and_blob_dirs(tmp_path):
    from services.localmodels.scanner import scan_dirs
    # real model in a normal subdir is kept...
    _touch(str(tmp_path / "GGUF" / "Real-Model-Q4_K_M.gguf"))
    # ...but GGUFs inside cache/blob/ollama dirs are pruned (avoids HF cache
    # blobs, ollama stores, and duplicate copies surfacing as models).
    _touch(str(tmp_path / "cache" / "Junk-Q4_K_M.gguf"))
    _touch(str(tmp_path / "llama-cache" / "Junk2-Q4_K_M.gguf"))
    _touch(str(tmp_path / "ollama" / "blobs" / "Junk3-Q4_K_M.gguf"))
    _touch(str(tmp_path / ".cache" / "Junk4-Q4_K_M.gguf"))
    names = {m.name for m in scan_dirs([str(tmp_path)])}
    assert names == {"Real-Model-Q4_K_M"}


def test_scan_diffusion_gguf_header_gives_unsupported(tmp_path):
    """A GGUF with diffusion-gemma architecture must be classified as unsupported."""
    from services.localmodels.scanner import scan_dirs
    d = tmp_path / "GGUF"
    _write_gguf(str(d / "google.diffusiongemma-2b-Q4_K_M.gguf"), "diffusion-gemma")
    models = scan_dirs([str(tmp_path)])
    assert len(models) == 1
    m = models[0]
    assert m.kind == "unsupported"
    assert m.arch == "diffusion-gemma"


def test_scan_header_beats_chat_looking_filename(tmp_path):
    """A file named like a chat model but with nomic-bert header → embedding."""
    from services.localmodels.scanner import scan_dirs
    d = tmp_path / "GGUF"
    # Filename has no embed hint → would be "chat" by filename alone
    _write_gguf(str(d / "mymodel-Q4_K_M.gguf"), "nomic-bert")
    models = scan_dirs([str(tmp_path)])
    assert len(models) == 1
    m = models[0]
    assert m.kind == "embedding"
    assert m.arch == "nomic-bert"


def test_scan_filename_fallback_when_header_unreadable(tmp_path):
    """When the GGUF header can't be read (zero-byte file), fall back to filename."""
    from services.localmodels.scanner import scan_dirs
    d = tmp_path / "GGUF"
    # A zero/stub file — _touch writes null bytes which are not a valid GGUF
    _touch(str(d / "nomic-embed-text-v1.5.f16.gguf"))
    models = scan_dirs([str(tmp_path)])
    assert len(models) == 1
    assert models[0].kind == "embedding"
    assert models[0].arch == ""


def test_scan_arch_field_populated(tmp_path):
    """The arch field must be set to the GGUF general.architecture value."""
    from services.localmodels.scanner import scan_dirs
    d = tmp_path / "GGUF"
    _write_gguf(str(d / "Qwen3.5-9B-Q4_K_M.gguf"), "qwen2")
    models = scan_dirs([str(tmp_path)])
    assert models[0].arch == "qwen2"
    assert models[0].kind == "chat"


def test_discover_piper_voices(tmp_path):
    from services.localmodels.scanner import discover_piper_voices
    d = tmp_path / "TTS"
    _touch(str(d / "en_US-amy-medium.onnx"))
    (d / "en_US-amy-medium.onnx.json").write_text("{}")
    _touch(str(d / "no-sidecar.onnx"))            # skipped: no .json sidecar
    _touch(str(tmp_path / "cache" / "junk.onnx"))
    (tmp_path / "cache" / "junk.onnx.json").write_text("{}")  # pruned (cache dir)
    voices = discover_piper_voices([str(tmp_path)])
    assert [v["name"] for v in voices] == ["en_US-amy-medium"]
    assert voices[0]["path"].endswith("/TTS/en_US-amy-medium.onnx")

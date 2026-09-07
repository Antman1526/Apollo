"""Two discovery defects found by serving a real model library.

1. A vision model's projector (``mmproj-*.gguf``) was discovered only to be
   skipped, and never handed to llama-server, so every vision model answered
   an image with HTTP 500 "image input is not supported".
2. Model libraries are full of symlinks — LM Studio keeps a tree of them, and
   a user may alias their models directory. Keying discovery on the walked
   path rather than the real one listed the same model two or three times in
   the picker.
"""
import os
import struct

from services.localmodels.scanner import scan_dirs


def _write_gguf(path: str, arch: str) -> None:
    """Minimal syntactically-valid GGUF carrying general.architecture."""
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def encode_str(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    arch_kv = (
        encode_str("general.architecture")
        + struct.pack("<I", 8)  # STRING
        + encode_str(arch)
    )
    data = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 1) + arch_kv
    with open(path, "wb") as f:
        f.write(data)


# ── 1. Projector discovery ──────────────────────────────────────────────

def test_sibling_projector_is_attached_to_the_model(tmp_path):
    root = tmp_path / "models"
    _write_gguf(str(root / "Qwen3VL-8B" / "Qwen3VL-8B-Q4_K_M.gguf"), "qwen3vl")
    _write_gguf(str(root / "Qwen3VL-8B" / "mmproj-Qwen3VL-8B-Q8_0.gguf"), "clip")

    models = scan_dirs([str(root)])

    assert len(models) == 1, "the projector must not be listed as its own model"
    assert models[0].mmproj is not None
    assert os.path.basename(models[0].mmproj) == "mmproj-Qwen3VL-8B-Q8_0.gguf"


def test_model_without_a_projector_has_none(tmp_path):
    root = tmp_path / "models"
    _write_gguf(str(root / "Mistral-7B-Q4_K_M.gguf"), "llama")

    models = scan_dirs([str(root)])

    assert len(models) == 1
    assert models[0].mmproj is None


def test_projector_in_another_directory_is_not_borrowed(tmp_path):
    """A projector belongs to the model beside it, not to one a folder away."""
    root = tmp_path / "models"
    _write_gguf(str(root / "vision" / "VL-Q4.gguf"), "qwen3vl")
    _write_gguf(str(root / "vision" / "mmproj-F16.gguf"), "clip")
    _write_gguf(str(root / "text" / "Text-Q4.gguf"), "llama")

    by_name = {m.name: m for m in scan_dirs([str(root)])}

    assert by_name["VL-Q4"].mmproj is not None
    assert by_name["Text-Q4"].mmproj is None


def test_projector_matching_the_model_name_wins(tmp_path):
    """Two models sharing a folder must each get their own projector."""
    root = tmp_path / "models"
    _write_gguf(str(root / "Alpha-Q4.gguf"), "qwen3vl")
    _write_gguf(str(root / "Beta-Q4.gguf"), "qwen3vl")
    _write_gguf(str(root / "mmproj-Alpha-Q4-F16.gguf"), "clip")
    _write_gguf(str(root / "mmproj-Beta-Q4-F16.gguf"), "clip")

    by_name = {m.name: m for m in scan_dirs([str(root)])}

    assert os.path.basename(by_name["Alpha-Q4"].mmproj) == "mmproj-Alpha-Q4-F16.gguf"
    assert os.path.basename(by_name["Beta-Q4"].mmproj) == "mmproj-Beta-Q4-F16.gguf"


def test_ambiguous_projector_choice_is_deterministic(tmp_path):
    """Several projectors, none name-matched: pick one, and the same one twice."""
    root = tmp_path / "models"
    _write_gguf(str(root / "Model-Q4.gguf"), "qwen3vl")
    _write_gguf(str(root / "mmproj-F16.gguf"), "clip")
    _write_gguf(str(root / "mmproj-BF16.gguf"), "clip")

    first = scan_dirs([str(root)])[0].mmproj
    second = scan_dirs([str(root)])[0].mmproj

    assert first is not None
    assert first == second


# ── 2. Symlink deduplication ────────────────────────────────────────────

def test_symlinked_copy_is_not_listed_twice(tmp_path):
    """LM Studio keeps a folder of symlinks pointing back at the real files."""
    root = tmp_path / "models"
    real = root / "GGUF" / "Mistral-7B-Q4_K_M.gguf"
    _write_gguf(str(real), "llama")

    link_dir = root / "LMStudio" / "gguf-local" / "Mistral-7B-Q4_K_M"
    link_dir.mkdir(parents=True)
    os.symlink(str(real), str(link_dir / "Mistral-7B-Q4_K_M.gguf"))

    models = scan_dirs([str(root)])

    assert len(models) == 1, f"expected one model, got {[m.path for m in models]}"
    assert models[0].path == os.path.realpath(str(real))


def test_same_model_reached_through_two_configured_dirs_appears_once(tmp_path):
    """A user may configure both a directory and an alias of it."""
    root = tmp_path / "models"
    _write_gguf(str(root / "Phi-3.5-mini-Q4_K_M.gguf"), "phi3")
    alias = tmp_path / "AI_Models"
    os.symlink(str(root), str(alias))

    models = scan_dirs([str(root), str(alias)])

    assert len(models) == 1


def test_distinct_models_are_still_listed_separately(tmp_path):
    """Deduplication must not collapse genuinely different files."""
    root = tmp_path / "models"
    _write_gguf(str(root / "Mistral-7B-Q4_K_M.gguf"), "llama")
    _write_gguf(str(root / "Phi-3.5-mini-Q4_K_M.gguf"), "phi3")

    assert len(scan_dirs([str(root)])) == 2


def test_model_id_is_stable_across_the_symlink_and_the_real_path(tmp_path):
    """The id keys chat sessions, so it must not change with the route taken."""
    root = tmp_path / "models"
    real = root / "real" / "Model-Q4.gguf"
    _write_gguf(str(real), "llama")

    direct = scan_dirs([str(root / "real")])[0].id

    alias = tmp_path / "alias"
    os.symlink(str(root / "real"), str(alias))
    through_link = scan_dirs([str(alias)])[0].id

    assert direct == through_link

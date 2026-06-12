"""Tests for services/localmodels/gguf_meta.py — GGUF header reader + classifier."""
from __future__ import annotations

import struct


def _make_gguf(arch: str | None = None, extra_kv: list[tuple] | None = None) -> bytes:
    """Build a minimal syntactically-valid GGUF byte string.

    extra_kv is a list of (key, type_int, value_bytes) tuples added BEFORE the
    arch key, letting tests exercise type-skipping.
    """
    magic = b"GGUF"
    version = struct.pack("<I", 3)
    n_tensors = struct.pack("<Q", 0)

    def encode_str(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    kv_parts: list[bytes] = []

    if extra_kv:
        for key, t, val_bytes in extra_kv:
            kv_parts.append(encode_str(key) + struct.pack("<I", t) + val_bytes)

    if arch is not None:
        kv_parts.append(
            encode_str("general.architecture") +
            struct.pack("<I", 8) +  # STRING type
            encode_str(arch)
        )

    n_kv = struct.pack("<Q", len(kv_parts))
    return magic + version + n_tensors + n_kv + b"".join(kv_parts)


def test_reads_architecture(tmp_path):
    from services.localmodels.gguf_meta import read_architecture
    f = tmp_path / "model.gguf"
    f.write_bytes(_make_gguf("llama"))
    assert read_architecture(str(f)) == "llama"


def test_reads_diffusion_architecture(tmp_path):
    from services.localmodels.gguf_meta import read_architecture
    f = tmp_path / "diffusion.gguf"
    f.write_bytes(_make_gguf("diffusion-gemma"))
    assert read_architecture(str(f)) == "diffusion-gemma"


def test_returns_none_for_non_gguf(tmp_path):
    from services.localmodels.gguf_meta import read_architecture
    f = tmp_path / "notgguf.gguf"
    f.write_bytes(b"NOT_GGUF_FILE_DATA" + b"\x00" * 32)
    assert read_architecture(str(f)) is None


def test_returns_none_for_truncated_file(tmp_path):
    from services.localmodels.gguf_meta import read_architecture
    f = tmp_path / "trunc.gguf"
    f.write_bytes(b"GGUF\x03\x00\x00")  # truncated after magic + partial version
    assert read_architecture(str(f)) is None


def test_skips_non_string_kv_before_arch(tmp_path):
    """A u32 KV (type 4) appearing before the arch key must be skipped correctly."""
    from services.localmodels.gguf_meta import read_architecture
    f = tmp_path / "mixed.gguf"
    # Insert a u32 KV (type=4, 4 bytes) before the arch key
    u32_kv = ("general.quantization_version", 4, struct.pack("<I", 2))
    f.write_bytes(_make_gguf("nomic-bert", extra_kv=[u32_kv]))
    assert read_architecture(str(f)) == "nomic-bert"


def test_returns_none_for_gguf_without_arch_key(tmp_path):
    from services.localmodels.gguf_meta import read_architecture
    f = tmp_path / "noarch.gguf"
    f.write_bytes(_make_gguf(arch=None))  # no KVs at all
    assert read_architecture(str(f)) is None


def test_returns_none_for_missing_file():
    from services.localmodels.gguf_meta import read_architecture
    assert read_architecture("/tmp/does_not_exist_xyz.gguf") is None


# ---------------------------------------------------------------------------
# classify_architecture tests
# ---------------------------------------------------------------------------

def test_classify_diffusion_gemma_is_unsupported():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("diffusion-gemma") == "unsupported"


def test_classify_nomic_bert_is_embedding():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("nomic-bert") == "embedding"


def test_classify_bert_is_embedding():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("bert") == "embedding"


def test_classify_gte_is_embedding():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("gte") == "embedding"


def test_classify_llama_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("llama") == "chat"


def test_classify_qwen2_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("qwen2") == "chat"


def test_classify_qwen35_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("qwen35") == "chat"


def test_classify_gemma4_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("gemma4") == "chat"


def test_classify_phi3_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("phi3") == "chat"


def test_classify_qwen3moe_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("qwen3moe") == "chat"


def test_classify_lfm2moe_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("lfm2moe") == "chat"


def test_classify_nemotron_h_moe_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("nemotron_h_moe") == "chat"


def test_classify_gpt_oss_is_chat():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("gpt-oss") == "chat"


def test_classify_none_returns_none():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture(None) is None


def test_classify_dream_is_unsupported():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("dream") == "unsupported"


def test_classify_whisper_is_unsupported():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("whisper") == "unsupported"


def test_classify_embed_in_arch_is_embedding():
    from services.localmodels.gguf_meta import classify_architecture
    assert classify_architecture("snowflake-arctic-embed") == "embedding"

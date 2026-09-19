"""Discover MLX model folders and locate the mlx_lm runtime that serves them.

An MLX model is a directory, not a file: config.json plus *.safetensors
shards. `mlx_lm.server` speaks the OpenAI API, so Apollo serves these beside
llama.cpp with the same lifecycle (see server_manager).
"""
from __future__ import annotations

import ast
import glob
import hashlib
import json
import logging
import os
import shutil
import subprocess
from functools import lru_cache
from typing import Optional

from services.localmodels.config import get_local_model_dirs, get_mlx_python_path

logger = logging.getLogger(__name__)

_SKIP_DIRS = {"cache", ".cache", "llama-cache", "ollama", ".ollama", "blobs", "tmp", ".git"}


# Where `pipx install mlx-lm`, `uv tool install mlx-lm` and Homebrew put the
# entry point. Checked explicitly: a macOS GUI app's PATH has none of them.
_SERVER_CANDIDATES = (
    "~/.local/bin/mlx_lm.server",
    "/opt/homebrew/bin/mlx_lm.server",
    "/usr/local/bin/mlx_lm.server",
)
# Virtualenv names people give an mlx_lm install, looked for inside each
# configured model directory and in the home directory.
_VENV_NAMES = (".mlx_lm_venv", "mlx_lm_venv", ".mlx-venv", ".mlx_venv", ".venv", "venv")


def _venv_with_mlx_lm(root: str) -> Optional[str]:
    """Python of a venv under `root` that has mlx_lm installed (no import)."""
    for name in _VENV_NAMES:
        python = os.path.join(root, name, "bin", "python")
        if not os.path.isfile(python):
            continue
        if glob.glob(os.path.join(root, name, "lib", "python*", "site-packages", "mlx_lm", "server.py")):
            return python
    return None


def find_mlx_runtime() -> Optional[list[str]]:
    """Command prefix that runs mlx_lm.server, or None when not installed.

    Configured python (Settings / APOLLO_MLX_PYTHON) wins. Otherwise, so MLX
    models work without setup: an `mlx_lm.server` entry point on PATH or in a
    standard install location, then a venv with mlx_lm inside a configured
    model directory or the home directory. Apple Silicon only.
    """
    python = get_mlx_python_path()
    if python:
        return [python, "-m", "mlx_lm.server"] if os.path.isfile(python) else None
    server = shutil.which("mlx_lm.server")
    if server:
        return [server]
    for cand in _SERVER_CANDIDATES:
        path = os.path.expanduser(cand)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return [path]
    for root in [*get_local_model_dirs(), os.path.expanduser("~")]:
        python = _venv_with_mlx_lm(os.path.expanduser(root))
        if python:
            return [python, "-m", "mlx_lm.server"]
    return None


def _runtime_python(runtime: tuple[str, ...]) -> Optional[str]:
    """Interpreter behind a runtime command (an entry point's shebang)."""
    if runtime[1:2] == ("-m",):
        return runtime[0]
    try:
        with open(runtime[0], "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return None
    return first[2:].strip().split()[0] if first.startswith("#!") else None


@lru_cache(maxsize=4)
def _supported_types(runtime: tuple[str, ...]) -> frozenset[str]:
    """model_type values the runtime's mlx_lm can load (incl. remappings).

    Read from the package's files, never by importing it: `import mlx_lm`
    initialises Metal and took 40 s on a busy disk, which is too slow for a
    scan that runs at startup and behind GET /api/local-models.
    """
    python = _runtime_python(runtime)
    if not python:
        return frozenset()
    try:
        purelib = subprocess.run(
            [python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        pkg = os.path.join(purelib, "mlx_lm")
        names = {f[:-3] for f in os.listdir(os.path.join(pkg, "models")) if f.endswith(".py")}
        with open(os.path.join(pkg, "utils.py"), "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "MODEL_REMAPPING" for t in node.targets):
                remap = ast.literal_eval(node.value)
                names |= {k for k, v in remap.items() if v in names}
        return frozenset(names)
    except (OSError, subprocess.SubprocessError, ValueError, SyntaxError) as e:
        logger.warning("Could not list mlx_lm model types via %s: %s", python, e)
        return frozenset()


def supported_types() -> frozenset[str]:
    runtime = find_mlx_runtime()
    return _supported_types(tuple(runtime)) if runtime else frozenset()


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_mlx_dir(path: str, files: list[str]) -> bool:
    """config.json + safetensors, and MLX-format (quantized by mlx or named so).

    Plain Hugging Face checkpoints also carry config.json + safetensors; the
    MLX marker keeps a Transformers folder from being offered here.
    """
    if "config.json" not in files or not any(f.endswith(".safetensors") for f in files):
        return False
    if "mlx" in os.path.basename(path).lower():
        return True
    return "quantization" in _read_json(os.path.join(path, "config.json"))


# "python|model path|template mtime" -> [parser, inject] or None. Persisted
# under the data dir: inferring a parser imports transformers in the runtime
# python (seconds to a minute on a busy disk), so pay it once per model.
_PARSER_CACHE: dict[str, Optional[list]] = {}
_PARSER_CACHE_LOADED = False


def _parser_cache_path() -> str:
    from src.constants import DATA_DIR
    return os.path.join(DATA_DIR, "mlx_parsers.json")


def _load_parser_cache() -> None:
    global _PARSER_CACHE_LOADED
    if _PARSER_CACHE_LOADED:
        return
    _PARSER_CACHE_LOADED = True
    try:
        with open(_parser_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _PARSER_CACHE.update(data)
    except (OSError, ValueError):
        pass


def _prune_parser_cache(version: str) -> None:
    """Drop entries from older script versions or for folders that are gone."""
    for key in list(_PARSER_CACHE):
        parts = key.split("|", 2)
        folder = parts[2].rsplit("|", 1)[0] if len(parts) == 3 else ""
        if parts[0] != version or not os.path.isdir(folder):
            del _PARSER_CACHE[key]


def _save_parser_cache() -> None:
    try:
        path = _parser_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_PARSER_CACHE, f)
        os.replace(tmp, path)
    except OSError as e:
        logger.debug("mlx parser cache not saved: %s", e)

_PARSER_SCRIPT = """
import importlib, json, os, pkgutil, sys
from mlx_lm.tokenizer_utils import _infer_tool_parser
import mlx_lm.tool_parsers as tool_parsers
out = {}
def infer(d):
    def read(name):
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    cfg = json.loads(read("tokenizer_config.json") or "{}")
    t = cfg.get("chat_template") or read("chat_template.jinja")
    if not t and read("chat_template.json"):
        t = json.loads(read("chat_template.json")).get("chat_template")
    if isinstance(t, list):
        t = " ".join(x.get("template", "") for x in t if isinstance(x, dict))
    t = t or ""
    if all(tok in t for tok in ("<|channel|>", "<|message|>", "<|start|>")):
        return ["harmony", False]  # gpt-oss: Apollo parses the markup itself
    name = cfg.get("tool_parser_type") or _infer_tool_parser(t)
    inject = False
    if not name:
        # mlx_lm's inference keys on template quirks and misses models whose
        # template uses one of its parsers' exact markers (LFM2.5 -> pythonic).
        for mod in pkgutil.iter_modules(tool_parsers.__path__):
            cand = importlib.import_module("mlx_lm.tool_parsers." + mod.name)
            if cand.tool_call_start and cand.tool_call_end and \
                    cand.tool_call_start in t and cand.tool_call_end in t:
                name, inject = mod.name, True
                break
    # A parser with no end marker (mistral: "[TOOL_CALLS]" ... "") leaves the
    # call buffered when generation stops; mlx_lm 0.31 then returns an empty
    # message (seen with Devstral-Small-2). Prompt-mode tools work instead.
    if name and not importlib.import_module("mlx_lm.tool_parsers." + name).tool_call_end:
        return [False, False]
    return [name or False, inject]
for d in json.load(sys.stdin):
    try:
        out[d] = infer(d)
    except Exception:
        out[d] = None  # this folder only: unknown, not "no tools"
print(json.dumps(out))
"""


def _template_mtime(path: str) -> float:
    return max((os.path.getmtime(os.path.join(path, f)) for f in
                ("tokenizer_config.json", "chat_template.jinja", "chat_template.json")
                if os.path.isfile(os.path.join(path, f))), default=0.0)


def tool_parsers(paths: list[str]) -> dict[str, Optional[tuple[str | bool, bool]]]:
    """(parser, inject) per model folder, or None when it could not be told.

    parser: an mlx_lm parser name (native tool calls work), "harmony" (Apollo
    parses gpt-oss output itself), or False (no usable parser: prompt-mode
    tools). inject: mlx_lm would not pick this parser on its own, so the
    launch must set tool_parser_type in an overlay tokenizer_config.json.

    A template that mentions tools is not enough: mlx_lm only returns
    structured tool_calls when it recognises the template's call syntax
    (LFM2.5, gpt-oss and others come back as plain text). The runtime's own
    inference is asked once per folder and cached until the template changes.
    """
    runtime = find_mlx_runtime()
    python = _runtime_python(tuple(runtime)) if runtime else None
    if not python:
        return {p: None for p in paths}
    _load_parser_cache()
    version = hashlib.sha1(_PARSER_SCRIPT.encode()).hexdigest()[:8]
    keys = {p: f"{version}|{python}|{p}|{_template_mtime(p)}" for p in paths}
    todo = [p for p, k in keys.items() if k not in _PARSER_CACHE]
    if todo:
        try:
            out = subprocess.run([python, "-c", _PARSER_SCRIPT], input=json.dumps(todo),
                                 capture_output=True, text=True, timeout=300, check=True).stdout
            found = json.loads(out)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.warning("Could not infer mlx_lm tool parsers via %s: %s", python, e)
            found = {}
        for p in todo:
            if p in found:
                _PARSER_CACHE[keys[p]] = found[p]
        if found:
            _prune_parser_cache(version)
            _save_parser_cache()
    return {p: (tuple(v) if v is not None else None)
            for p, v in ((p, _PARSER_CACHE.get(k)) for p, k in keys.items())}


def dir_size(path: str) -> int:
    total = 0
    for f in os.listdir(path):
        if f.endswith(".safetensors"):
            try:
                total += os.path.getsize(os.path.join(path, f))
            except OSError:
                pass
    return total


def model_type(path: str) -> str:
    return str(_read_json(os.path.join(path, "config.json")).get("model_type") or "")

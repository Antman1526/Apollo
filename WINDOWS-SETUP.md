# Apollo on Windows — Setup

Running Apollo natively on Windows (no Docker), including local GGUF models via
llama.cpp. Works from a normal `git clone` or from a slimmed source zip — in the
zip case runtime data, the virtualenv, build artifacts, and all secrets (`.env`)
are stripped, so it starts as a fresh install.

For the bare install/launch steps see the **Native Windows** section of the
[README](README.md); this guide adds the local-model setup on top.

## 1. Prerequisites

Apollo needs exactly three things: **Python 3.11+**, **Git for Windows**, and a
**recent llama.cpp**.

> **Easiest path: just run the launcher (section 2).** It detects anything
> missing and **offers to install it for you** via winget — it always asks
> first (`Install X now via winget? [Y/n]`) and never installs silently.
> Python is required; Git and llama.cpp are offered as optional. The rest of
> this section is for installing manually, or for machines without winget.

Check what you already have — open PowerShell
(<kbd>Win</kbd>+<kbd>X</kbd> → *Terminal*) and run:

```powershell
python --version
git --version
llama-server --version
```

Each line prints a version if the tool is installed. For anything missing (or
erroring), install it below. Two rules that save the most head-scratching:

- **After installing anything, open a NEW PowerShell window.** PATH changes
  don't reach windows that are already open, so a just-installed tool looks
  "not found" until you do.
- The `winget` commands below use Windows' built-in package manager — nothing
  extra to install on Windows 10/11. If a `winget` command isn't recognized,
  use the download link given for each tool instead.

### Python 3.11+ (not installed?)

Either:

- `winget install --id Python.Python.3.12 -e`, or
- download the installer from https://www.python.org/downloads/windows/ and run
  it — **check "Add python.exe to PATH"** on the first screen (it is unchecked
  by default, and missing it is the #1 Python-on-Windows problem).

Verify in a new window: `python --version` → `Python 3.12.x`.

> **Gotcha — Microsoft Store alias:** on a fresh Windows install, typing
> `python` may open the Microsoft Store instead of running Python. Either
> install from the Store prompt, or disable the alias under
> **Settings → Apps → Advanced app settings → App execution aliases** (turn off
> both `python.exe` entries) so the python.org install wins.

### Git for Windows (not installed?)

Either:

- `winget install --id Git.Git -e`, or
- download from https://git-scm.com/download/win and run the installer — the
  defaults are fine on every screen (Apollo's launcher uses Git's bundled bash
  for first-time setup scripts, which the default install includes).

Verify in a new window: `git --version`.

### llama.cpp (for local GGUF models)

Either:

- `winget install llama.cpp` — then immediately `winget upgrade llama.cpp` to
  make sure you have a current build (see the warning below), or
- download a release build from https://github.com/ggml-org/llama.cpp/releases:
  on the latest release, expand **Assets** and pick the file for your machine —
  the **`cudart`/CUDA** zip if you have an NVIDIA GPU (most gaming laptops,
  e.g. ROG), otherwise the plain **`win-x64`** CPU zip. Unzip it anywhere,
  e.g. `C:\llama.cpp\`, so that `C:\llama.cpp\llama-server.exe` exists.

Verify: `llama-server --version` (or run
`C:\llama.cpp\llama-server.exe --version` if it isn't on PATH — Apollo can be
pointed at the exact file either way, see step 3).

**Get a RECENT build.** Newer model architectures need newer llama.cpp, and a
stale package-manager copy is the single most confusing failure here: the
model appears in the picker, then refuses to start with

```
llama_model_load: error loading model: missing tensor 'blk.64.ssm_conv1d.weight'
```

That is not an Apollo bug and not a corrupt download — it means this
`llama-server` predates support for that architecture. `ssm_*` tensors are
state-space (Mamba-style) layers used by the hybrid attention+SSM models
(Qwen 3.5 / 3.6 / 3.8 and similar); older builds simply cannot load them.

Fix: upgrade llama.cpp (`winget upgrade llama.cpp`, or grab the latest
release build) and start the model again. If Apollo still picks up an old
copy from your PATH, set the exact binary in **Settings → AI → Local Models →
llama-server Binary** (e.g. `C:\llama.cpp\llama-server.exe`) — the status
line under that field shows which binary is actually in use.

### Do I need Ollama or LM Studio?

**No — neither is required.** Apollo serves local GGUF files itself by launching
`llama-server` on demand, so llama.cpp above is the only local-model dependency.

They are optional, and only useful if you already run them:

- **LM Studio** — if you have it, Apollo scans its model folder
  (`%USERPROFILE%\.lmstudio\models`) by default, so downloads you already made
  show up without copying anything. That is a convenience, not a requirement.
- **Ollama** — supported as an *additional* endpoint. Add it under
  **Settings → AI → Add Models** to use models it already serves; Apollo talks
  to it over HTTP and never installs or manages it.

Everything in this guide works with neither installed.

## 2. Launch

From PowerShell in this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

First run creates a virtualenv, installs dependencies, prints an **admin
password** (save it), and starts the server on http://127.0.0.1:7000.

## 3. Point Apollo at your models

1. Put your `.gguf` files in a folder, e.g. `C:\AI_Models`
   (`%USERPROFILE%\Desktop\AI_Models`, `%USERPROFILE%\AI_Models`, and
   LM Studio's `%USERPROFILE%\.lmstudio\models` are now scanned by default).
2. In the app: **Settings → AI → Local Models → Scan Directories** — add your
   folder and hit **Rescan**. Discovered models appear in the model picker.
3. **llama-server Binary** (new setting, same card): leave empty to
   auto-detect. If Apollo says llama-server was not found, paste the full path
   here, e.g. `C:\llama.cpp\llama-server.exe`, and Save. The status line under
   the field shows which binary is in use.
   - Equivalent env var: `APOLLO_LLAMA_SERVER=C:\llama.cpp\llama-server.exe`
   - Auto-detect also checks PATH, scoop shims, `%LOCALAPPDATA%\llama.cpp`,
     `%ProgramFiles%\llama.cpp`, and `%USERPROFILE%\llama.cpp\build\bin\Release`.

## 4. Verify

- Settings → AI → Local Models lists your GGUF files.
- Selecting a local model in the chat picker starts `llama-server`
  automatically and the model responds.
- If a model won't start, the error message now says exactly whether the
  binary path is wrong or llama.cpp isn't installed.

## Where the Windows support lives in the code

- `services/localmodels/config.py` — Windows-aware default scan dirs;
  `llama_server_path` setting + `APOLLO_LLAMA_SERVER` env var.
- `services/localmodels/server_manager.py` — Windows `llama-server.exe`
  auto-detect candidates; configured path wins; clearer launch errors.
- `routes/localmodels_routes.py` — `GET/PUT /api/local-models/binary`.
- `static/index.html` + `static/js/settingsAiExtras.js` — the "llama-server
  Binary" field in Settings → AI → Local Models.

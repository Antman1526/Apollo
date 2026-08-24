# Apollo on Windows — Setup

Running Apollo natively on Windows (no Docker), including local GGUF models via
llama.cpp. Works from a normal `git clone` or from a slimmed source zip — in the
zip case runtime data, the virtualenv, build artifacts, and all secrets (`.env`)
are stripped, so it starts as a fresh install.

For the bare install/launch steps see the **Native Windows** section of the
[README](README.md); this guide adds the local-model setup on top.

## 1. Prerequisites

- **Python 3.11+** — install from https://www.python.org/downloads/ and check
  "Add python.exe to PATH" in the installer.
- **Git for Windows** — https://git-scm.com/download/win (the launcher uses its
  bundled bash for first-time setup scripts).
- **llama.cpp** (for local GGUF models) — either:
  - `winget install llama.cpp`, or
  - download a release build from https://github.com/ggml-org/llama.cpp/releases
    (pick the CUDA build if you have an NVIDIA GPU) and unzip it anywhere,
    e.g. `C:\llama.cpp\`.

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

#Requires -Version 5.1
<#
  Apollo - native Windows launcher (no Docker).

  One command to: create a virtualenv, install dependencies, run first-time
  setup (prints an admin password on first run), and start the server.
  Safe to re-run - it skips whatever already exists.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7000 -BindHost 127.0.0.1

  Tip: bind 127.0.0.1 (default) for local-only use. Use 0.0.0.0 only when you
  intentionally want other devices on your LAN to reach it.
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Find-GitBash {
    $cmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $roots = @()
    foreach ($name in @("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LocalAppData")) {
        $base = [Environment]::GetEnvironmentVariable($name)
        if ($base) { $roots += (Join-Path $base "Git") }
    }
    $roots += @("C:\Program Files\Git", "C:\Program Files (x86)\Git")

    foreach ($root in ($roots | Select-Object -Unique)) {
        foreach ($relative in @("bin\bash.exe", "usr\bin\bash.exe")) {
            $candidate = Join-Path $root $relative
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

# --- Missing-prerequisite auto-install (via winget, always with consent) ---

function Test-Winget {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Update-SessionPath {
    # A fresh install lands in the registry PATH, not in this session's copy.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ";"
}

function Confirm-Install($what) {
    $answer = Read-Host ("    Install {0} now via winget? [Y/n]" -f $what)
    return ($answer -eq "" -or $answer -match "^[Yy]")
}

function Install-WithWinget($displayName, $wingetArgs) {
    Write-Step ("Installing {0} (winget)" -f $displayName)
    & winget install @wingetArgs --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("winget could not install {0} (exit code {1})." -f $displayName, $LASTEXITCODE) -ForegroundColor Yellow
        return $false
    }
    Update-SessionPath
    return $true
}

# 1. Locate a Python interpreter (3.11+ required)
Write-Step "Checking for Python"
function Get-PythonVersionText($launcher, $launcherArgs) {
    try {
        return (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim()
    } catch {
        return $null
    }
}

function Resolve-Python {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($v in @("-3.13", "-3.12", "-3.11")) {
            $ver = Get-PythonVersionText $pyLauncher.Source @($v)
            if ($ver) {
                return @{ Exe = $pyLauncher.Source; Args = @($v); Version = $ver }
            }
        }
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $ver = Get-PythonVersionText $pythonCmd.Source @()
        if ($ver) {
            $versionParts = $ver.Split('.')
            $major = [int]$versionParts[0]
            $minor = [int]$versionParts[1]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                return @{ Exe = $pythonCmd.Source; Args = @(); Version = $ver }
            }
        }
    }
    return $null
}

$py = Resolve-Python
if (-not $py) {
    Write-Host "Python 3.11+ was not found on this machine." -ForegroundColor Yellow
    if ((Test-Winget) -and (Confirm-Install "Python 3.12")) {
        if (Install-WithWinget "Python 3.12" @("--id", "Python.Python.3.12", "-e")) {
            $py = Resolve-Python
            if (-not $py) {
                Write-Host "Installed, but this window can't see it yet - a new PowerShell window will." -ForegroundColor Yellow
            }
        }
    }
}
if (-not $py) {
    Fail "Couldn't find Python 3.11+ for Windows setup. Install Python 3.11+ (or open the Python launcher with 'py -3.11') from https://www.python.org/downloads/, then re-run this script from a NEW PowerShell window."
}
$pyExe = $py.Exe
$pyArgs = $py.Args
$pyVersion = $py.Version
$pythonLabel = ("Using Python {0}: {1} {2}" -f $pyVersion, $pyExe, ($pyArgs -join ' ')).TrimEnd()
Write-Host $pythonLabel

# 2. Create the virtualenv if missing
$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Step "Creating virtual environment (venv)"
    & $pyExe @pyArgs -m venv venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Failed to create the virtual environment." }
} else {
    Write-Host "venv already exists - skipping creation."
}

# 3. Install / update dependencies
Write-Step "Installing dependencies (first run can take a few minutes)"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Scroll up for the pip error." }

# 4. First-time setup (creates data dirs, DB, .env, admin user)
Write-Step "Running first-time setup"
& $venvPy setup.py
if ($LASTEXITCODE -ne 0) { Fail "setup.py failed." }

# 5. Optional prerequisites: Git Bash and llama.cpp (offer to install, never force)
if (-not (Find-GitBash)) {
    Write-Host ""
    Write-Host "NOTE: Git Bash (bash.exe) was not found on PATH." -ForegroundColor Yellow
    Write-Host "      The core app works without it. For full Cookbook background" -ForegroundColor Yellow
    Write-Host "      downloads and the agent shell tool, Git for Windows is needed." -ForegroundColor Yellow
    if ((Test-Winget) -and (Confirm-Install "Git for Windows")) {
        Install-WithWinget "Git for Windows" @("--id", "Git.Git", "-e") | Out-Null
        if (-not (Find-GitBash)) {
            Write-Host "      If bash still isn't found, it will be after a new PowerShell window." -ForegroundColor Yellow
        }
    } else {
        Write-Host "      Manual install: https://git-scm.com/download/win" -ForegroundColor Yellow
    }
}

if (-not (Get-Command llama-server -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "NOTE: llama.cpp (llama-server.exe) was not found on PATH." -ForegroundColor Yellow
    Write-Host "      Only needed to run local GGUF models; cloud/remote endpoints work without it." -ForegroundColor Yellow
    if ((Test-Winget) -and (Confirm-Install "llama.cpp (local GGUF models)")) {
        # Install then upgrade: winget's cached manifest can lag, and a stale
        # llama.cpp can't load newer model architectures (see WINDOWS-SETUP.md).
        if (Install-WithWinget "llama.cpp" @("llama.cpp")) {
            & winget upgrade llama.cpp --accept-package-agreements --accept-source-agreements 2>$null | Out-Null
            Update-SessionPath
        }
    } else {
        Write-Host "      Manual install: see WINDOWS-SETUP.md section 1 (CUDA build for NVIDIA GPUs)." -ForegroundColor Yellow
    }
}

# 6. Start the server (use `python -m uvicorn` - bare `uvicorn` may not be on PATH)
Write-Step ("Starting Apollo at http://{0}:{1}" -f $BindHost, $Port)
Write-Host "Press Ctrl+C to stop."
Write-Host ""
# Run Paperclip in native mode (Apollo supervises it + auto-provisions Node,
# reusing any already-running instance). Enabled by default; set
# PAPERCLIP_ENABLED=false to turn it off.
if (-not $env:PAPERCLIP_MODE) { $env:PAPERCLIP_MODE = "native" }
if (-not $env:PAPERCLIP_ENABLED) { $env:PAPERCLIP_ENABLED = "true" }
& $venvPy -m uvicorn app:app --host $BindHost --port $Port

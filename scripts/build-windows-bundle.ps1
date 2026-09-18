#requires -Version 5.1
<#!
Build a self-contained Windows x64 Apollo portable ZIP.

The checked-in requirements remain the runtime source of truth. PyInstaller is
installed as a build tool, and Chromium is placed beside the frozen runtime so
the browser feature does not require a user's global Playwright cache.

Usage:
  powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-bundle.ps1
  ... -Python python -Version 1.1.0-rc.1 -OutputDirectory .\dist
#>
param(
    [string]$Python = "python",
    [string]$Version = "1.1.0-rc.1",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $Repo "dist" }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Apollo Windows bundle requires a 64-bit Windows host."
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$') {
    throw "Version must look like 1.2.3 or 1.2.3-rc.1."
}

function Invoke-Python([string[]]$Arguments) {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed: $Python $($Arguments -join ' ')" }
}

$env:APOLLO_VERSION = $Version
$env:APOLLO_TARGET_ARCH = ""
$BrowserPath = Join-Path $Repo "packaging\playwright-browsers"
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserPath
$BuildPath = Join-Path $Repo "build"
$OneDir = Join-Path $OutputDirectory "apollo"
$StageRoot = Join-Path ([IO.Path]::GetTempPath()) ("apollo-windows-" + [guid]::NewGuid().ToString("N"))
$Stage = Join-Path $StageRoot "Apollo"
$ZipPath = Join-Path $OutputDirectory ("Apollo-" + $Version + "-windows-x64.zip")

Push-Location $Repo
try {
    $SourceVersion = (& $Python -c "from src.constants import APP_VERSION; print(APP_VERSION)").Trim()
    if ($LASTEXITCODE -ne 0 -or $Version -ne $SourceVersion) {
        throw "Version $Version does not match source APP_VERSION $SourceVersion."
    }
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    Invoke-Python -Arguments @("-m", "pip", "install", "pyinstaller")

    $ChromiumExecutable = Get-ChildItem -Path $BrowserPath -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @("chrome.exe", "headless_shell.exe") } | Select-Object -First 1
    if (-not $ChromiumExecutable) {
        New-Item -ItemType Directory -Force -Path $BrowserPath | Out-Null
        Invoke-Python -Arguments @("-m", "playwright", "install", "chromium")
    }

    if (Test-Path $BuildPath) { Remove-Item -Recurse -Force $BuildPath }
    if (Test-Path $OneDir) { Remove-Item -Recurse -Force $OneDir }
    Invoke-Python -Arguments @("-m", "PyInstaller", "packaging\apollo.spec", "--noconfirm", "--distpath", $OutputDirectory, "--workpath", $BuildPath)

    $FrozenExe = Join-Path $OneDir "apollo.exe"
    if (-not (Test-Path $FrozenExe)) { throw "PyInstaller did not produce $FrozenExe" }
    $BundledBrowser = Join-Path $OneDir "_internal\playwright-browsers"
    if (Test-Path $BundledBrowser) { Remove-Item -Recurse -Force $BundledBrowser }
    Copy-Item -Path $BrowserPath -Destination $BundledBrowser -Recurse -Force
    $Bytes = [IO.File]::ReadAllBytes($FrozenExe)
    if ($Bytes.Length -lt 0x40 -or $Bytes[0] -ne 0x4d -or $Bytes[1] -ne 0x5a) { throw "Frozen executable is not a PE file." }
    $PeOffset = [BitConverter]::ToInt32($Bytes, 0x3c)
    if ($PeOffset -lt 0 -or $PeOffset + 6 -gt $Bytes.Length -or $Bytes[$PeOffset] -ne 0x50 -or $Bytes[$PeOffset + 1] -ne 0x45) { throw "Frozen executable has no PE header." }
    $Machine = [BitConverter]::ToUInt16($Bytes, $PeOffset + 4)
    if ($Machine -ne 0x8664) { throw ("Expected x64 PE machine 0x8664, got 0x{0:x4}." -f $Machine) }

    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    Copy-Item -Path (Join-Path $OneDir "*") -Destination $Stage -Recurse -Force
    Rename-Item -Path (Join-Path $Stage "apollo.exe") -NewName "Apollo.exe"
    Set-Content -Path (Join-Path $Stage "VERSION.txt") -Value $Version -Encoding ascii
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
    Compress-Archive -Path $Stage -DestinationPath $ZipPath -CompressionLevel Optimal
    Expand-Archive -Path $ZipPath -DestinationPath (Join-Path $StageRoot "verify") -Force
    if (-not (Test-Path (Join-Path $StageRoot "verify\Apollo\Apollo.exe"))) { throw "Portable ZIP integrity check failed." }
    Write-Host ("Wrote {0} ({1:N0} bytes)" -f $ZipPath, (Get-Item $ZipPath).Length)
}
finally {
    Pop-Location
    if (Test-Path $StageRoot) { Remove-Item -Recurse -Force $StageRoot }
}

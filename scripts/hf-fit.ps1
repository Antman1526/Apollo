<#
.SYNOPSIS
  Find GGUF models on Hugging Face that fit this machine's memory.

.DESCRIPTION
  Reads the machine's RAM and GPU memory, searches Hugging Face for GGUF
  repositories, lists each quantised file with the memory it needs (file size
  plus a KV-cache estimate for the chosen context window) and says whether it
  fits on the GPU, in RAM with CPU offload, or not at all.

  Works without Apollo running. Windows PowerShell 5.1 and PowerShell 7+.

.PARAMETER Search
  Text to search for on Hugging Face (default: qwen3). Can also be an exact
  repo id such as unsloth/Qwen3.5-9B-GGUF.

.PARAMETER Limit
  How many repositories to inspect, most downloaded first (default 10).

.PARAMETER ContextK
  Context window, in thousands of tokens, to budget the KV cache for
  (default 16). Apollo's own default is 16K; use 128 or 256 for long chats.

.PARAMETER Json
  Emit the results as JSON instead of a table.

.PARAMETER Download
  Download the best-fitting file of the top repository into -Dest with
  huggingface-cli (or print the command when it is not installed).

.PARAMETER Dest
  Folder to download into. Defaults to Apollo's Windows scan folder,
  %USERPROFILE%\Desktop\AI_Models.

.EXAMPLE
  .\scripts\hf-fit.ps1 -Search "gemma 4" -ContextK 32
  .\scripts\hf-fit.ps1 -Search unsloth/Qwen3.5-9B-GGUF -Download
#>
[CmdletBinding()]
param(
    [string]$Search = "qwen3",
    [int]$Limit = 10,
    [int]$ContextK = 16,
    [switch]$Json,
    [switch]$Download,
    [string]$Dest = ""
)

$ErrorActionPreference = "Stop"
$GB = 1GB
if (-not $Dest) {
    # Apollo's default scan folder on Windows; $HOME on other systems (pwsh
    # runs on Linux/macOS too, and USERPROFILE is unset there).
    $homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $Dest = Join-Path $homeDir "Desktop/AI_Models"
}

function Get-MachineMemory {
    $ramBytes = 0; $vramBytes = 0; $gpuName = ""
    try {
        $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
        $ramBytes = [int64]$cs.TotalPhysicalMemory
    } catch { }
    if ($ramBytes -eq 0) {
        # Not Windows: /proc/meminfo (Linux) or sysctl (macOS).
        try {
            if (Test-Path /proc/meminfo) {
                $kb = (Select-String -Path /proc/meminfo -Pattern "^MemTotal:\s+(\d+)").Matches[0].Groups[1].Value
                $ramBytes = [int64]$kb * 1KB
            } elseif (Get-Command sysctl -ErrorAction SilentlyContinue) {
                $ramBytes = [int64](& sysctl -n hw.memsize 2>$null)
            }
        } catch { }
    }
    # NVIDIA: nvidia-smi reports real VRAM. Win32_VideoController.AdapterRAM
    # is a 32-bit field and lies above 4 GB, so it is only the fallback.
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        try {
            $line = & $smi.Source --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1
            if ($line) {
                $parts = $line -split ","
                $gpuName = $parts[0].Trim()
                $vramBytes = [int64]([double]$parts[1].Trim() * 1MB)
            }
        } catch { }
    }
    if ($vramBytes -eq 0) {
        try {
            $gpu = Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop | Sort-Object AdapterRAM -Descending | Select-Object -First 1
            if ($gpu) { $gpuName = $gpu.Name; $vramBytes = [int64]$gpu.AdapterRAM }
        } catch { }
    }
    [pscustomobject]@{ RamBytes = $ramBytes; VramBytes = $vramBytes; GpuName = $gpuName }
}

function Get-HfJson([string]$Url) {
    $headers = @{ "User-Agent" = "apollo-hf-fit/1.0" }
    if ($env:HF_TOKEN) { $headers["Authorization"] = "Bearer $($env:HF_TOKEN)" }
    Invoke-RestMethod -Uri $Url -Headers $headers -TimeoutSec 30
}

function Get-ParamsB([string]$Text) {
    # "9B", "27B", "35B-A3B" (active 3B on a 35B MoE: weights are still 35B)
    $m = [regex]::Match($Text, "(\d+(?:\.\d+)?)\s*[Bb](?![a-zA-Z])")
    if ($m.Success) { return [double]$m.Groups[1].Value }
    return 0
}

function Get-Quant([string]$Name) {
    $m = [regex]::Match($Name, "(?i)(UD-)?(IQ\d_[A-Z0-9_]+|Q\d(?:_[A-Z0-9]+)+|BF16|F16|FP16|F32|Q8_0)")
    if ($m.Success) { return $m.Value.ToUpper() }
    return ""
}

# KV cache per token (bytes) ≈ 2 (K,V) × layers × kv_heads × head_dim × 2 bytes.
# Without the header we scale from parameter count; ~10 KB/token at 8B in
# f16 matches llama.cpp for Qwen/Llama-class models within ~20%. Apollo
# launches with an 8-bit KV cache, which halves it.
function Get-KvBytesPerToken([double]$ParamsB) {
    if ($ParamsB -le 0) { $ParamsB = 8 }
    $f16 = 10KB * [math]::Sqrt($ParamsB / 8.0)
    return $f16 / 2
}

$mem = Get-MachineMemory
$ramGB = [math]::Round($mem.RamBytes / $GB, 1)
$vramGB = [math]::Round($mem.VramBytes / $GB, 1)
if (-not $Json) {
    Write-Host ("Machine: {0} GB RAM, {1}{2}" -f $ramGB, ($(if ($vramGB -gt 0) { "$vramGB GB VRAM" } else { "no discrete GPU memory found" })), $(if ($mem.GpuName) { " ($($mem.GpuName))" } else { "" }))
    Write-Host ("Budget:  {0}K-token context, 8-bit KV cache (Apollo's default)" -f $ContextK)
    Write-Host ""
}

# Repos: an exact id, or a search sorted by downloads.
if ($Search -match "^[\w.-]+/[\w.-]+$") {
    $repos = @(@{ id = $Search })
} else {
    $q = [uri]::EscapeDataString($Search)
    $repos = @(Get-HfJson "https://huggingface.co/api/models?search=$q&filter=gguf&sort=downloads&direction=-1&limit=$Limit")
}

$rows = @()
foreach ($repo in $repos) {
    $id = $repo.id
    try { $info = Get-HfJson "https://huggingface.co/api/models/$([uri]::EscapeDataString($id) -replace '%2F','/')?blobs=true" }
    catch { Write-Verbose "skip $id : $_"; continue }
    $params = Get-ParamsB $id
    $files = @($info.siblings | Where-Object { $_.rfilename -match "\.gguf$" -and $_.rfilename -notmatch "(?i)mmproj" })
    # Split models (name-00001-of-00003.gguf): sum the parts under the first.
    $groups = @{}
    foreach ($f in $files) {
        $base = [regex]::Replace($f.rfilename, "-\d{5}-of-\d{5}\.gguf$", ".gguf")
        if (-not $groups.ContainsKey($base)) { $groups[$base] = 0 }
        $groups[$base] += [int64]$f.size
    }
    foreach ($name in $groups.Keys) {
        $size = [int64]$groups[$name]
        if ($size -le 0) { continue }
        $kv = (Get-KvBytesPerToken $params) * $ContextK * 1024
        $need = $size + $kv + 1.0 * $GB   # weights + KV + runtime overhead
        $fit = "no"
        if ($mem.VramBytes -gt 0 -and $need -le $mem.VramBytes * 0.92) { $fit = "GPU" }
        elseif ($need -le $mem.RamBytes * 0.80) { $fit = $(if ($mem.VramBytes -gt 0) { "RAM (partial GPU offload)" } else { "RAM (CPU)" }) }
        $rows += [pscustomobject]@{
            Repo = $id; File = ($name -split "/")[-1]; Quant = (Get-Quant $name)
            SizeGB = [math]::Round($size / $GB, 1); NeedsGB = [math]::Round($need / $GB, 1)
            Fits = $fit; Downloads = [int64]($info.downloads)
        }
    }
}

$order = @{ "GPU" = 0; "RAM (partial GPU offload)" = 1; "RAM (CPU)" = 1; "no" = 2 }
$rows = $rows | Sort-Object @{ e = { $order[$_.Fits] } }, @{ e = { $_.NeedsGB }; Descending = $true }

if ($Json) { $rows | ConvertTo-Json -Depth 3; return }
if (-not $rows) { Write-Host "No GGUF files found for '$Search'."; return }
$rows | Format-Table Repo, File, Quant, SizeGB, NeedsGB, Fits -AutoSize
$fitting = @($rows | Where-Object { $_.Fits -ne "no" })
Write-Host ("{0} of {1} files fit this machine." -f $fitting.Count, $rows.Count)

if ($Download) {
    $best = $fitting | Select-Object -First 1
    if (-not $best) { Write-Host "Nothing fits; not downloading."; return }
    $cmd = "huggingface-cli download `"$($best.Repo)`" `"$($best.File)`" --local-dir `"$Dest`""
    $cli = Get-Command huggingface-cli -ErrorAction SilentlyContinue
    if ($cli) {
        Write-Host "Downloading $($best.File) ($($best.SizeGB) GB) to $Dest ..."
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        & $cli.Source download $best.Repo $best.File --local-dir $Dest
        Write-Host "Done. Apollo scans $Dest; click Rescan in Settings → AI → Local Models."
    } else {
        Write-Host "huggingface-cli is not installed (pip install -U huggingface_hub). Run:"
        Write-Host "  $cmd"
    }
}

[CmdletBinding()]
param([string]$InstallDir = "")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadHotfix = "B19A0610DEC8FEED32ED5DC7FE671000411AD218D7FAD2CCDD63D37C7B1BA0F3"
$PayloadManual = "A71D04A03A9004465073901A407D10F01F2CF53ED5F3FA41743D0F63B94F414C"
$PayloadDistManifest = Join-Path $PackageRoot "payload-dist.manifest"

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File not found: $Path" }
    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "")
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Read-Manifest([string]$Path) {
    $map = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("#")) { continue }
        $parts = $text.Split("|")
        if ($parts.Count -ne 3) { throw "Invalid manifest line: $text" }
        $rel = $parts[0].Replace("\", "/")
        $map[$rel] = [pscustomobject]@{ Length = [long]$parts[1]; Hash = $parts[2].ToUpperInvariant() }
    }
    return $map
}

function Assert-ManifestExact([string]$Root, [string]$Manifest, [string]$Label) {
    $base = (Resolve-Path -LiteralPath $Root).Path.TrimEnd("\")
    $map = Read-Manifest $Manifest
    $files = @(Get-ChildItem -LiteralPath $base -Recurse -File)
    if ($files.Count -ne $map.Count) { throw "$Label file count mismatch" }
    foreach ($file in $files) {
        $rel = $file.FullName.Substring($base.Length + 1).Replace("\", "/")
        if (-not $map.ContainsKey($rel)) { throw "$Label unexpected file: $rel" }
        if ($file.Length -ne $map[$rel].Length -or (Get-Sha256 $file.FullName) -ne $map[$rel].Hash) {
            throw "$Label content mismatch: $rel"
        }
    }
}

function Resolve-Root([string]$Requested) {
    if (-not $Requested) { $Requested = $env:TIANJUN_PATCH_INSTALL_DIR }
    if (-not $Requested) {
        foreach ($candidate in @("D:\TJKJ-LIN\tianjun-ai-vision", "D:\tianjun-ai-vision", "D:\tianjun\tianjun-ai-vision", "D:\tianjunkeji\tianjun-ai-vision", (Join-Path $env:ProgramFiles "tianjun-ai-vision"))) {
            if (Test-Path -LiteralPath (Join-Path $candidate "resources\backend") -PathType Container) { $Requested = $candidate; break }
        }
    }
    if (-not $Requested) { $Requested = Read-Host "Enter the full TianJun install directory" }
    if (-not $Requested) { throw "Install directory is empty" }
    return (Resolve-Path -LiteralPath $Requested).Path.TrimEnd("\")
}

function Assert-UnderRoot([string]$Path, [string]$Root, [string]$Label) {
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($Root.TrimEnd("\") + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label is outside install directory: $full"
    }
}

function Stop-InScope([string]$Root) {
    $allowed = @("tianjun-ai-vision.exe", "TianJun AI Vision.exe", "electron.exe", "python.exe", "pythonw.exe")
    $prefix = $Root.TrimEnd("\") + "\"
    foreach ($proc in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($proc.Name -notin $allowed) { continue }
        $inside = $proc.ExecutablePath -and $proc.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
        if (-not $inside -and $proc.CommandLine) { $inside = $proc.CommandLine.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }
        if ($inside) { Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop }
    }
}

try {
    $root = Resolve-Root $InstallDir
    $backend = Join-Path $root "resources\backend"
    $exe = @((Join-Path $root "tianjun-ai-vision.exe"), (Join-Path $root "TianJun AI Vision.exe")) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $exe) { throw "Application EXE not found" }
    $version = ((Get-Item -LiteralPath $exe).VersionInfo.ProductVersion | Out-String).Trim()
    if ($version -notmatch '^3\.53\.0(?:\.0)?$') { throw "ProductVersion mismatch: $version" }
    $backup = Join-Path $root "resources\hotfix_backups\v3530b"
    Assert-UnderRoot $backup $root "backup directory"
    $statePath = Join-Path $backup "state.json"
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw "v3530b backup state not found" }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $backupHotfix = Join-Path $backup "backend\hotfix.py"
    $backupManual = Join-Path $backup "backend\manual_pass_v3530a.py"
    if ((Get-Sha256 $backupHotfix) -ne $state.hotfix_sha256.ToUpperInvariant()) { throw "Backup hotfix.py hash mismatch" }
    if ((Get-Sha256 $backupManual) -ne $state.manual_sha256.ToUpperInvariant()) { throw "Backup manual_pass_v3530a.py hash mismatch" }
    $backupManifest = Join-Path $backup "old-a-dist.manifest"
    Assert-ManifestExact (Join-Path $backup "dist") $backupManifest "backup dist"
    $dist = Join-Path $root $state.dist_relative
    Assert-UnderRoot $dist $root "frontend dist"

    $alreadyRestored = $false
    try {
        $alreadyRestored = ((Get-Sha256 (Join-Path $backend "hotfix.py")) -eq $state.hotfix_sha256.ToUpperInvariant()) -and
            ((Get-Sha256 (Join-Path $backend "manual_pass_v3530a.py")) -eq $state.manual_sha256.ToUpperInvariant())
        if ($alreadyRestored) { Assert-ManifestExact $dist $backupManifest "restored dist" }
    } catch { $alreadyRestored = $false }
    if ($alreadyRestored) {
        Write-Host "[OK] Exact pre-v3.53.0b state is already restored"
        exit 0
    }

    if ((Get-Sha256 (Join-Path $backend "hotfix.py")) -ne $PayloadHotfix -or (Get-Sha256 (Join-Path $backend "manual_pass_v3530a.py")) -ne $PayloadManual) {
        throw "Active backend is not exact v3.53.0b; refusing to overwrite unknown files"
    }
    Assert-ManifestExact $dist $PayloadDistManifest "active v3.53.0b dist"
    Stop-InScope $root
    Copy-Item -LiteralPath $backupHotfix -Destination (Join-Path $backend "hotfix.py") -Force
    Copy-Item -LiteralPath $backupManual -Destination (Join-Path $backend "manual_pass_v3530a.py") -Force
    if (Test-Path -LiteralPath $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }
    Copy-Item -LiteralPath (Join-Path $backup "dist") -Destination $dist -Recurse -Force
    if ((Get-Sha256 (Join-Path $backend "hotfix.py")) -ne $state.hotfix_sha256.ToUpperInvariant()) { throw "Restored hotfix hash mismatch" }
    if ((Get-Sha256 (Join-Path $backend "manual_pass_v3530a.py")) -ne $state.manual_sha256.ToUpperInvariant()) { throw "Restored manual hash mismatch" }
    Assert-ManifestExact $dist $backupManifest "restored dist"
    Write-Host "[OK] Exact pre-v3.53.0b state restored. Backup kept at $backup"
    Write-Host "No DB or configuration was changed."
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}

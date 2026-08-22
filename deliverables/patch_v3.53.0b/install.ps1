[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$PatchVersion = "v3.53.0b"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExpectedPayloadHotfix = "B19A0610DEC8FEED32ED5DC7FE671000411AD218D7FAD2CCDD63D37C7B1BA0F3"
$ExpectedPayloadManual = "A71D04A03A9004465073901A407D10F01F2CF53ED5F3FA41743D0F63B94F414C"
$ExpectedOldAHotfix = "2DCCA7B05887FD2B06846C649789691C26C6D89C31C6DEB12E221DA5283F3213"
$ExpectedScannerR1Hotfix = "99856414B976D75394022A20D9358D80BB82EC59935490E75A690C607A59A2D1"
$ExpectedOldAManual = "F9D3B5BA6DFD982ED8322017934AD1CBE1809DD70CED114F9285828BF61C4EC5"
$PayloadDistManifest = Join-Path $PackageRoot "payload-dist.manifest"
$OldADistManifest = Join-Path $PackageRoot "old-a-dist.manifest"

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "File not found: $Path"
    }
    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "")
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Read-Manifest([string]$ManifestPath) {
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Manifest not found: $ManifestPath"
    }
    $entries = @{}
    foreach ($line in Get-Content -LiteralPath $ManifestPath) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("#")) { continue }
        $parts = $text.Split("|")
        if ($parts.Count -ne 3) { throw "Invalid manifest line: $text" }
        $rel = $parts[0].Replace("\", "/")
        $entries[$rel] = [pscustomobject]@{
            Relative = $rel
            Length = [long]$parts[1]
            Hash = $parts[2].ToUpperInvariant()
        }
    }
    return $entries
}

function Assert-ManifestExact([string]$Root, [string]$ManifestPath, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "$Label directory not found: $Root"
    }
    $rootPath = (Resolve-Path -LiteralPath $Root).Path.TrimEnd("\")
    $entries = Read-Manifest $ManifestPath
    $files = @(Get-ChildItem -LiteralPath $rootPath -Recurse -File)
    if ($files.Count -ne $entries.Count) {
        throw "$Label file count mismatch: expected $($entries.Count), actual $($files.Count)"
    }
    foreach ($file in $files) {
        $rel = $file.FullName.Substring($rootPath.Length + 1).Replace("\", "/")
        if (-not $entries.ContainsKey($rel)) { throw "$Label unexpected file: $rel" }
        $expected = $entries[$rel]
        if ($file.Length -ne $expected.Length) {
            throw "$Label size mismatch: $rel"
        }
        if ((Get-Sha256 $file.FullName) -ne $expected.Hash) {
            throw "$Label hash mismatch: $rel"
        }
    }
}

function Resolve-InstallDirectory([string]$Requested) {
    if ($Requested) { return $Requested }
    if ($env:TIANJUN_PATCH_INSTALL_DIR) { return $env:TIANJUN_PATCH_INSTALL_DIR }
    $candidates = @(
        "D:\TJKJ-LIN\tianjun-ai-vision",
        "D:\tianjun-ai-vision",
        "D:\tianjun\tianjun-ai-vision",
        "D:\tianjunkeji\tianjun-ai-vision",
        (Join-Path $env:ProgramFiles "tianjun-ai-vision")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate "resources\backend") -PathType Container) {
            return $candidate
        }
    }
    return (Read-Host "Enter the full TianJun install directory")
}

function Resolve-InstallContext([string]$Requested) {
    $resolved = Resolve-InstallDirectory $Requested
    if (-not $resolved) { throw "Install directory is empty" }
    $root = (Resolve-Path -LiteralPath $resolved).Path.TrimEnd("\")
    $backend = Join-Path $root "resources\backend"
    if (-not (Test-Path -LiteralPath $backend -PathType Container)) {
        throw "resources\backend not found under: $root"
    }
    $exeCandidates = @(
        (Join-Path $root "tianjun-ai-vision.exe"),
        (Join-Path $root "TianJun AI Vision.exe")
    )
    $exe = $exeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $exe) { throw "Application EXE not found under: $root" }
    $distCandidates = @(
        (Join-Path $root "resources\app\dist"),
        (Join-Path $root "resources\app.asar.unpacked\dist")
    )
    $dist = $distCandidates | Where-Object { Test-Path -LiteralPath (Join-Path $_ "index.html") -PathType Leaf } | Select-Object -First 1
    if (-not $dist) { throw "Frontend dist not found under resources\app or app.asar.unpacked" }
    return [pscustomobject]@{ Root = $root; Backend = $backend; Exe = $exe; Dist = $dist }
}

function Assert-ProductVersion($Context) {
    $version = ((Get-Item -LiteralPath $Context.Exe).VersionInfo.ProductVersion | Out-String).Trim()
    if ($version -notmatch '^3\.53\.0(?:\.0)?$') {
        throw "ProductVersion mismatch. Expected 3.53.0, actual '$version'"
    }
    Write-Host "[OK] ProductVersion=$version"
}

function Assert-NoPythonShadow($Context) {
    foreach ($name in @("hotfix.pyd", "manual_pass_v3530a.pyd")) {
        $path = Join-Path $Context.Backend $name
        if (Test-Path -LiteralPath $path) { throw "Python source is shadowed by: $path" }
    }
}

function Assert-PackagePayload {
    if ((Get-Sha256 (Join-Path $PackageRoot "hotfix.py")) -ne $ExpectedPayloadHotfix) {
        throw "Package hotfix.py hash mismatch"
    }
    if ((Get-Sha256 (Join-Path $PackageRoot "manual_pass_v3530a.py")) -ne $ExpectedPayloadManual) {
        throw "Package manual_pass_v3530a.py hash mismatch"
    }
    Assert-ManifestExact (Join-Path $PackageRoot "dist") $PayloadDistManifest "package dist"
    Write-Host "[OK] Package payload hashes"
}

function Assert-InstalledB($Context) {
    if ((Get-Sha256 (Join-Path $Context.Backend "hotfix.py")) -ne $ExpectedPayloadHotfix) {
        throw "Installed hotfix.py is not v3.53.0b payload"
    }
    if ((Get-Sha256 (Join-Path $Context.Backend "manual_pass_v3530a.py")) -ne $ExpectedPayloadManual) {
        throw "Installed manual_pass_v3530a.py is not v3.53.0b payload"
    }
    Assert-ManifestExact $Context.Dist $PayloadDistManifest "installed v3.53.0b dist"
    Assert-NoPythonShadow $Context
}

function Test-InstalledB($Context) {
    try { Assert-InstalledB $Context; return $true } catch { return $false }
}

function Assert-OldABaseline($Context) {
    $hotfixHash = Get-Sha256 (Join-Path $Context.Backend "hotfix.py")
    if ($hotfixHash -notin @($ExpectedOldAHotfix, $ExpectedScannerR1Hotfix)) {
        throw "Installed hotfix.py is neither exact v3.53.0a nor recognized scanner E R1 overlay: $hotfixHash"
    }
    if ((Get-Sha256 (Join-Path $Context.Backend "manual_pass_v3530a.py")) -ne $ExpectedOldAManual) {
        throw "Installed manual_pass_v3530a.py is not exact v3.53.0a"
    }
    Assert-ManifestExact $Context.Dist $OldADistManifest "installed v3.53.0a dist"
    Assert-NoPythonShadow $Context
    $baseline = if ($hotfixHash -eq $ExpectedScannerR1Hotfix) { "v3.53.0a + scanner E R1" } else { "v3.53.0a" }
    Write-Host "[OK] Baseline=$baseline"
    return $baseline
}

function Assert-UnderInstall([string]$Path, $Context, [string]$Label) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $prefix = $Context.Root.TrimEnd("\") + "\"
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label is outside install directory: $full"
    }
}

function Stop-TianJunProcesses($Context) {
    $allowed = @("tianjun-ai-vision.exe", "TianJun AI Vision.exe", "electron.exe", "python.exe", "pythonw.exe")
    $rootPrefix = $Context.Root.TrimEnd("\") + "\"
    $stopped = 0
    foreach ($proc in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($proc.Name -notin $allowed) { continue }
        $inside = $false
        if ($proc.ExecutablePath) {
            $inside = $proc.ExecutablePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        }
        if (-not $inside -and $proc.CommandLine) {
            $inside = $proc.CommandLine.IndexOf($Context.Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        }
        if (-not $inside) { continue }
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        $stopped += 1
    }
    Write-Host "[OK] Stopped in-scope processes: $stopped"
}

function Restore-FromBackup($Context, [string]$BackupDir) {
    $backupBackend = Join-Path $BackupDir "backend"
    $backupDist = Join-Path $BackupDir "dist"
    Copy-Item -LiteralPath (Join-Path $backupBackend "hotfix.py") -Destination (Join-Path $Context.Backend "hotfix.py") -Force
    Copy-Item -LiteralPath (Join-Path $backupBackend "manual_pass_v3530a.py") -Destination (Join-Path $Context.Backend "manual_pass_v3530a.py") -Force
    Assert-UnderInstall $Context.Dist $Context "frontend dist"
    if (Test-Path -LiteralPath $Context.Dist) { Remove-Item -LiteralPath $Context.Dist -Recurse -Force }
    Copy-Item -LiteralPath $backupDist -Destination $Context.Dist -Recurse -Force
}

try {
    Assert-PackagePayload
    $context = Resolve-InstallContext $InstallDir
    Assert-ProductVersion $context
    Write-Host "InstallDir=$($context.Root)"
    Write-Host "DistDir=$($context.Dist)"

    if ($CheckOnly) {
        Assert-InstalledB $context
        Write-Host "[OK] $PatchVersion installed content is exact"
        exit 0
    }

    if (Test-InstalledB $context) {
        Write-Host "[OK] $PatchVersion is already installed; no files changed"
        exit 0
    }

    $baseline = Assert-OldABaseline $context
    $backupParent = Join-Path $context.Root "resources\hotfix_backups"
    $backupDir = Join-Path $backupParent "v3530b"
    Assert-UnderInstall $backupDir $context "backup directory"
    if (Test-Path -LiteralPath $backupDir) {
        throw "Backup directory already exists; refusing to overwrite: $backupDir"
    }

    Stop-TianJunProcesses $context
    New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
    $pending = Join-Path $backupParent ("v3530b.pending-" + [guid]::NewGuid().ToString("N"))
    Assert-UnderInstall $pending $context "pending backup"
    New-Item -ItemType Directory -Path (Join-Path $pending "backend") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $context.Backend "hotfix.py") -Destination (Join-Path $pending "backend\hotfix.py") -Force
    Copy-Item -LiteralPath (Join-Path $context.Backend "manual_pass_v3530a.py") -Destination (Join-Path $pending "backend\manual_pass_v3530a.py") -Force
    Copy-Item -LiteralPath $context.Dist -Destination (Join-Path $pending "dist") -Recurse -Force
    Copy-Item -LiteralPath $OldADistManifest -Destination (Join-Path $pending "old-a-dist.manifest") -Force
    Assert-ManifestExact (Join-Path $pending "dist") $OldADistManifest "backup dist"
    $state = [ordered]@{
        patch = $PatchVersion
        baseline = $baseline
        product_version = ((Get-Item -LiteralPath $context.Exe).VersionInfo.ProductVersion | Out-String).Trim()
        dist_relative = $context.Dist.Substring($context.Root.Length + 1)
        hotfix_sha256 = Get-Sha256 (Join-Path $pending "backend\hotfix.py")
        manual_sha256 = Get-Sha256 (Join-Path $pending "backend\manual_pass_v3530a.py")
        created_at = (Get-Date).ToString("o")
    }
    $state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $pending "state.json") -Encoding UTF8
    Move-Item -LiteralPath $pending -Destination $backupDir
    Write-Host "[OK] Backup created: $backupDir"

    try {
        Copy-Item -LiteralPath (Join-Path $PackageRoot "hotfix.py") -Destination (Join-Path $context.Backend "hotfix.py") -Force
        Copy-Item -LiteralPath (Join-Path $PackageRoot "manual_pass_v3530a.py") -Destination (Join-Path $context.Backend "manual_pass_v3530a.py") -Force
        Assert-UnderInstall $context.Dist $context "frontend dist"
        if (Test-Path -LiteralPath $context.Dist) { Remove-Item -LiteralPath $context.Dist -Recurse -Force }
        Copy-Item -LiteralPath (Join-Path $PackageRoot "dist") -Destination $context.Dist -Recurse -Force
        Assert-InstalledB $context
    } catch {
        $installError = $_
        Write-Warning "Install failed; restoring exact pre-install files"
        Restore-FromBackup $context $backupDir
        Assert-OldABaseline $context | Out-Null
        throw "Install failed and automatic rollback completed: $($installError.Exception.Message)"
    }

    Write-Host "[OK] $PatchVersion installed. No DB or configuration was changed."
    Write-Host "Start TianJun normally, then run check_patch.bat."
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}

# make_patch.ps1
# Robust SWW patch builder (handles Windows locks on VERSION.txt)

$ErrorActionPreference = "Stop"

function Write-Header($msg) {
    Write-Host ""
    Write-Host "==== $msg ====" -ForegroundColor Cyan
}

function Clear-FileAttributes($path) {
    if (Test-Path $path) {
        try { attrib -r -h -s $path 2>$null | Out-Null } catch {}
    }
}

function Write-TextFileAtomic {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Content,
        [Parameter(Mandatory=$true)][string]$FallbackPath
    )

    $dir = Split-Path $Path -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }

    $tmp = Join-Path $dir ("._tmp_" + [System.Guid]::NewGuid().ToString("N") + ".txt")

    # Always write temp first
    [System.IO.File]::WriteAllText($tmp, $Content, [System.Text.Encoding]::UTF8)

    # Try to replace target atomically-ish
    try {
        Clear-FileAttributes $Path
        Move-Item -Path $tmp -Destination $Path -Force
        return $true
    }
    catch {
        # If target is locked/denied, fall back to VERSION_<patch>.txt
        try {
            Move-Item -Path $tmp -Destination $FallbackPath -Force
            Write-Host "WARNING: Could not overwrite $Path (locked/denied). Wrote instead: $FallbackPath" -ForegroundColor Yellow
            return $false
        }
        catch {
            # If even fallback fails, keep temp and raise
            Write-Host "ERROR: Could not write VERSION file at all. Temp left at: $tmp" -ForegroundColor Red
            throw
        }
    }
}

$runRoot = Get-Location

Write-Header "SWW Patch Builder"

# --- Detect actual project root ---
# If main.py is in current dir: use it. Else if p44/main.py exists: use p44.
$projRoot = $runRoot
if (-not (Test-Path (Join-Path $projRoot "main.py")) -and (Test-Path (Join-Path $projRoot "p44\main.py"))) {
    $projRoot = Join-Path $projRoot "p44"
}

Write-Host "Project root detected: $projRoot" -ForegroundColor Green

# --- Patch ID ---
$patch = Read-Host "Enter Patch ID (example: P4_10_46 or P4_10_46_FeatureName)"
if ([string]::IsNullOrWhiteSpace($patch)) {
    throw "Patch ID cannot be empty."
}

$archiveName = "Sww_complete_$patch.zip"
$destZip = Join-Path $runRoot $archiveName

# --- Auto-detect latest base zip (search run dir + parent dir) ---
Write-Header "Detecting Base Archive"

$searchPaths = @($runRoot)
$parentDir = Split-Path $runRoot -Parent
if ($parentDir -and (Test-Path $parentDir)) { $searchPaths += $parentDir }

$allZips = @()
foreach ($path in $searchPaths) {
    $z = Get-ChildItem -Path $path -Filter "Sww_complete_*.zip" -File -ErrorAction SilentlyContinue
    if ($z) { $allZips += $z }
}

$baseZip = $allZips | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$base = ""

if ($null -ne $baseZip) {
    $base = $baseZip.Name
    Write-Host "Base detected: $base (from $($baseZip.Directory.Name))" -ForegroundColor Green
} else {
    Write-Host "No previous Sww_complete_*.zip found in run or parent directory." -ForegroundColor Yellow
}

# --- Generate project_tree.txt ---
Write-Header "Generating project_tree.txt"

Push-Location $projRoot
try {
    cmd /c "tree /F /A > project_tree.txt" | Out-Null
}
catch {
    Get-ChildItem -Recurse -Force |
        Select-Object FullName, Length, LastWriteTime |
        Out-File -Encoding UTF8 project_tree.txt
}
Pop-Location

Write-Host "Wrote: project_tree.txt"

# --- Check MANIFEST.md ---
Write-Header "Checking MANIFEST.md"

if (-not (Test-Path (Join-Path $projRoot "MANIFEST.md"))) {
    Write-Host "WARNING: MANIFEST.md not found in project root." -ForegroundColor Yellow
} else {
    Write-Host "Found: MANIFEST.md"
}

# --- Create VERSION.txt content ---
Write-Header "Writing VERSION.txt"

# Patch-only version file: never touch VERSION.txt (avoids Windows locks)
$versionPath = Join-Path $projRoot ("VERSION_" + $patch + ".txt")

# If it already exists, clear attributes just in case
Clear-FileAttributes $versionPath

# Write atomically (temp -> move)
$tmp = Join-Path $projRoot ("._tmp_VERSION_" + [System.Guid]::NewGuid().ToString("N") + ".txt")
[System.IO.File]::WriteAllText($tmp, $content, [System.Text.Encoding]::UTF8)
Move-Item -Path $tmp -Destination $versionPath -Force

Write-Host "Wrote: $versionPath"
Write-Host "Reminder: Fill Summary + file lists before release." -ForegroundColor Yellow

# --- Build Zip from project root contents ---
Write-Header "Building Archive"

if (Test-Path $destZip) { Remove-Item $destZip -Force }

$excludeNames = @(".pytest_cache", "__pycache__")

$items = Get-ChildItem -Path $projRoot -Force | Where-Object {
    $excludeNames -notcontains $_.Name
}

$paths = @()
foreach ($it in $items) { $paths += $it.FullName }

Compress-Archive -Path $paths -DestinationPath $destZip -Force

Write-Host ""
Write-Host "Created: $archiveName" -ForegroundColor Green
Write-Host "Done."
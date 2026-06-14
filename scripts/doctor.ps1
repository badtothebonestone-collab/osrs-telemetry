Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Find-RepoRoot {
    param([string]$StartPath)

    $current = (Resolve-Path -LiteralPath $StartPath).Path
    if (-not (Get-Item -LiteralPath $current).PSIsContainer) {
        $current = Split-Path -Parent $current
    }

    while ($true) {
        if (Test-Path -LiteralPath (Join-Path $current ".git")) {
            return $current
        }

        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            throw "Could not find a repo root containing .git from $StartPath"
        }
        $current = $parent
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Find-RepoRoot -StartPath $scriptDir
$currentLocation = (Resolve-Path -LiteralPath (Get-Location)).Path

if ($currentLocation -ne $repoRoot) {
    Set-Location -LiteralPath $repoRoot
    Write-Host "Changed directory to repo root: $repoRoot"
}

$failed = $false
$blessedCommand = "powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1"

Write-Section "Repository"
Write-Host "Repo root: $repoRoot"

try {
    $branch = (& git branch --show-current)
    if ($LASTEXITCODE -ne 0) {
        throw "git branch --show-current failed"
    }
    Write-Host "Current branch: $branch"
}
catch {
    Write-Host "ERROR: git is unavailable or this folder is not a usable git worktree."
    Write-Host "Detail: $($_.Exception.Message)"
    $failed = $true
}

Write-Host "Git status --short:"
try {
    $status = @(& git status --short)
    if ($LASTEXITCODE -ne 0) {
        throw "git status --short failed"
    }
    if ($status.Count -eq 0) {
        Write-Host "(clean)"
    }
    else {
        $status | ForEach-Object { Write-Host $_ }
    }
}
catch {
    Write-Host "ERROR: Could not read git status."
    Write-Host "Detail: $($_.Exception.Message)"
    $failed = $true
}

Write-Section "Blessed command"
Write-Host $blessedCommand
$projectStatePath = Join-Path $repoRoot "PROJECT_STATE.md"
if (-not (Test-Path -LiteralPath $projectStatePath)) {
    Write-Host "ERROR: Missing PROJECT_STATE.md"
    $failed = $true
}
else {
    $projectState = Get-Content -LiteralPath $projectStatePath -Raw
    if ($projectState.Contains($blessedCommand)) {
        Write-Host "PROJECT_STATE.md contains the blessed command."
    }
    else {
        Write-Host "ERROR: PROJECT_STATE.md does not list the blessed command exactly."
        Write-Host "Expected: $blessedCommand"
        $failed = $true
    }
}

Write-Section "Expected files and folders"
$expectedPaths = @(
    "AGENTS.md",
    "PROJECT_STATE.md",
    "MILESTONES.md",
    "RECOVERY_LOG.md",
    "scripts\doctor.ps1",
    "scripts\run_current_milestone.ps1",
    "telemetry-viewer",
    "src\main\java",
    "src\test\java",
    "build.gradle",
    "settings.gradle"
)

foreach ($path in $expectedPaths) {
    if (Test-Path -LiteralPath (Join-Path $repoRoot $path)) {
        Write-Host "OK: $path"
    }
    else {
        Write-Host "ERROR: Missing expected path: $path"
        $failed = $true
    }
}

Write-Section "Python"
$pythonFiles = @(Get-ChildItem -LiteralPath $repoRoot -Recurse -File -Filter "*.py" -ErrorAction SilentlyContinue | Where-Object {
    $_.FullName -notmatch "\\\.git\\" -and
    $_.FullName -notmatch "\\\.gradle\\" -and
    $_.FullName -notmatch "\\build\\" -and
    $_.FullName -notmatch "\\__pycache__\\"
})

if ($pythonFiles.Count -eq 0) {
    Write-Host "No Python files found."
}
else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        Write-Host "ERROR: Python files exist, but 'python' was not found on PATH."
        Write-Host "Missing command: python"
        $failed = $true
    }
    else {
        $pythonVersion = (& $python.Source --version 2>&1)
        Write-Host "Python: $pythonVersion"
        Write-Host "Python files found: $($pythonFiles.Count)"
    }
}

Write-Section "Java and Gradle"
$usesGradle = (Test-Path -LiteralPath (Join-Path $repoRoot "build.gradle")) -or
    (Test-Path -LiteralPath (Join-Path $repoRoot "gradlew.bat")) -or
    (Test-Path -LiteralPath (Join-Path $repoRoot "gradlew"))

if (-not $usesGradle) {
    Write-Host "No Gradle build files found."
}
else {
    $java = Get-Command java -ErrorAction SilentlyContinue
    if ($null -eq $java) {
        Write-Host "ERROR: Gradle files exist, but 'java' was not found on PATH."
        Write-Host "Missing command: java"
        $failed = $true
    }
    else {
        $javaVersion = (& $java.Source --version | Select-Object -First 1)
        Write-Host "Java: $javaVersion"
    }

    if (Test-Path -LiteralPath (Join-Path $repoRoot "gradlew.bat")) {
        Write-Host "Gradle wrapper: gradlew.bat"
    }
    elseif (Test-Path -LiteralPath (Join-Path $repoRoot "gradlew")) {
        Write-Host "Gradle wrapper: gradlew"
    }
    else {
        Write-Host "ERROR: Gradle build exists, but no Gradle wrapper was found."
        Write-Host "Missing file: gradlew.bat or gradlew"
        $failed = $true
    }
}

if ($failed) {
    Write-Host ""
    Write-Host "Doctor result: FAIL"
    exit 1
}

Write-Host ""
Write-Host "Doctor result: PASS"
exit 0

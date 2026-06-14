Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:CurrentMilestone = "R1"

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Fail-Clearly {
    param([string]$Message)
    Write-Host ""
    Write-Host "$script:CurrentMilestone result: FAIL"
    Write-Host "Reason: $Message"
    exit 1
}

function Invoke-Native {
    param([scriptblock]$Command)

    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Command 2>&1
        $exitCode = $LASTEXITCODE
        foreach ($line in $output) {
            Write-Host $line
        }
        if ($null -eq $exitCode) {
            return 0
        }
        return [int]$exitCode
    }
    finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
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
            Fail-Clearly "Could not find a repo root containing .git from $StartPath"
        }
        $current = $parent
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Find-RepoRoot -StartPath $scriptDir
Set-Location -LiteralPath $repoRoot

$blessedCommand = "powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1"
$projectStatePath = Join-Path $repoRoot "PROJECT_STATE.md"
$milestonesPath = Join-Path $repoRoot "MILESTONES.md"

if (Test-Path -LiteralPath $milestonesPath) {
    $milestonesText = Get-Content -LiteralPath $milestonesPath -Raw
    if ($milestonesText -match "Active milestone:\s*R2\.5\b") {
        $script:CurrentMilestone = "R2.5"
    }
    elseif ($milestonesText -match "Active milestone:\s*R3\b") {
        $script:CurrentMilestone = "R3"
    }
    elseif ($milestonesText -match "Active milestone:\s*R2\b") {
        $script:CurrentMilestone = "R2"
    }
}

Write-Section "$script:CurrentMilestone recovery checks"
Write-Host "Repo root: $repoRoot"
Write-Host "Blessed command: $blessedCommand"
Write-Host "Active milestone: $script:CurrentMilestone"

if (-not (Test-Path -LiteralPath $projectStatePath)) {
    Fail-Clearly "PROJECT_STATE.md is missing; expected it at $projectStatePath"
}

$projectState = Get-Content -LiteralPath $projectStatePath -Raw
if (-not $projectState.Contains($blessedCommand)) {
    Fail-Clearly "Blessed recovery command is not listed exactly in PROJECT_STATE.md: $blessedCommand"
}

Write-Section "Doctor"
& (Join-Path $scriptDir "doctor.ps1")
if ($LASTEXITCODE -ne 0) {
    Fail-Clearly "scripts/doctor.ps1 failed. Read the Doctor section above for the missing command or file."
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    Fail-Clearly "Python is required for this repo's R1 checks, but 'python' was not found on PATH."
}

$oldDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = "1"

try {
    Write-Section "Python syntax compile"
    $compileScript = @'
import sys
import tokenize
from pathlib import Path

root = Path.cwd()
skip_parts = {
    ".git",
    ".gradle",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "_recovery",
    "build",
    "venv",
}

files = [
    path for path in root.rglob("*.py")
    if not any(part in skip_parts for part in path.parts)
]
files.sort(key=lambda path: path.as_posix())

errors = []
for path in files:
    try:
        with tokenize.open(path) as handle:
            source = handle.read()
        compile(source, str(path), "exec")
    except Exception as exc:
        errors.append((path, exc))

print(f"Python files checked: {len(files)}")
if errors:
    print("Python compile errors:")
    for path, exc in errors:
        print(f"- {path}: {exc}")
    sys.exit(1)
'@

    $compileExit = Invoke-Native { $compileScript | & $python.Source - }
    if ($compileExit -ne 0) {
        Fail-Clearly "Python syntax compilation failed."
    }

    Write-Section "$script:CurrentMilestone unittest subset"
    $unittestScripts = @(
        "telemetry-viewer\tests\test_state_baseline.py"
    )
    if (@("R2", "R2.5", "R3") -contains $script:CurrentMilestone) {
        $unittestScripts += @(
            "telemetry-viewer\tests\test_compact_context_boundary.py",
            "telemetry-viewer\tests\test_recovery_response_verifier.py"
        )
    }
    if (@("R2.5", "R3") -contains $script:CurrentMilestone) {
        $unittestScripts += @(
            "telemetry-viewer\tests\test_recovery_diagnostics.py"
        )
    }

    $existingUnittestScripts = @($unittestScripts | Where-Object { Test-Path -LiteralPath (Join-Path $repoRoot $_) })
    if ($existingUnittestScripts.Count -ne $unittestScripts.Count) {
        $missingTests = @($unittestScripts | Where-Object { -not (Test-Path -LiteralPath (Join-Path $repoRoot $_)) })
        Fail-Clearly "Missing current milestone test script(s): $($missingTests -join ', ')"
    }

    Write-Host "Tests:"
    $unittestRunner = "import runpy, sys; path = sys.argv[1]; sys.argv = [path]; sys.stderr = sys.stdout; runpy.run_path(path, run_name='__main__')"
    foreach ($testScript in $existingUnittestScripts) {
        Write-Host "- $testScript"
        $testExit = Invoke-Native { & $python.Source -c $unittestRunner $testScript }
        if ($testExit -ne 0) {
            Fail-Clearly "The current milestone unittest subset failed: $testScript"
        }
    }

    Write-Section "Optional latest-session diagnostic"
    Write-Host "Advisory only. The pass gate is the deterministic unittest and fixture suite above."
    $contextService = "telemetry-viewer\context_service.py"
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $contextService))) {
        Fail-Clearly "Missing expected read-only state parser: $contextService"
    }

    Write-Host "Latest-session R1 diagnostic:"
    $parserExit = Invoke-Native { & $python.Source $contextService --latest-session --state-baseline }
    if ($parserExit -ne 0) {
        Write-Host "Advisory latest-session state parser command exited nonzero: $parserExit"
    }

    if (@("R2", "R2.5", "R3") -contains $script:CurrentMilestone) {
        Write-Host "Latest-session R2 diagnostic:"
        $compactExit = Invoke-Native { & $python.Source $contextService --latest-session --compact-context }
        if ($compactExit -ne 0) {
            Write-Host "Advisory latest-session compact context command exited nonzero: $compactExit"
        }
    }
}
finally {
    $env:PYTHONDONTWRITEBYTECODE = $oldDontWriteBytecode
}

Write-Host ""
Write-Host "$script:CurrentMilestone result: PASS"
exit 0

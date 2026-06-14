param(
    [int]$WaitSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedRepoRoot = "C:\Users\badto\OneDrive\Documents\osrs-telemetry-recovery"
$ForbiddenRepoRoot = "C:\Users\badto\osrs-telemetry"
$BaselineCommandText = "powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1"
$LaunchCommandText = ".\gradlew.bat run --console=plain --no-daemon"
$ResultSchema = "baseline_launch_smoke.v1"

function Get-NormalizedPath {
    param([string]$Path)
    return (Resolve-Path -LiteralPath $Path).Path
}

function Write-TextFile {
    param(
        [string]$Path,
        [string[]]$Lines
    )
    $Lines | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Save-Json {
    param(
        [string]$Path,
        [object]$Payload
    )
    $Payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function New-ResultPayload {
    param(
        [string]$Status,
        [string]$Reason,
        [hashtable]$Extra
    )

    $payload = [ordered]@{
        schema = $ResultSchema
        status = $Status
        reason = $Reason
        generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        repoRoot = $script:RepoRoot
        branch = $script:Branch
        proofDir = $script:ProofDir
        baselineCommand = $BaselineCommandText
        launchCommand = $LaunchCommandText
        launchMode = "gradle_run_dev_client"
        waitSeconds = $WaitSeconds
    }
    foreach ($key in $Extra.Keys) {
        $payload[$key] = $Extra[$key]
    }
    return $payload
}

function Complete-Smoke {
    param(
        [string]$Status,
        [string]$Reason,
        [hashtable]$Extra = @{}
    )

    $payload = New-ResultPayload -Status $Status -Reason $Reason -Extra $Extra
    Save-Json -Path (Join-Path $script:ProofDir "launch_result.json") -Payload $payload
    Write-Host "Launch smoke result: $Status"
    Write-Host "Reason: $Reason"
    Write-Host "Proof folder: $script:ProofDir"
    if ($Status -ne "PASS") {
        exit 1
    }
    exit 0
}

function Invoke-BaselineGate {
    param(
        [string]$Label,
        [string]$OutputPath
    )

    Add-Content -LiteralPath $OutputPath -Encoding UTF8 -Value ""
    Add-Content -LiteralPath $OutputPath -Encoding UTF8 -Value "== $Label =="
    $output = & powershell -ExecutionPolicy Bypass -File (Join-Path $script:RepoRoot "scripts\run_current_milestone.ps1") 2>&1
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    foreach ($line in $output) {
        Add-Content -LiteralPath $OutputPath -Encoding UTF8 -Value $line
    }
    Add-Content -LiteralPath $OutputPath -Encoding UTF8 -Value "Exit code: $exitCode"
    return $exitCode
}

function Read-LogText {
    param([string[]]$Paths)
    $chunks = @()
    foreach ($path in $Paths) {
        if (Test-Path -LiteralPath $path) {
            $chunks += Get-Content -LiteralPath $path -Raw
        }
    }
    return ($chunks -join "`n")
}

function Get-FatalLogMatches {
    param([string]$LogText)

    $patterns = @(
        "Exception in thread",
        "BUILD FAILED",
        "Could not create the Java Virtual Machine",
        "ClassNotFoundException",
        "NoClassDefFoundError",
        "failed to boot",
        "port already in use",
        "Address already in use"
    )
    $matches = @()
    foreach ($pattern in $patterns) {
        if ($LogText -match [regex]::Escape($pattern)) {
            $matches += $pattern
        }
    }
    return $matches
}

function Test-PluginSnapshotHealth {
    $healthUrl = "http://127.0.0.1:8893/health"
    try {
        $health = Invoke-RestMethod -Method Get -Uri $healthUrl -TimeoutSec 2
        return [ordered]@{
            status = "PASS_PLUGIN_SNAPSHOT_HEALTH"
            url = $healthUrl
            available = $true
            schema = $health.schema
            endpointStatus = $health.status
            latestTick = $health.latestTick
            warnings = @($health.warnings)
        }
    }
    catch {
        return [ordered]@{
            status = "WARN_TELEMETRY_NOT_READY"
            url = $healthUrl
            available = $false
            reason = $_.Exception.Message
        }
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepoRoot = Get-NormalizedPath (Join-Path $scriptDir "..")
$expected = Get-NormalizedPath $ExpectedRepoRoot
$forbidden = Get-NormalizedPath $ForbiddenRepoRoot

if ($script:RepoRoot -ne $expected) {
    Write-Host "Expected repo root: $expected"
    Write-Host "Actual repo root: $script:RepoRoot"
    exit 1
}
if ($script:RepoRoot -eq $forbidden) {
    Write-Host "Refusing to run from quarantined old checkout: $forbidden"
    exit 1
}

Set-Location -LiteralPath $script:RepoRoot
$gitRootRaw = (& git rev-parse --show-toplevel 2>&1)
if ($LASTEXITCODE -ne 0) {
    Write-Host $gitRootRaw
    exit 1
}
$gitRoot = Get-NormalizedPath ([string]$gitRootRaw)
if ($gitRoot -ne $script:RepoRoot) {
    Write-Host "Git root mismatch: $gitRoot"
    exit 1
}

$script:Branch = (& git branch --show-current).Trim()
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$script:ProofDir = Join-Path $script:RepoRoot "_run_proofs\baseline_launch\$timestamp"
New-Item -ItemType Directory -Force -Path $script:ProofDir | Out-Null

$baselineResultPath = Join-Path $script:ProofDir "baseline_result.txt"
$stdoutPath = Join-Path $script:ProofDir "stdout.log"
$stderrPath = Join-Path $script:ProofDir "stderr.log"
$processInfoPath = Join-Path $script:ProofDir "process_info.txt"
$commandPath = Join-Path $script:ProofDir "command_used.txt"
$launchResultPath = Join-Path $script:ProofDir "launch_result.json"

Write-TextFile -Path $commandPath -Lines @(
    "Repo root: $script:RepoRoot",
    "Branch: $script:Branch",
    "Baseline gate: $BaselineCommandText",
    "Launch: $LaunchCommandText",
    "Read-only telemetry probe: GET http://127.0.0.1:8893/health",
    "No login automation, gameplay automation, route execution, or direct client control is performed."
)

$allowedBranches = @(
    "work/baseline-launch-smoke",
    "work/telemetry-payload-handshake",
    "work/manual-live-payload-capture",
    "work/live-payload-unblocker"
)
if ($allowedBranches -notcontains $script:Branch) {
    Complete-Smoke -Status "FAIL" -Reason "wrong_branch" -Extra @{ expectedBranches = $allowedBranches; actualBranch = $script:Branch }
}

$python = Get-Command python -ErrorAction SilentlyContinue
$java = Get-Command java -ErrorAction SilentlyContinue
$gradlew = Join-Path $script:RepoRoot "gradlew.bat"
$toolInfo = [ordered]@{
    python = if ($python) { $python.Source } else { $null }
    java = if ($java) { $java.Source } else { $null }
    gradlewBat = if (Test-Path -LiteralPath $gradlew) { $gradlew } else { $null }
}

if ($null -eq $python) {
    Complete-Smoke -Status "FAIL" -Reason "python_not_found" -Extra @{ tools = $toolInfo }
}
if ($null -eq $java) {
    Complete-Smoke -Status "FAIL" -Reason "java_not_found" -Extra @{ tools = $toolInfo }
}
if (-not (Test-Path -LiteralPath $gradlew)) {
    Complete-Smoke -Status "FAIL" -Reason "gradlew_bat_not_found" -Extra @{ tools = $toolInfo }
}

$preBaselineExit = Invoke-BaselineGate -Label "Pre-launch deterministic baseline" -OutputPath $baselineResultPath
if ($preBaselineExit -ne 0) {
    Complete-Smoke -Status "FAIL" -Reason "pre_launch_baseline_failed" -Extra @{ tools = $toolInfo; baselineExitCode = $preBaselineExit }
}

$existingTelemetry = Test-PluginSnapshotHealth
if ($existingTelemetry.available -eq $true) {
    Write-TextFile -Path $processInfoPath -Lines @(
        "Repo root: $script:RepoRoot",
        "Branch: $script:Branch",
        "Python: $($toolInfo.python)",
        "Java: $($toolInfo.java)",
        "Gradle wrapper: $($toolInfo.gradlewBat)",
        "Launch command: $LaunchCommandText",
        "Proof folder: $script:ProofDir",
        "Existing plugin snapshot endpoint detected before launching a duplicate process.",
        "Endpoint URL: $($existingTelemetry.url)"
    )
    Write-TextFile -Path $stdoutPath -Lines @("Existing endpoint detected; no duplicate Gradle run process was started.")
    Write-TextFile -Path $stderrPath -Lines @("")
    $postBaselineExit = Invoke-BaselineGate -Label "Post-launch deterministic baseline" -OutputPath $baselineResultPath
    if ($postBaselineExit -ne 0) {
        Complete-Smoke -Status "FAIL" -Reason "post_launch_baseline_failed" -Extra @{
            tools = $toolInfo
            telemetry = $existingTelemetry
            preLaunchBaselineExitCode = $preBaselineExit
            postLaunchBaselineExitCode = $postBaselineExit
            logs = [ordered]@{
                stdout = $stdoutPath
                stderr = $stderrPath
                baseline = $baselineResultPath
                processInfo = $processInfoPath
                commandUsed = $commandPath
                result = $launchResultPath
            }
        }
    }
    Complete-Smoke -Status "PASS" -Reason "existing_endpoint_alive_and_baseline_passed" -Extra @{
        tools = $toolInfo
        telemetry = $existingTelemetry
        launchAlreadyRunning = $true
        preLaunchBaselineExitCode = $preBaselineExit
        postLaunchBaselineExitCode = $postBaselineExit
        logs = [ordered]@{
            stdout = $stdoutPath
            stderr = $stderrPath
            baseline = $baselineResultPath
            processInfo = $processInfoPath
            commandUsed = $commandPath
            result = $launchResultPath
        }
    }
}

$tasksOutput = & $gradlew tasks --all --console=plain --no-daemon 2>&1
$tasksExit = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
$runTaskFound = ($tasksOutput -join "`n") -match "(?m)^run(\s|$)"
if ($tasksExit -ne 0 -or -not $runTaskFound) {
    Write-TextFile -Path $processInfoPath -Lines @(
        "Gradle task discovery failed or did not list run.",
        "Exit code: $tasksExit",
        "Output:",
        ($tasksOutput -join "`n")
    )
    Complete-Smoke -Status "FAIL" -Reason "no_safe_launch_command_found" -Extra @{ tools = $toolInfo; gradleTasksExitCode = $tasksExit; runTaskFound = $runTaskFound }
}

Write-TextFile -Path $processInfoPath -Lines @(
    "Repo root: $script:RepoRoot",
    "Branch: $script:Branch",
    "Python: $($toolInfo.python)",
    "Java: $($toolInfo.java)",
    "Gradle wrapper: $($toolInfo.gradlewBat)",
    "Gradle run task found: $runTaskFound",
    "Launch command: $LaunchCommandText",
    "Proof folder: $script:ProofDir"
)

$process = Start-Process `
    -FilePath $gradlew `
    -ArgumentList @("run", "--console=plain", "--no-daemon") `
    -WorkingDirectory $script:RepoRoot `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

Add-Content -LiteralPath $processInfoPath -Encoding UTF8 -Value "Started process id: $($process.Id)"
Add-Content -LiteralPath $processInfoPath -Encoding UTF8 -Value "Started process name: $($process.ProcessName)"
Add-Content -LiteralPath $processInfoPath -Encoding UTF8 -Value "Started at UTC: $((Get-Date).ToUniversalTime().ToString("o"))"

Start-Sleep -Seconds $WaitSeconds
$process.Refresh()
$alive = -not $process.HasExited
$exitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
$telemetry = Test-PluginSnapshotHealth
$postBaselineExit = Invoke-BaselineGate -Label "Post-launch deterministic baseline" -OutputPath $baselineResultPath
$logText = Read-LogText -Paths @($stdoutPath, $stderrPath)
$fatalMatches = @(Get-FatalLogMatches -LogText $logText)

Add-Content -LiteralPath $processInfoPath -Encoding UTF8 -Value "Alive after wait: $alive"
Add-Content -LiteralPath $processInfoPath -Encoding UTF8 -Value "Exit code after wait: $exitCode"
Add-Content -LiteralPath $processInfoPath -Encoding UTF8 -Value "Post-launch baseline exit code: $postBaselineExit"
Add-Content -LiteralPath $processInfoPath -Encoding UTF8 -Value "Telemetry status: $($telemetry.status)"

$processPayload = [ordered]@{
    id = $process.Id
    name = $process.ProcessName
    aliveAfterWait = $alive
    exitCodeAfterWait = $exitCode
}
$logPayload = [ordered]@{
    stdout = $stdoutPath
    stderr = $stderrPath
    baseline = $baselineResultPath
    processInfo = $processInfoPath
    commandUsed = $commandPath
    result = $launchResultPath
}

if (-not $alive) {
    Complete-Smoke -Status "FAIL" -Reason "launch_process_exited_before_wait_completed" -Extra @{
        tools = $toolInfo
        process = $processPayload
        telemetry = $telemetry
        postLaunchBaselineExitCode = $postBaselineExit
        fatalLogMatches = $fatalMatches
        logs = $logPayload
    }
}
if ($fatalMatches.Count -gt 0) {
    Complete-Smoke -Status "FAIL" -Reason "fatal_startup_log_match" -Extra @{
        tools = $toolInfo
        process = $processPayload
        telemetry = $telemetry
        postLaunchBaselineExitCode = $postBaselineExit
        fatalLogMatches = $fatalMatches
        logs = $logPayload
    }
}
if ($postBaselineExit -ne 0) {
    Complete-Smoke -Status "FAIL" -Reason "post_launch_baseline_failed" -Extra @{
        tools = $toolInfo
        process = $processPayload
        telemetry = $telemetry
        postLaunchBaselineExitCode = $postBaselineExit
        fatalLogMatches = $fatalMatches
        logs = $logPayload
    }
}

Complete-Smoke -Status "PASS" -Reason "launch_process_alive_and_baseline_passed" -Extra @{
    tools = $toolInfo
    process = $processPayload
    telemetry = $telemetry
    preLaunchBaselineExitCode = $preBaselineExit
    postLaunchBaselineExitCode = $postBaselineExit
    fatalLogMatches = $fatalMatches
    logs = $logPayload
}

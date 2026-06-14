param(
    [int]$WaitSeconds = 240,
    [int]$PollIntervalSeconds = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedRepoRoot = "C:\Users\badto\OneDrive\Documents\osrs-telemetry-recovery"
$ForbiddenRepoRoot = "C:\Users\badto\osrs-telemetry"
$BaselineText = "powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1"
$CaptureText = "powershell -ExecutionPolicy Bypass -File scripts/capture_live_payload.ps1"
$ResultSchema = "manual_live_payload_capture.v1"
$EndpointHost = "127.0.0.1"
$EndpointPort = 8893
$HealthPath = "/health"
$SchemaPath = "/schema"
$SnapshotPath = "/snapshot"
$EndpointBase = "http://${EndpointHost}:${EndpointPort}"
$CanonicalPayloadNames = @("baseline", "inventory", "activity", "writer_health", "scene_delta", "projection", "navigation", "collision_window", "world_model_summary", "scene_object_census", "resource_object_census")

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
    $Payload | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-JsonValue {
    param(
        [object]$Object,
        [string]$Name,
        [object]$Default = $null
    )

    if ($null -eq $Object) {
        return $Default
    }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) {
            return $Object[$Name]
        }
        return $Default
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $Default
    }
    return $property.Value
}

function Get-PropertyNames {
    param([object]$Object)

    if ($null -eq $Object) {
        return @()
    }
    if ($Object -is [System.Collections.IDictionary]) {
        return @($Object.Keys)
    }
    $names = @()
    foreach ($property in $Object.PSObject.Properties) {
        $names += $property.Name
    }
    return $names
}

function ConvertTo-LongOrDefault {
    param(
        [object]$Value,
        [long]$Default = -1
    )

    if ($null -eq $Value) {
        return $Default
    }
    try {
        return [long]$Value
    }
    catch {
        return $Default
    }
}

function Invoke-BaselineGate {
    param([string]$OutputPath)

    Add-Content -LiteralPath $OutputPath -Encoding UTF8 -Value "== Deterministic baseline gate =="
    $output = & powershell -ExecutionPolicy Bypass -File (Join-Path $script:RepoRoot "scripts\run_current_milestone.ps1") 2>&1
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    foreach ($line in $output) {
        Add-Content -LiteralPath $OutputPath -Encoding UTF8 -Value $line
    }
    Add-Content -LiteralPath $OutputPath -Encoding UTF8 -Value "Exit code: $exitCode"
    return $exitCode
}

function Test-EndpointListening {
    try {
        $connections = @(Get-NetTCPConnection -LocalAddress $EndpointHost -LocalPort $EndpointPort -State Listen -ErrorAction Stop)
        return $connections.Count -gt 0
    }
    catch {
        return $false
    }
}

function Read-ErrorResponse {
    param([object]$ErrorRecord)

    $statusCode = $null
    $raw = ""
    $response = $ErrorRecord.Exception.Response
    if ($null -ne $response) {
        try {
            if ($response.StatusCode) {
                $statusCode = [int]$response.StatusCode
            }
        }
        catch {
            $statusCode = $null
        }
        try {
            $stream = $response.GetResponseStream()
            if ($null -ne $stream) {
                $reader = New-Object System.IO.StreamReader($stream)
                try {
                    $raw = $reader.ReadToEnd()
                }
                finally {
                    $reader.Dispose()
                }
            }
        }
        catch {
            $raw = ""
        }
    }

    return [ordered]@{
        statusCode = $statusCode
        raw = $raw
    }
}

function Invoke-EndpointGet {
    param([string]$Uri)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $Uri -TimeoutSec 5
        return [ordered]@{
            ok = $true
            statusCode = [int]$response.StatusCode
            raw = [string]$response.Content
            errorMessage = $null
        }
    }
    catch {
        $errorResponse = Read-ErrorResponse -ErrorRecord $_
        return [ordered]@{
            ok = $false
            statusCode = Get-JsonValue -Object $errorResponse -Name "statusCode"
            raw = [string](Get-JsonValue -Object $errorResponse -Name "raw" -Default "")
            errorMessage = $_.Exception.Message
        }
    }
}

function Invoke-EndpointPostJson {
    param(
        [string]$Uri,
        [string]$Body
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $Uri -ContentType "application/json" -Body $Body -TimeoutSec 5
        return [ordered]@{
            ok = $true
            statusCode = [int]$response.StatusCode
            raw = [string]$response.Content
            errorMessage = $null
        }
    }
    catch {
        $errorResponse = Read-ErrorResponse -ErrorRecord $_
        return [ordered]@{
            ok = $false
            statusCode = Get-JsonValue -Object $errorResponse -Name "statusCode"
            raw = [string](Get-JsonValue -Object $errorResponse -Name "raw" -Default "")
            errorMessage = $_.Exception.Message
        }
    }
}

function ConvertFrom-JsonText {
    param([string]$Raw)

    if ([string]::IsNullOrWhiteSpace($Raw)) {
        return [ordered]@{
            ok = $false
            value = $null
            errorMessage = "empty JSON response"
        }
    }
    try {
        return [ordered]@{
            ok = $true
            value = ($Raw | ConvertFrom-Json -ErrorAction Stop)
            errorMessage = $null
        }
    }
    catch {
        return [ordered]@{
            ok = $false
            value = $null
            errorMessage = $_.Exception.Message
        }
    }
}

function Get-SnapshotSummary {
    param(
        [object]$Snapshot,
        [object]$SnapshotHttp,
        [object]$SnapshotParse
    )

    $payloads = Get-JsonValue -Object $Snapshot -Name "payloads"
    $payloadNames = @(Get-PropertyNames -Object $payloads)
    $freshness = Get-JsonValue -Object $Snapshot -Name "freshness"
    $latestTick = ConvertTo-LongOrDefault -Value (Get-JsonValue -Object $Snapshot -Name "latestTick" -Default (Get-JsonValue -Object $freshness -Name "latestTick" -Default -1))
    $hasCanonicalPayload = $false
    foreach ($name in $payloadNames) {
        if ($CanonicalPayloadNames -contains $name) {
            $hasCanonicalPayload = $true
            break
        }
    }
    return [ordered]@{
        parseOk = [bool](Get-JsonValue -Object $SnapshotParse -Name "ok" -Default $false)
        httpOk = [bool](Get-JsonValue -Object $SnapshotHttp -Name "ok" -Default $false)
        httpStatus = Get-JsonValue -Object $SnapshotHttp -Name "statusCode"
        schema = Get-JsonValue -Object $Snapshot -Name "schema"
        status = Get-JsonValue -Object $Snapshot -Name "status"
        latestTick = $latestTick
        fresh = [bool](Get-JsonValue -Object $freshness -Name "fresh" -Default $false)
        payloadNames = $payloadNames
        payloadCount = @($payloadNames).Count
        baselinePayloadPresent = $payloadNames -contains "baseline"
        canonicalPayloadPresent = $hasCanonicalPayload
        missingCapabilities = @(Get-JsonValue -Object $Snapshot -Name "missingCapabilities" -Default @())
        warningMessages = @(Get-JsonValue -Object $Snapshot -Name "warnings" -Default @())
        staleReasons = @(Get-JsonValue -Object $freshness -Name "staleReasons" -Default @())
        parseError = Get-JsonValue -Object $SnapshotParse -Name "errorMessage"
        httpError = Get-JsonValue -Object $SnapshotHttp -Name "errorMessage"
    }
}

function Write-PollLog {
    param([object]$Payload)

    $Payload | ConvertTo-Json -Depth 12 -Compress | Add-Content -LiteralPath $script:PollLogPath -Encoding UTF8
}

function Invoke-StackConsumption {
    param([string]$SnapshotPath)

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        return [ordered]@{
            ran = $false
            status = "WARN"
            reason = "python_not_found"
        }
    }

    $adapterPath = Join-Path $script:RepoRoot "telemetry-viewer\live_payload_adapter.py"
    $outputPath = Join-Path $script:ProofDir "stack_consumption.json"
    $output = & python $adapterPath --snapshot $SnapshotPath --out $outputPath 2>&1
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    if ($exitCode -ne 0) {
        return [ordered]@{
            ran = $true
            status = "FAIL"
            reason = "adapter_failed"
            exitCode = $exitCode
            output = @($output)
        }
    }
    try {
        $payload = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json -ErrorAction Stop
        return [ordered]@{
            ran = $true
            status = if ((Get-JsonValue -Object $payload -Name "consumedByRecoveredStack" -Default $false) -eq $true) { "PASS" } else { "WARN" }
            observationStatus = Get-JsonValue -Object (Get-JsonValue -Object $payload -Name "observationDiagnostic") -Name "status"
            consumedByRecoveredStack = Get-JsonValue -Object $payload -Name "consumedByRecoveredStack" -Default $false
            observationReady = Get-JsonValue -Object $payload -Name "observationReady" -Default $false
            path = $outputPath
        }
    }
    catch {
        return [ordered]@{
            ran = $true
            status = "FAIL"
            reason = "adapter_output_unreadable"
            errorMessage = $_.Exception.Message
        }
    }
}

function New-ResultPayload {
    param(
        [string]$Status,
        [string]$Reason,
        [int]$AttemptCount,
        [object]$Health,
        [object]$SnapshotSummary,
        [object]$StackConsumption
    )

    return [ordered]@{
        schema = $ResultSchema
        status = $Status
        reason = $Reason
        generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        repoRoot = $script:RepoRoot
        branch = $script:Branch
        proofDir = $script:ProofDir
        endpoint = [ordered]@{
            host = $EndpointHost
            port = $EndpointPort
            pathsChecked = @($HealthPath, $SchemaPath, $SnapshotPath)
            listening = Test-EndpointListening
        }
        baselineGate = [ordered]@{
            passed = $script:BaselineExitCode -eq 0
            exitCode = $script:BaselineExitCode
        }
        attempts = $AttemptCount
        waitSeconds = $WaitSeconds
        pollIntervalSeconds = $PollIntervalSeconds
        latestTick = if ($null -eq $SnapshotSummary) { Get-JsonValue -Object $Health -Name "latestTick" -Default -1 } else { Get-JsonValue -Object $SnapshotSummary -Name "latestTick" -Default -1 }
        cachedPacketTypes = @(Get-JsonValue -Object $Health -Name "cachedPacketTypes" -Default @())
        snapshot = $SnapshotSummary
        stackConsumption = $StackConsumption
        proofFiles = [ordered]@{
            invocationText = "command_used.txt"
            health = "health.json"
            schema = "schema.json"
            snapshotRawLatest = "snapshot_raw_latest.txt"
            snapshotPrettyLatest = "snapshot_pretty_latest.json"
            captureResult = "capture_result.json"
            pollLog = "poll_log.jsonl"
            stackConsumption = "stack_consumption.json"
        }
        readOnly = $true
        noClientControl = $true
    }
}

function Complete-Capture {
    param(
        [string]$Status,
        [string]$Reason,
        [int]$AttemptCount,
        [object]$Health = $null,
        [object]$SnapshotSummary = $null,
        [object]$StackConsumption = $null
    )

    $payload = New-ResultPayload -Status $Status -Reason $Reason -AttemptCount $AttemptCount -Health $Health -SnapshotSummary $SnapshotSummary -StackConsumption $StackConsumption
    Save-Json -Path (Join-Path $script:ProofDir "capture_result.json") -Payload $payload
    Write-Host "Manual live payload capture result: $Status"
    Write-Host "Reason: $Reason"
    Write-Host "Proof folder: $script:ProofDir"
    if ($Status -like "FAIL_*") {
        exit 1
    }
    exit 0
}

function Invoke-ReadOnlyProbe {
    param([int]$Attempt)

    $checkedAt = (Get-Date).ToUniversalTime().ToString("o")
    if (-not (Test-EndpointListening)) {
        Write-PollLog -Payload ([ordered]@{
            checkedAtUtc = $checkedAt
            attempt = $Attempt
            status = "FAIL_ENDPOINT_NOT_LISTENING"
            latestTick = -1
            payloadNames = @()
        })
        return [ordered]@{
            status = "FAIL_ENDPOINT_NOT_LISTENING"
            reason = "127.0.0.1:8893 is not listening"
            health = $null
            snapshotSummary = $null
            stackConsumption = $null
        }
    }

    $healthHttp = Invoke-EndpointGet -Uri "$EndpointBase$HealthPath"
    $healthParse = ConvertFrom-JsonText -Raw ([string](Get-JsonValue -Object $healthHttp -Name "raw" -Default ""))
    $health = Get-JsonValue -Object $healthParse -Name "value"
    if (-not [bool](Get-JsonValue -Object $healthParse -Name "ok" -Default $false)) {
        Write-PollLog -Payload ([ordered]@{
            checkedAtUtc = $checkedAt
            attempt = $Attempt
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "health endpoint response was not parseable JSON"
        })
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "health endpoint response was not parseable JSON"
            health = $null
            snapshotSummary = $null
            stackConsumption = $null
        }
    }
    if ((Get-JsonValue -Object $health -Name "schema") -ne "plugin_snapshot_health.v1") {
        Write-PollLog -Payload ([ordered]@{
            checkedAtUtc = $checkedAt
            attempt = $Attempt
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "health endpoint returned an unexpected schema"
        })
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "health endpoint returned an unexpected schema"
            health = $health
            snapshotSummary = $null
            stackConsumption = $null
        }
    }
    Save-Json -Path (Join-Path $script:ProofDir "health.json") -Payload $health

    $schemaHttp = Invoke-EndpointGet -Uri "$EndpointBase$SchemaPath"
    $schemaParse = ConvertFrom-JsonText -Raw ([string](Get-JsonValue -Object $schemaHttp -Name "raw" -Default ""))
    $schemaPayload = Get-JsonValue -Object $schemaParse -Name "value"
    if (-not [bool](Get-JsonValue -Object $schemaParse -Name "ok" -Default $false) -or (Get-JsonValue -Object $schemaPayload -Name "schema") -ne "plugin_snapshot_schema.v1") {
        Write-PollLog -Payload ([ordered]@{
            checkedAtUtc = $checkedAt
            attempt = $Attempt
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "schema endpoint did not return plugin_snapshot_schema.v1"
        })
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "schema endpoint did not return plugin_snapshot_schema.v1"
            health = $health
            snapshotSummary = $null
            stackConsumption = $null
        }
    }
    Save-Json -Path (Join-Path $script:ProofDir "schema.json") -Payload $schemaPayload

    $snapshotBody = '{"schema":"plugin_snapshot_request.v1","needs":["baseline","inventory","activity","scene_delta"],"snapshotTier":"hot","responseMode":"compact","includeCollisionWindow":false,"includeWatchValues":false}'
    $snapshotHttp = Invoke-EndpointPostJson -Uri "$EndpointBase$SnapshotPath" -Body $snapshotBody
    $snapshotRaw = [string](Get-JsonValue -Object $snapshotHttp -Name "raw" -Default "")
    Write-TextFile -Path (Join-Path $script:ProofDir "snapshot_raw_latest.txt") -Lines @($snapshotRaw)
    $snapshotParse = ConvertFrom-JsonText -Raw $snapshotRaw
    $snapshot = Get-JsonValue -Object $snapshotParse -Name "value"
    $snapshotSummary = Get-SnapshotSummary -Snapshot $snapshot -SnapshotHttp $snapshotHttp -SnapshotParse $snapshotParse

    if (-not [bool](Get-JsonValue -Object $snapshotParse -Name "ok" -Default $false)) {
        Write-PollLog -Payload ([ordered]@{
            checkedAtUtc = $checkedAt
            attempt = $Attempt
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "snapshot endpoint response was not parseable JSON"
        })
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "snapshot endpoint response was not parseable JSON"
            health = $health
            snapshotSummary = $snapshotSummary
            stackConsumption = $null
        }
    }
    Save-Json -Path (Join-Path $script:ProofDir "snapshot_pretty_latest.json") -Payload $snapshot

    if ((Get-JsonValue -Object $snapshot -Name "schema") -ne "plugin_snapshot_response.v1") {
        Write-PollLog -Payload ([ordered]@{
            checkedAtUtc = $checkedAt
            attempt = $Attempt
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "snapshot endpoint returned an unexpected schema"
        })
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "snapshot endpoint returned an unexpected schema"
            health = $health
            snapshotSummary = $snapshotSummary
            stackConsumption = $null
        }
    }

    $latestTick = ConvertTo-LongOrDefault -Value (Get-JsonValue -Object $snapshotSummary -Name "latestTick" -Default -1)
    $baselinePresent = [bool](Get-JsonValue -Object $snapshotSummary -Name "baselinePayloadPresent" -Default $false)
    $canonicalPresent = [bool](Get-JsonValue -Object $snapshotSummary -Name "canonicalPayloadPresent" -Default $false)
    $payloadReady = $latestTick -ge 0 -and ($baselinePresent -or $canonicalPresent)
    $stackConsumption = $null

    if ($payloadReady) {
        $stackConsumption = Invoke-StackConsumption -SnapshotPath (Join-Path $script:ProofDir "snapshot_pretty_latest.json")
        Write-PollLog -Payload ([ordered]@{
            checkedAtUtc = $checkedAt
            attempt = $Attempt
            status = "PASS_LIVE_PAYLOAD_CAPTURED"
            latestTick = $latestTick
            payloadNames = @(Get-JsonValue -Object $snapshotSummary -Name "payloadNames" -Default @())
            stackConsumption = $stackConsumption
        })
        return [ordered]@{
            status = "PASS_LIVE_PAYLOAD_CAPTURED"
            reason = "live telemetry payload captured from read-only endpoint"
            health = $health
            snapshotSummary = $snapshotSummary
            stackConsumption = $stackConsumption
        }
    }

    Write-PollLog -Payload ([ordered]@{
        checkedAtUtc = $checkedAt
        attempt = $Attempt
        status = "WARN_ENDPOINT_ALIVE_NO_PAYLOAD"
        latestTick = $latestTick
        cachedPacketTypes = @(Get-JsonValue -Object $health -Name "cachedPacketTypes" -Default @())
        payloadNames = @(Get-JsonValue -Object $snapshotSummary -Name "payloadNames" -Default @())
        warningMessages = @(Get-JsonValue -Object $snapshotSummary -Name "warningMessages" -Default @())
    })
    return [ordered]@{
        status = "WARN_ENDPOINT_ALIVE_NO_PAYLOAD"
        reason = "endpoint is alive, but a live telemetry payload is not available yet"
        health = $health
        snapshotSummary = $snapshotSummary
        stackConsumption = $stackConsumption
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
$script:ProofDir = Join-Path $script:RepoRoot "_run_proofs\live_payload\$timestamp"
New-Item -ItemType Directory -Force -Path $script:ProofDir | Out-Null
$script:PollLogPath = Join-Path $script:ProofDir "poll_log.jsonl"

Write-TextFile -Path (Join-Path $script:ProofDir "command_used.txt") -Lines @(
    "Repo root: $script:RepoRoot",
    "Branch: $script:Branch",
    "Baseline gate: $BaselineText",
    "Manual live payload capture: $CaptureText",
    "Endpoint: $EndpointBase",
    "Paths checked: GET $HealthPath, GET $SchemaPath, POST $SnapshotPath",
    "Snapshot needs: baseline, inventory, activity, scene_delta",
    "Read-only only. No login automation, clicks, keyboard or mouse events, route execution, banking/activity automation, gameplay automation, or direct client control is performed."
)

$baselinePath = Join-Path $script:ProofDir "baseline_result.txt"
$script:BaselineExitCode = Invoke-BaselineGate -OutputPath $baselinePath
if ($script:BaselineExitCode -ne 0) {
    Complete-Capture -Status "FAIL_BASELINE_GATE" -Reason "deterministic baseline gate failed before live payload capture" -AttemptCount 0
}

Write-Host "MANUAL STEP REQUIRED:"
Write-Host "Put the dev client into a live scene yourself."
Write-Host "This script will only observe the endpoint."
Write-Host "It will not login, click, type, move, route, bank, or control the client."

$attempt = 0
$deadline = (Get-Date).AddSeconds([Math]::Max(0, $WaitSeconds))
$lastProbe = $null
do {
    $attempt += 1
    $lastProbe = Invoke-ReadOnlyProbe -Attempt $attempt
    $status = [string](Get-JsonValue -Object $lastProbe -Name "status")
    Write-Host "Attempt $attempt result: $status"
    if ($status -eq "PASS_LIVE_PAYLOAD_CAPTURED" -or $status -like "FAIL_*") {
        break
    }
    if ($WaitSeconds -le 0 -or (Get-Date) -ge $deadline) {
        break
    }
    Start-Sleep -Seconds ([Math]::Max(1, $PollIntervalSeconds))
} while ($true)

Complete-Capture `
    -Status ([string](Get-JsonValue -Object $lastProbe -Name "status")) `
    -Reason ([string](Get-JsonValue -Object $lastProbe -Name "reason")) `
    -AttemptCount $attempt `
    -Health (Get-JsonValue -Object $lastProbe -Name "health") `
    -SnapshotSummary (Get-JsonValue -Object $lastProbe -Name "snapshotSummary") `
    -StackConsumption (Get-JsonValue -Object $lastProbe -Name "stackConsumption")

param(
    [int]$WaitSeconds = 0,
    [int]$PollIntervalSeconds = 2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedRepoRoot = "C:\Users\badto\OneDrive\Documents\osrs-telemetry-recovery"
$ForbiddenRepoRoot = "C:\Users\badto\osrs-telemetry"
$BaselineCommandText = "powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1"
$HandshakeText = "powershell -ExecutionPolicy Bypass -File scripts/check_telemetry_payload.ps1"
$ResultSchema = "telemetry_payload_handshake.v1"
$EndpointHost = "127.0.0.1"
$EndpointPort = 8893
$HealthPath = "/health"
$SchemaPath = "/schema"
$SnapshotPath = "/snapshot"
$EndpointBase = "http://${EndpointHost}:${EndpointPort}"

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
    $Payload | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $Path -Encoding UTF8
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

function New-StatusPayload {
    param(
        [bool]$Listening,
        [object]$Health,
        [object]$HealthHttp,
        [object]$SchemaHttp,
        [object]$SnapshotHttp
    )

    $cachedPacketTypes = @(Get-JsonValue -Object $Health -Name "cachedPacketTypes" -Default @())
    return [ordered]@{
        schema = "telemetry_endpoint_status.v1"
        checkedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        host = $EndpointHost
        port = $EndpointPort
        listening = $Listening
        pathsChecked = @($HealthPath, $SchemaPath, $SnapshotPath)
        healthHttpStatus = Get-JsonValue -Object $HealthHttp -Name "statusCode"
        schemaHttpStatus = Get-JsonValue -Object $SchemaHttp -Name "statusCode"
        snapshotHttpStatus = Get-JsonValue -Object $SnapshotHttp -Name "statusCode"
        healthSchema = Get-JsonValue -Object $Health -Name "schema"
        healthStatus = Get-JsonValue -Object $Health -Name "status"
        latestTick = Get-JsonValue -Object $Health -Name "latestTick" -Default -1
        latestSequence = Get-JsonValue -Object $Health -Name "latestSequence" -Default -1
        cachedPacketTypes = $cachedPacketTypes
        cacheWallClockFresh = Get-JsonValue -Object $Health -Name "cacheWallClockFresh" -Default $false
        staleReasons = @(Get-JsonValue -Object $Health -Name "staleReasons" -Default @())
        warningMessages = @(Get-JsonValue -Object $Health -Name "warnings" -Default @())
    }
}

function New-SnapshotSummary {
    param(
        [object]$Snapshot,
        [object]$SnapshotParse,
        [object]$SnapshotHttp
    )

    $payloads = Get-JsonValue -Object $Snapshot -Name "payloads"
    $payloadNames = @(Get-PropertyNames -Object $payloads)
    $freshness = Get-JsonValue -Object $Snapshot -Name "freshness"
    return [ordered]@{
        parseOk = [bool](Get-JsonValue -Object $SnapshotParse -Name "ok" -Default $false)
        httpOk = [bool](Get-JsonValue -Object $SnapshotHttp -Name "ok" -Default $false)
        httpStatus = Get-JsonValue -Object $SnapshotHttp -Name "statusCode"
        schema = Get-JsonValue -Object $Snapshot -Name "schema"
        status = Get-JsonValue -Object $Snapshot -Name "status"
        latestTick = Get-JsonValue -Object $Snapshot -Name "latestTick" -Default -1
        payloadNames = $payloadNames
        payloadCount = @($payloadNames).Count
        fresh = Get-JsonValue -Object $freshness -Name "fresh" -Default $false
        staleReasons = @(Get-JsonValue -Object $freshness -Name "staleReasons" -Default @())
        missingCapabilities = @(Get-JsonValue -Object $Snapshot -Name "missingCapabilities" -Default @())
        warningMessages = @(Get-JsonValue -Object $Snapshot -Name "warnings" -Default @())
        parseError = Get-JsonValue -Object $SnapshotParse -Name "errorMessage"
        httpError = Get-JsonValue -Object $SnapshotHttp -Name "errorMessage"
    }
}

function New-ResultPayload {
    param(
        [string]$Status,
        [string]$Reason,
        [int]$AttemptCount,
        [object]$EndpointStatus,
        [object]$SnapshotSummary
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
        }
        baselineGate = [ordered]@{
            passed = $script:BaselineExitCode -eq 0
            exitCode = $script:BaselineExitCode
        }
        attempts = $AttemptCount
        waitSeconds = $WaitSeconds
        endpointStatus = $EndpointStatus
        snapshotProbe = $SnapshotSummary
        proofFiles = [ordered]@{
            invocationText = "command_used.txt"
            endpointStatus = "endpoint_status.json"
            endpointResponseRaw = "endpoint_response_raw.txt"
            endpointResponsePretty = "endpoint_response_pretty.json"
            result = "payload_handshake_result.json"
        }
        readOnly = $true
        noClientControl = $true
    }
}

function Complete-Handshake {
    param(
        [string]$Status,
        [string]$Reason,
        [int]$AttemptCount,
        [object]$EndpointStatus,
        [object]$SnapshotSummary
    )

    $payload = New-ResultPayload -Status $Status -Reason $Reason -AttemptCount $AttemptCount -EndpointStatus $EndpointStatus -SnapshotSummary $SnapshotSummary
    Save-Json -Path (Join-Path $script:ProofDir "payload_handshake_result.json") -Payload $payload
    Write-Host "Telemetry payload handshake result: $Status"
    Write-Host "Reason: $Reason"
    Write-Host "Proof folder: $script:ProofDir"
    if ($Status -like "FAIL_*") {
        exit 1
    }
    exit 0
}

function Invoke-ReadOnlyProbe {
    $listening = Test-EndpointListening
    if (-not $listening) {
        $endpointStatus = New-StatusPayload -Listening $false -Health $null -HealthHttp $null -SchemaHttp $null -SnapshotHttp $null
        Save-Json -Path (Join-Path $script:ProofDir "endpoint_status.json") -Payload $endpointStatus
        Write-TextFile -Path (Join-Path $script:ProofDir "endpoint_response_raw.txt") -Lines @("")
        return [ordered]@{
            status = "FAIL_ENDPOINT_NOT_LISTENING"
            reason = "127.0.0.1:8893 is not listening"
            endpointStatus = $endpointStatus
            snapshotSummary = $null
        }
    }

    $healthUri = "$EndpointBase$HealthPath"
    $schemaUri = "$EndpointBase$SchemaPath"
    $snapshotUri = "$EndpointBase$SnapshotPath"
    $snapshotBody = '{"schema":"plugin_snapshot_request.v1","needs":["baseline"],"snapshotTier":"hot","responseMode":"compact","includeCollisionWindow":false,"includeWatchValues":false}'

    $healthHttp = Invoke-EndpointGet -Uri $healthUri
    Write-TextFile -Path (Join-Path $script:ProofDir "endpoint_response_raw.txt") -Lines @([string](Get-JsonValue -Object $healthHttp -Name "raw" -Default ""))
    $healthParse = ConvertFrom-JsonText -Raw ([string](Get-JsonValue -Object $healthHttp -Name "raw" -Default ""))
    $health = Get-JsonValue -Object $healthParse -Name "value"

    if (-not [bool](Get-JsonValue -Object $healthParse -Name "ok" -Default $false)) {
        $endpointStatus = New-StatusPayload -Listening $true -Health $null -HealthHttp $healthHttp -SchemaHttp $null -SnapshotHttp $null
        Save-Json -Path (Join-Path $script:ProofDir "endpoint_status.json") -Payload $endpointStatus
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "health endpoint response was not parseable JSON"
            endpointStatus = $endpointStatus
            snapshotSummary = $null
        }
    }

    Save-Json -Path (Join-Path $script:ProofDir "endpoint_response_pretty.json") -Payload $health

    if ((Get-JsonValue -Object $health -Name "schema") -ne "plugin_snapshot_health.v1") {
        $endpointStatus = New-StatusPayload -Listening $true -Health $health -HealthHttp $healthHttp -SchemaHttp $null -SnapshotHttp $null
        Save-Json -Path (Join-Path $script:ProofDir "endpoint_status.json") -Payload $endpointStatus
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "health endpoint returned an unexpected schema"
            endpointStatus = $endpointStatus
            snapshotSummary = $null
        }
    }

    $schemaHttp = Invoke-EndpointGet -Uri $schemaUri
    $schemaParse = ConvertFrom-JsonText -Raw ([string](Get-JsonValue -Object $schemaHttp -Name "raw" -Default ""))
    $schemaPayload = Get-JsonValue -Object $schemaParse -Name "value"
    if (-not [bool](Get-JsonValue -Object $schemaParse -Name "ok" -Default $false) -or (Get-JsonValue -Object $schemaPayload -Name "schema") -ne "plugin_snapshot_schema.v1") {
        $endpointStatus = New-StatusPayload -Listening $true -Health $health -HealthHttp $healthHttp -SchemaHttp $schemaHttp -SnapshotHttp $null
        Save-Json -Path (Join-Path $script:ProofDir "endpoint_status.json") -Payload $endpointStatus
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "schema endpoint did not return plugin_snapshot_schema.v1"
            endpointStatus = $endpointStatus
            snapshotSummary = $null
        }
    }

    $snapshotHttp = Invoke-EndpointPostJson -Uri $snapshotUri -Body $snapshotBody
    $snapshotParse = ConvertFrom-JsonText -Raw ([string](Get-JsonValue -Object $snapshotHttp -Name "raw" -Default ""))
    $snapshot = Get-JsonValue -Object $snapshotParse -Name "value"
    $snapshotSummary = New-SnapshotSummary -Snapshot $snapshot -SnapshotParse $snapshotParse -SnapshotHttp $snapshotHttp
    $endpointStatus = New-StatusPayload -Listening $true -Health $health -HealthHttp $healthHttp -SchemaHttp $schemaHttp -SnapshotHttp $snapshotHttp
    Save-Json -Path (Join-Path $script:ProofDir "endpoint_status.json") -Payload $endpointStatus

    if (-not [bool](Get-JsonValue -Object $snapshotParse -Name "ok" -Default $false)) {
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "snapshot endpoint response was not parseable JSON"
            endpointStatus = $endpointStatus
            snapshotSummary = $snapshotSummary
        }
    }
    if ((Get-JsonValue -Object $snapshot -Name "schema") -ne "plugin_snapshot_response.v1") {
        return [ordered]@{
            status = "FAIL_ENDPOINT_BAD_RESPONSE"
            reason = "snapshot endpoint returned an unexpected schema"
            endpointStatus = $endpointStatus
            snapshotSummary = $snapshotSummary
        }
    }

    $payloadNames = @(Get-JsonValue -Object $snapshotSummary -Name "payloadNames" -Default @())
    $snapshotStatus = Get-JsonValue -Object $snapshotSummary -Name "status"
    $latestTick = ConvertTo-LongOrDefault -Value (Get-JsonValue -Object $snapshotSummary -Name "latestTick" -Default -1)
    $fresh = [bool](Get-JsonValue -Object $snapshotSummary -Name "fresh" -Default $false)
    $hasBaselinePayload = $payloadNames -contains "baseline"

    if ($snapshotStatus -eq "PASS" -and $hasBaselinePayload -and $latestTick -ge 0 -and $fresh) {
        return [ordered]@{
            status = "PASS_ENDPOINT_PAYLOAD_READY"
            reason = "endpoint returned a fresh baseline telemetry payload"
            endpointStatus = $endpointStatus
            snapshotSummary = $snapshotSummary
        }
    }

    return [ordered]@{
        status = "WARN_ENDPOINT_ALIVE_NO_PAYLOAD"
        reason = "endpoint is alive, but a fresh baseline telemetry payload is not available yet"
        endpointStatus = $endpointStatus
        snapshotSummary = $snapshotSummary
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
$script:ProofDir = Join-Path $script:RepoRoot "_run_proofs\telemetry_payload\$timestamp"
New-Item -ItemType Directory -Force -Path $script:ProofDir | Out-Null

Write-TextFile -Path (Join-Path $script:ProofDir "command_used.txt") -Lines @(
    "Repo root: $script:RepoRoot",
    "Branch: $script:Branch",
    "Baseline gate: $BaselineCommandText",
    "Telemetry handshake: $HandshakeText",
    "Endpoint: $EndpointBase",
    "Paths checked: GET $HealthPath, GET $SchemaPath, POST $SnapshotPath",
    "Snapshot probe need: baseline",
    "Read-only only. No login automation, clicks, keyboard or mouse events, route execution, banking/activity automation, gameplay automation, or direct client control is performed."
)

$baselinePath = Join-Path $script:ProofDir "baseline_result.txt"
$script:BaselineExitCode = Invoke-BaselineGate -OutputPath $baselinePath
if ($script:BaselineExitCode -ne 0) {
    $endpointStatus = [ordered]@{
        schema = "telemetry_endpoint_status.v1"
        checkedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        host = $EndpointHost
        port = $EndpointPort
        listening = $false
        pathsChecked = @()
        baselineGatePassed = $false
    }
    Save-Json -Path (Join-Path $script:ProofDir "endpoint_status.json") -Payload $endpointStatus
    Complete-Handshake -Status "FAIL_ENDPOINT_BAD_RESPONSE" -Reason "deterministic baseline gate failed before endpoint probing" -AttemptCount 0 -EndpointStatus $endpointStatus -SnapshotSummary $null
}

if ($WaitSeconds -gt 0) {
    Write-Host "Polling read-only for up to $WaitSeconds seconds. You may manually put the client into a live scene; this script will not automate login or input."
}

$attempt = 0
$deadline = (Get-Date).AddSeconds([Math]::Max(0, $WaitSeconds))
$lastProbe = $null
do {
    $attempt += 1
    $lastProbe = Invoke-ReadOnlyProbe
    $status = [string](Get-JsonValue -Object $lastProbe -Name "status")
    if ($status -eq "PASS_ENDPOINT_PAYLOAD_READY") {
        break
    }
    if ($WaitSeconds -le 0 -or (Get-Date) -ge $deadline) {
        break
    }
    Start-Sleep -Seconds ([Math]::Max(1, $PollIntervalSeconds))
} while ($true)

Complete-Handshake `
    -Status ([string](Get-JsonValue -Object $lastProbe -Name "status")) `
    -Reason ([string](Get-JsonValue -Object $lastProbe -Name "reason")) `
    -AttemptCount $attempt `
    -EndpointStatus (Get-JsonValue -Object $lastProbe -Name "endpointStatus") `
    -SnapshotSummary (Get-JsonValue -Object $lastProbe -Name "snapshotSummary")

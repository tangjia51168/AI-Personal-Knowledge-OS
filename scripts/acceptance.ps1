param([switch]$RestartCheck, [switch]$Hardened, [switch]$Embedding, [string]$DownloadProxy)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repoRoot
$reportDirectory = Join-Path $repoRoot '.cache/acceptance-reports'
$suite = $null
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
[pscustomobject]@{ Status = 'running'; StartedAt = (Get-Date).ToString('o') } |
    ConvertTo-Json | Out-File -LiteralPath (Join-Path $reportDirectory 'summary.json') -Encoding utf8
try {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    $dockerPath = if ($dockerCommand) { $dockerCommand.Source } else {
        'C:/Program Files/Docker/Docker/resources/bin/docker.exe'
    }
    if (-not (Test-Path -LiteralPath $dockerPath)) {
        throw 'Docker CLI not found. Install/start Docker Desktop, then open a new terminal.'
    }
    & $dockerPath info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker engine is not ready. No tests ran.' }
    $downloadArgs = @('scripts/prepare_minio.py')
    if ($DownloadProxy) { $downloadArgs += @('--proxy', $DownloadProxy) }
    & './.venv/Scripts/python.exe' @downloadArgs
    if ($LASTEXITCODE -ne 0) { throw 'Verified official MinIO binary preparation failed' }
    $mcDownloadArgs = @('scripts/prepare_minio_mc.py')
    if ($DownloadProxy) { $mcDownloadArgs += @('--proxy', $DownloadProxy) }
    & './.venv/Scripts/python.exe' @mcDownloadArgs
    if ($LASTEXITCODE -ne 0) { throw 'Verified official MinIO mc preparation failed' }
    if (-not (Test-Path -LiteralPath '.env.acceptance')) {
        & './.venv/Scripts/python.exe' scripts/configure_dev.py --acceptance
        if ($LASTEXITCODE -ne 0) { throw 'Acceptance configuration failed' }
    }
    $baseComposeArgs = @('--env-file', '.env.acceptance', '-p', 'knowledge-os-acceptance',
        '-f', 'docker-compose.yml', '-f', 'docker-compose.acceptance.yml')
    if ($Hardened) { $baseComposeArgs += @('-f', 'docker-compose.hardened.yml') }
    if ($Embedding) { $baseComposeArgs += @('-f', 'docker-compose.embedding.yml', '-f', 'docker-compose.embedding-acceptance.yml') }
    & $dockerPath compose @baseComposeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid' }
    # Bootstrap credentials stay with MinIO and one-shot initialization. Never
    # recreate a running API/Worker/Beat with root while preparing an acceptance.
    & $dockerPath compose @baseComposeArgs up --build -d --wait --wait-timeout 300 postgres redis minio
    if ($LASTEXITCODE -ne 0) { throw 'Acceptance bootstrap dependencies failed to become healthy' }
    # An initialization container is expected to exit successfully. Listing it
    # as an explicit --wait target can race and reject that normal exit.
    & $dockerPath compose @baseComposeArgs run --rm --no-deps --build storage-init
    if ($LASTEXITCODE -ne 0) { throw 'Acceptance private storage initialization failed' }
    & './.venv/Scripts/python.exe' scripts/provision_storage_runtime.py --acceptance
    if ($LASTEXITCODE -ne 0) { throw 'Scoped storage identity provisioning failed' }
    $runtimeComposeArgs = @('--env-file', '.env.acceptance', '--env-file', '.env.storage-runtime.acceptance',
        '-p', 'knowledge-os-acceptance', '-f', 'docker-compose.yml', '-f',
        'docker-compose.acceptance.yml', '-f', 'docker-compose.storage-runtime.yml')
    if ($Hardened) { $runtimeComposeArgs += @('-f', 'docker-compose.hardened.yml') }
    if ($Embedding) { $runtimeComposeArgs += @('-f', 'docker-compose.embedding.yml', '-f', 'docker-compose.embedding-acceptance.yml') }
    & $dockerPath compose @runtimeComposeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Scoped runtime compose configuration is invalid' }
    & $dockerPath compose @runtimeComposeArgs up --build -d --wait --wait-timeout 300
    if ($LASTEXITCODE -ne 0) { throw 'Scoped runtime services failed to become healthy' }
    $composeArgs = $runtimeComposeArgs
    # Test report is copied out before removing this one test container; preserve on failure.
    $reportDirectory = Join-Path $repoRoot '.cache/acceptance-reports'
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
    $reportName = 'knowledge-os-acceptance-tests-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
    & $dockerPath compose @composeArgs --profile acceptance run --build --name $reportName acceptance
    $testExitCode = $LASTEXITCODE
    & $dockerPath cp "${reportName}:/tmp/acceptance.xml" (Join-Path $reportDirectory 'integration.xml')
    if ($LASTEXITCODE -ne 0) { throw 'Test report could not be collected; test container retained' }
    [xml]$report = Get-Content -LiteralPath (Join-Path $reportDirectory 'integration.xml') -Raw
    $suite = $report.testsuites.testsuite
    if ([int]$suite.tests -lt 123 -or [int]$suite.skipped -gt 0 -or
        [int]$suite.failures -gt 0 -or [int]$suite.errors -gt 0) {
        throw 'Acceptance report contains missing/skipped/failed tests; test container retained'
    }
    if ($testExitCode -eq 0) {
        # This container is created by this script and contains no user data or volumes.
        & $dockerPath rm $reportName
        if ($LASTEXITCODE -ne 0) { throw 'Completed test container cleanup failed' }
    }
    if ($testExitCode -ne 0) { throw 'Live integration acceptance failed; test container retained' }
    if ($RestartCheck) {
        & $dockerPath compose @composeArgs --profile acceptance run --rm acceptance python scripts/persistence_probe.py write
        if ($LASTEXITCODE -ne 0) { throw 'Persistence probe write failed' }
        & $dockerPath compose @composeArgs restart postgres redis minio
        if ($LASTEXITCODE -ne 0) { throw 'Dependency restart failed' }
        & $dockerPath compose @composeArgs up -d --wait --wait-timeout 180
        if ($LASTEXITCODE -ne 0) { throw 'Dependencies failed to recover after restart' }
        & $dockerPath compose @composeArgs --profile acceptance run --rm acceptance python scripts/persistence_probe.py verify
        if ($LASTEXITCODE -ne 0) { throw 'Persistence probe verification failed' }
    }
    & './.venv/Scripts/python.exe' scripts/storage_runtime_check.py --acceptance
    if ($LASTEXITCODE -ne 0) { throw 'Scoped runtime storage identity acceptance failed' }
    [pscustomobject]@{
        Status = 'passed'
        CompletedAt = (Get-Date).ToString('o')
        Project = 'knowledge-os-acceptance'
        Tests = [int]$suite.tests
        Failures = [int]$suite.failures
        Errors = [int]$suite.errors
        Skipped = [int]$suite.skipped
        RestartPersistenceVerified = [bool]$RestartCheck
        HardenedResourceLimits = [bool]$Hardened
        ScopedRuntimeStorageVerified = $true
    } | ConvertTo-Json | Out-File -LiteralPath (Join-Path $reportDirectory 'summary.json') -Encoding utf8
    Write-Output 'PASS: isolated live integration acceptance. Stack retained; no volumes deleted.'
} catch {
    [pscustomobject]@{
        Status = 'failed'
        CompletedAt = (Get-Date).ToString('o')
        Project = 'knowledge-os-acceptance'
        Tests = if ($suite) { [int]$suite.tests } else { 0 }
        Failures = if ($suite) { [int]$suite.failures } else { $null }
        Errors = if ($suite) { [int]$suite.errors } else { $null }
        Skipped = if ($suite) { [int]$suite.skipped } else { $null }
        RestartPersistenceVerified = $false
        HardenedResourceLimits = [bool]$Hardened
        ScopedRuntimeStorageVerified = $false
    } | ConvertTo-Json | Out-File -LiteralPath (Join-Path $reportDirectory 'summary.json') -Encoding utf8
    throw
} finally { Pop-Location }

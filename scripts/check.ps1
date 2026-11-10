$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repoRoot
try {
    $pythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe'
    # Unique workspace directories avoid stale ACLs in shared Windows pytest paths.
    # Preserve artifacts for inspection instead of deleting previous test data.
    $checkRunPath = Join-Path $repoRoot ('.cache/checks/' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $checkRunPath | Out-Null
    $pytestTempPath = Join-Path $checkRunPath 'pytest-temp'
    $pytestCachePath = Join-Path $checkRunPath 'pytest-cache'
    & $pythonPath -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Python lint failed' }
    & $pythonPath -m ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'Python formatting failed' }
    & $pythonPath -m mypy
    if ($LASTEXITCODE -ne 0) { throw 'Python types failed' }
    & $pythonPath -m pytest -m 'not integration' -q --basetemp $pytestTempPath -o "cache_dir=$pytestCachePath"
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
    node --experimental-strip-types --test scripts/collections.test.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Collection response contract failed' }
    node scripts/check-dependency-patch.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Dependency security override is not effective' }
    & $pythonPath scripts/check_image_pins.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime image digest pin check failed' }
    & $pythonPath scripts/foundation_audit.py --inventory
    if ($LASTEXITCODE -ne 0) { throw 'Exact dependency and license inventory failed' }
    node --experimental-strip-types --test scripts/request-body.test.mjs scripts/account-session.test.mjs scripts/project-progress.test.mjs scripts/note-reader.test.mjs scripts/learning-summary.test.mjs scripts/learning-analysis.test.mjs scripts/learning-schedule.test.mjs scripts/review-plan.test.mjs scripts/knowledge-explorer.test.mjs scripts/topic-tree.test.mjs scripts/concept-cards.test.mjs scripts/research-answer.test.mjs scripts/source-schedules.test.mjs scripts/notifications.test.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Request body resource checks failed' }
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw 'TypeScript failed' }
    npm run lint
    if ($LASTEXITCODE -ne 0) { throw 'Frontend lint failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }

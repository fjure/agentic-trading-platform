$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:OPENSPEC_TELEMETRY = '0'
$env:DO_NOT_TRACK = '1'
$env:XDG_CONFIG_HOME = Join-Path $projectRoot '.tooling/config'
$configDirectory = Join-Path $env:XDG_CONFIG_HOME 'openspec'
New-Item -ItemType Directory -Force -Path $configDirectory | Out-Null
$settings = @{
    featureFlags = @{}
    profile = 'custom'
    delivery = 'skills'
    workflows = @('explore', 'propose', 'apply', 'verify', 'sync', 'archive')
    telemetry = @{ enabled = $false }
}
$settings | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $configDirectory 'config.json') -Encoding utf8NoBOM
Push-Location $projectRoot
try {
    & '.tooling/node_modules/.bin/openspec.cmd' init --tools codex --profile custom --language de --no-animation
    if ($LASTEXITCODE -ne 0) { throw 'OpenSpec initialization failed' }
    & '.tooling/node_modules/.bin/openspec.cmd' update
    if ($LASTEXITCODE -ne 0) { throw 'OpenSpec update failed' }
} finally {
    Pop-Location
}

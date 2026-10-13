# Private child entrypoint; invoke OpenSpec only through scripts/openspec.ps1.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:OPENSPEC_TELEMETRY = '0'
$env:DO_NOT_TRACK = '1'
$env:XDG_CONFIG_HOME = Join-Path $projectRoot '.tooling/config'
Push-Location $projectRoot
try {
    & (Join-Path $projectRoot '.tooling/node_modules/.bin/openspec.cmd') @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

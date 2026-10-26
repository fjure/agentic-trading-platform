param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$nodeRoot = Join-Path $root '.tooling/contracts/node/node-v22.23.3-win-x64'
$previousPath = $env:PATH
Push-Location $root
try {
    $env:PATH = "$nodeRoot;$previousPath"
    if (-not $SkipBuild) {
        & (Join-Path $nodeRoot 'npm.cmd') --prefix apps/dashboard run build
        if ($LASTEXITCODE -ne 0) { throw 'Dashboard production build failed.' }
    }
    & .venv/Scripts/python.exe -m tests.integration.backtest_browser_support host
    if ($LASTEXITCODE -ne 0) { throw 'Actual Backtest browser acceptance failed.' }
} finally {
    $env:PATH = $previousPath
    Pop-Location
}

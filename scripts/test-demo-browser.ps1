param([switch]$SkipBuild, [switch]$InstallBrowser)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$nodeRoot = Join-Path $root '.tooling/contracts/node/node-v22.23.3-win-x64'
$nodeBinary = Join-Path $nodeRoot 'node.exe'
$python = Join-Path $root '.venv/Scripts/python.exe'
$cli = Join-Path $root 'apps/dashboard/node_modules/@playwright/test/cli.js'
foreach ($required in @($nodeBinary, $python, $cli)) {
    if (-not (Test-Path -LiteralPath $required)) { throw 'Pinned browser test tools are missing.' }
}
$previousPath = $env:PATH
Push-Location $root
try {
    $env:PATH = "$nodeRoot;$previousPath"
    if (-not $SkipBuild) {
        & (Join-Path $nodeRoot 'npm.cmd') --prefix apps/dashboard run build
        if ($LASTEXITCODE -ne 0) { throw 'Dashboard production build failed.' }
    }
    if ($InstallBrowser) {
        & $nodeBinary $cli install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Pinned Chromium installation failed.' }
    }
    & $python -m tests.integration.demo_browser_support host
    if ($LASTEXITCODE -ne 0) { throw 'Actual API/PostgreSQL browser acceptance failed.' }
} finally {
    $env:PATH = $previousPath
    Pop-Location
}

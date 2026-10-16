param([switch]$SkipBuild, [switch]$InstallBrowser)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$nodeRoot = Join-Path $root '.tooling/contracts/node/node-v22.23.3-win-x64'
$nodeBinary = Join-Path $nodeRoot 'node.exe'
$cli = Join-Path $root 'apps/dashboard/node_modules/@playwright/test/cli.js'
if (-not (Test-Path -LiteralPath $nodeBinary)) { throw 'Verified Node22.23.3 is missing; run bootstrap-contract-tools.ps1.' }
if (-not (Test-Path -LiteralPath $cli)) { throw 'Locked dashboard dependencies are missing; run npm ci --prefix apps/dashboard.' }
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
    & $nodeBinary $cli test --config playwright.config.ts
    if ($LASTEXITCODE -ne 0) { throw 'Supervisor browser tests failed.' }
} finally {
    $env:PATH = $previousPath
    Pop-Location
}

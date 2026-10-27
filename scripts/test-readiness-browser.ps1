param([switch]$SkipBuild, [switch]$Synthetic)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$taskNode = Join-Path $root '.tooling/contracts/node/node-v22.23.3-win-x64'
$previousPath = $env:PATH
Push-Location $root
try {
    $env:PATH = "$taskNode;$previousPath"
    if (-not $SkipBuild) {
        & (Join-Path $taskNode 'npm.cmd') --prefix apps/dashboard run build
        if ($LASTEXITCODE -ne 0) { throw 'Dashboard production build failed.' }
    }
    if ($Synthetic) {
        & (Join-Path $taskNode 'node.exe') apps/dashboard/node_modules/@playwright/test/cli.js test --config playwright.readiness.config.ts
    } else {
        & .venv/Scripts/python.exe -m tests.integration.readiness_browser_support host
    }
    if ($LASTEXITCODE -ne 0) { throw 'Readiness browser verification failed.' }
} finally {
    $env:PATH = $previousPath
    Pop-Location
}

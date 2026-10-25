param([switch]$SkipBuild, [switch]$Crash, [switch]$DayBoundary, [switch]$Model,
      [switch]$SubmitCrash)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$nodeRoot = Join-Path $root '.tooling/contracts/node/node-v22.23.3-win-x64'
$previousPath = $env:PATH
$previousCrash = $env:PAPER_BROWSER_CRASH
$previousDayBoundary = $env:PAPER_BROWSER_DAY_BOUNDARY
$previousModel = $env:PAPER_BROWSER_MODEL
$previousSubmitCrash = $env:PAPER_BROWSER_SUBMIT_CRASH
Push-Location $root
try {
    $env:PATH = "$nodeRoot;$previousPath"
    $env:PAPER_BROWSER_CRASH = if ($Crash) { '1' } else { '0' }
    if (($Crash -and $DayBoundary) -or ($Crash -and $Model) -or ($DayBoundary -and $Model)) { throw 'Select one isolated scenario.' }
    if ($SubmitCrash -and ($Crash -or $DayBoundary -or $Model)) { throw 'Select one isolated scenario.' }
    $env:PAPER_BROWSER_DAY_BOUNDARY = if ($DayBoundary) { '1' } else { '0' }
    $env:PAPER_BROWSER_MODEL = if ($Model) { '1' } else { '0' }
    $env:PAPER_BROWSER_SUBMIT_CRASH = if ($SubmitCrash) { '1' } else { '0' }
    if (-not $SkipBuild) {
        & (Join-Path $nodeRoot 'npm.cmd') --prefix apps/dashboard run build
        if ($LASTEXITCODE -ne 0) { throw 'Dashboard production build failed.' }
    }
    & .venv/Scripts/python.exe -m tests.integration.paper_browser_support host
    if ($LASTEXITCODE -ne 0) { throw 'Actual Paper browser acceptance failed.' }
} finally {
    $env:PATH = $previousPath
    $env:PAPER_BROWSER_CRASH = $previousCrash
    $env:PAPER_BROWSER_DAY_BOUNDARY = $previousDayBoundary
    $env:PAPER_BROWSER_MODEL = $previousModel
    $env:PAPER_BROWSER_SUBMIT_CRASH = $previousSubmitCrash
    Pop-Location
}

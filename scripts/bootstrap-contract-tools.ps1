param([switch]$SkipPython)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tooling = Join-Path $root '.tooling/contracts'
$cache = Join-Path $tooling '.cache/openapi-generator'
$jdkDir = Join-Path $tooling 'jdk'
$nodeDir = Join-Path $tooling 'node'

foreach ($dir in @($tooling, $cache, $jdkDir, $nodeDir)) {
    $resolved = [IO.Path]::GetFullPath($dir)
    $base = [IO.Path]::GetFullPath($tooling)
    if ($resolved -ne $base -and -not $resolved.StartsWith($base + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Tool destination is outside .tooling/contracts: $resolved"
    }
    New-Item -ItemType Directory -Force -Path $resolved | Out-Null
}

function Ensure-VerifiedFile([string]$url, [string]$path, [string]$sha256) {
    $expected = $sha256.ToLowerInvariant()
    if (-not (Test-Path -LiteralPath $path)) {
        Invoke-WebRequest -Uri $url -OutFile $path
    }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "SHA-256 mismatch: $path" }
    Write-Output "Verified SHA-256: $path"
}

$jdkZip = Join-Path $jdkDir 'OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip'
Ensure-VerifiedFile 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip' $jdkZip 'e53a79c3c3d86865bd7e787903884331068e71321714ffd44f145785affc7cb0'
$jdkHome = Join-Path $jdkDir 'jdk-17.0.20.1+1'
if (-not (Test-Path -LiteralPath (Join-Path $jdkHome 'bin/java.exe'))) {
    Expand-Archive -LiteralPath $jdkZip -DestinationPath $jdkDir -Force
}

$nodeZip = Join-Path $nodeDir 'node-v22.23.3-win-x64.zip'
Ensure-VerifiedFile 'https://nodejs.org/download/release/v22.23.3/node-v22.23.3-win-x64.zip' $nodeZip '2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71'
$nodeHome = Join-Path $nodeDir 'node-v22.23.3-win-x64'
if (-not (Test-Path -LiteralPath (Join-Path $nodeHome 'node.exe'))) {
    Expand-Archive -LiteralPath $nodeZip -DestinationPath $nodeDir -Force
}

$jar = Join-Path $cache 'openapi-generator-cli-7.26.0.jar'
Ensure-VerifiedFile 'https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/7.26.0/openapi-generator-cli-7.26.0.jar' $jar '1760050094997b9cc790cc1350be095f15da8c08a996b184d3eadc4ccda5e35e'

$oasdiffDir = Join-Path $tooling 'oasdiff'
New-Item -ItemType Directory -Force -Path $oasdiffDir | Out-Null
$oasdiffArchive = Join-Path $oasdiffDir 'release.tar.gz'
Ensure-VerifiedFile 'https://github.com/oasdiff/oasdiff/releases/download/v1.33.0/oasdiff_1.33.0_windows_amd64.tar.gz' $oasdiffArchive '22f98c7247075f8a446e783595802d6d38637de1760df264f3d4b3309f766c5b'
& tar -xzf $oasdiffArchive -C $oasdiffDir
if ($LASTEXITCODE -ne 0) { throw 'Verified oasdiff extraction failed.' }
& (Join-Path $oasdiffDir 'oasdiff.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'oasdiff version check failed.' }

$previousPath = $env:PATH
try {
    $env:PATH = $nodeHome + ';' + (Join-Path $jdkHome 'bin') + ';' + $previousPath
    Push-Location $tooling
    try {
        & (Join-Path $nodeHome 'npm.cmd') ci --ignore-scripts --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Pinned npm ci failed.' }
    } finally { Pop-Location }
} finally { $env:PATH = $previousPath }

if (-not $SkipPython) {
    $venv = Join-Path $tooling 'venv'
    & py -3.13 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Local Python venv creation failed.' }
    & (Join-Path $venv 'Scripts/python.exe') -m pip install --disable-pip-version-check -r (Join-Path $tooling 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Python requirements installation failed.' }
}

Write-Output 'Contract tools bootstrapped locally. Run .tooling/contracts/venv/Scripts/python.exe scripts/validate-contracts.py, npm --prefix .tooling/contracts run lint, and ./scripts/check-angular-client.ps1.'

param(
    [switch]$SkipCompile,
    [switch]$WriteClient
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tooling = Join-Path $root '.tooling/contracts'
$jdk = Join-Path $tooling 'jdk/jdk-17.0.20.1+1'
$node = Join-Path $tooling 'node/node-v22.23.3-win-x64'
$generator = Join-Path $tooling '.cache/openapi-generator/openapi-generator-cli-7.26.0.jar'
$generatorSha256 = '1760050094997b9cc790cc1350be095f15da8c08a996b184d3eadc4ccda5e35e'
$spec = Join-Path $root 'contracts/http/control-api.openapi.yaml'
$a = Join-Path $tooling 'generated-a'
$b = Join-Path $tooling 'generated-b'
$appGeneratedRoot = Join-Path $root 'apps/dashboard/src/app/api/generated'
$clientDestination = Join-Path $appGeneratedRoot 'control-api'

if (-not (Test-Path -LiteralPath $generator)) { throw 'Pinned OpenAPI Generator 7.26.0 JAR is missing from .tooling/contracts/.cache.' }
if ((Get-FileHash -LiteralPath $generator -Algorithm SHA256).Hash.ToLowerInvariant() -ne $generatorSha256) { throw 'OpenAPI Generator JAR SHA-256 mismatch.' }
if (-not (Test-Path -LiteralPath (Join-Path $jdk 'bin/java.exe'))) { throw 'Verified local Temurin JDK 17 is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $node 'node.exe'))) { throw 'Verified local Node 22.23.3 is missing.' }
if (-not (Test-Path -LiteralPath $spec)) { throw 'Canonical OpenAPI contract is missing.' }

$resolvedTooling = [IO.Path]::GetFullPath($tooling)
foreach ($target in @($a, $b)) {
    $resolvedTarget = [IO.Path]::GetFullPath($target)
    if (-not $resolvedTarget.StartsWith($resolvedTooling + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing cleanup outside .tooling/contracts: $resolvedTarget"
    }
    if (Test-Path -LiteralPath $resolvedTarget) { Remove-Item -LiteralPath $resolvedTarget -Recurse -Force }
}

$env:JAVA_HOME = $jdk
$env:PATH = (Join-Path $node '') + ';' + (Join-Path $jdk 'bin') + ';' + $env:PATH

foreach ($target in @($a, $b)) {
    & (Join-Path $jdk 'bin/java.exe') -jar $generator generate -i $spec -g typescript-angular -o $target --additional-properties=ngVersion=22.2.2,withInterfaces=true,modelPropertyNaming=original,hideGenerationTimestamp=true --global-property=apiDocs=false,modelDocs=false,apiTests=false,modelTests=false
    if ($LASTEXITCODE -ne 0) { throw "Angular client generation failed for $target" }
}

function Get-ContentMap([string]$directory) {
    $map = @{}
    Get-ChildItem -LiteralPath $directory -Recurse -File | ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($directory, $_.FullName).Replace('\', '/')
        $map[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    return $map
}

$first = Get-ContentMap $a
$second = Get-ContentMap $b
if (($first.Keys.Count -ne $second.Keys.Count) -or (@($first.Keys | Where-Object { -not $second.ContainsKey($_) -or $first[$_] -ne $second[$_] }).Count -gt 0)) {
    throw 'Generated Angular clients differ; drift detected.'
}
Write-Output "PASS: two Angular client generations match across $($first.Keys.Count) files (SHA-256)."

$resolvedGeneratedRoot = [IO.Path]::GetFullPath($appGeneratedRoot)
$resolvedDestination = [IO.Path]::GetFullPath($clientDestination)
$destinationPrefix = $resolvedGeneratedRoot + [IO.Path]::DirectorySeparatorChar
if (-not $resolvedDestination.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing client destination outside apps/dashboard/src/app/api/generated: $resolvedDestination"
}

if ($WriteClient) {
    if ((Test-Path -LiteralPath $resolvedDestination) -and -not (Get-Item -LiteralPath $resolvedDestination).PSIsContainer) {
        throw "Client destination exists but is not a directory: $resolvedDestination"
    }
    $previous = @{}
    if (Test-Path -LiteralPath $resolvedDestination) { $previous = Get-ContentMap $resolvedDestination }
    New-Item -ItemType Directory -Force -Path $resolvedDestination | Out-Null
    foreach ($relative in $first.Keys) {
        $source = Join-Path $a ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
        $target = Join-Path $resolvedDestination ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
        $resolvedTargetFile = [IO.Path]::GetFullPath($target)
        if (-not $resolvedTargetFile.StartsWith($resolvedDestination + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing generated file outside client destination: $resolvedTargetFile"
        }
        $parent = Split-Path -Parent $resolvedTargetFile
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        Copy-Item -LiteralPath $source -Destination $resolvedTargetFile -Force
    }
    foreach ($relative in $previous.Keys) {
        if (-not $first.ContainsKey($relative)) {
            $stale = Join-Path $resolvedDestination ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
            $resolvedStale = [IO.Path]::GetFullPath($stale)
            if (-not $resolvedStale.StartsWith($resolvedDestination + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing stale-file removal outside client destination: $resolvedStale"
            }
            Remove-Item -LiteralPath $resolvedStale -Force
        }
    }
    Write-Output "PASS: wrote $($first.Keys.Count) generated files to $resolvedDestination."
} else {
    if (-not (Test-Path -LiteralPath $resolvedDestination -PathType Container)) {
        throw "Generated Angular client is missing at $resolvedDestination; run with -WriteClient to create it."
    }
    $committed = Get-ContentMap $resolvedDestination
    $missing = @($first.Keys | Where-Object { -not $committed.ContainsKey($_) })
    $unexpected = @($committed.Keys | Where-Object { -not $first.ContainsKey($_) })
    $changed = @($first.Keys | Where-Object { $committed.ContainsKey($_) -and $first[$_] -ne $committed[$_] })
    if ($missing.Count -gt 0 -or $unexpected.Count -gt 0 -or $changed.Count -gt 0) {
        throw "Generated client drift: missing=$($missing.Count), unexpected=$($unexpected.Count), changed=$($changed.Count). Run with -WriteClient to refresh intentionally."
    }
    Write-Output "PASS: application client matches both fresh generations across $($committed.Keys.Count) files (SHA-256)."
}

if (-not $SkipCompile) {
    $fixtures = @("import type { Decision } from './generated-a/model/decision';")
    $fixtureIndex = 0
    foreach ($fixtureName in @('decision-result.valid.json', 'decision-result.abstain.json')) {
        $fixturePath = Join-Path $root ('contracts/examples/v1/' + $fixtureName)
        $value = Get-Content -LiteralPath $fixturePath -Raw | ConvertFrom-Json -AsHashtable
        [void]$value.Remove('schemaVersion')
        $literal = $value | ConvertTo-Json -Depth 32 -Compress
        $fixtures += "const fixture$fixtureIndex : Decision = $literal;"
        $fixtureIndex++
    }
    [IO.File]::WriteAllLines((Join-Path $tooling 'client-contract-fixtures.generated.ts'), $fixtures)
    $tsc = Join-Path $tooling 'node_modules/.bin/tsc.cmd'
    if (-not (Test-Path -LiteralPath $tsc)) { throw 'Pinned TypeScript compiler is missing.' }
    & $tsc --project (Join-Path $tooling 'tsconfig.generated.json') --noEmit
    if ($LASTEXITCODE -ne 0) { throw 'Generated Angular client failed TypeScript compile.' }
    Write-Output 'PASS: generated Angular client and success/ABSTAIN fixtures TypeScript compile.'
}

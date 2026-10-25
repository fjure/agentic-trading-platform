param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.tooling/output/paper-clock'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
$commit = '86b37fde2fed7336ea2d0c17928e3015a55d9b4a'
$archive = Join-Path $cache "libfaketime-$commit.tar.gz"
$expected = 'd98aa7beeb21344b3612f60cbfe08196541a2ef25525b3774b11871493a4726e'
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest -Uri "https://codeload.github.com/wolfcw/libfaketime/tar.gz/$commit" -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
    throw 'Pinned libfaketime source integrity mismatch.'
}
@('*', "!libfaketime-$commit.tar.gz") | Set-Content -LiteralPath (Join-Path $cache '.dockerignore') -Encoding utf8
$dockerfile = Join-Path $root 'tests/integration/paper_clock/Dockerfile'
foreach ($target in @('python', 'postgres')) {
    $tag = "agentic-trading-lab-paper-test-clock-${target}:0.9.13"
    & docker build --pull=false --platform linux/amd64 --file $dockerfile --target "$target-clock" --tag $tag $cache
    if ($LASTEXITCODE -ne 0) { throw "Isolated test clock image build failed: $target" }
    $identity = & docker image inspect $tag --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0) { throw 'Built clock image identity unavailable.' }
    Write-Output "$tag $identity"
}
Write-Output "Verified source SHA256 $expected; no clock enabled by image defaults."

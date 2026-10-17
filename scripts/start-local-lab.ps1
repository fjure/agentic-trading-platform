param(
    [switch]$SkipBuild,
    [switch]$ProvisionOnly,
    [switch]$Stop,
    [string]$InitialCash = '10000'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$composePath = Join-Path $root 'infra/compose/compose.yaml'
$runtimeDir = Join-Path $root '.tooling/application/runtime'
$secretDir = Join-Path $root '.tooling/application/secrets'
$postgresSecret = Join-Path $secretDir 'postgres-password'
$nodeRoot = Join-Path $root '.tooling/contracts/node/node-v22.23.3-win-x64'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$bridgeRecord = Join-Path $runtimeDir 'host-bridge.json'
if ($InitialCash -notmatch '^[1-9][0-9]{0,5}(\.[0-9]{1,18})?$|^1000000$') {
    throw 'InitialCash must be a positive exact simulation amount, at most 1000000.'
}

function Set-PrivateAcl([string]$Path, [bool]$Directory) {
    $entry = Get-Item -LiteralPath $Path -Force
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Private configuration cannot be a reparse point.'
    }
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetOwner($identity)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.Access)) { $acl.RemoveAccessRuleSpecific($rule) }
    if ($Directory) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
        [System.IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($Path), $acl)
    } else {
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
            $identity, 'FullControl', 'Allow'))
        [System.IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($Path), $acl)
    }
    $verified = Get-Acl -LiteralPath $Path
    if (-not $verified.AreAccessRulesProtected -or $verified.Access.Count -ne 1 -or
        $verified.Access[0].IdentityReference.Translate(
            [System.Security.Principal.SecurityIdentifier]).Value -ne $identity.Value -or
        $verified.Access[0].AccessControlType -ne 'Allow' -or
        $verified.Access[0].FileSystemRights -ne 'FullControl') {
        throw 'Private configuration ACL verification failed.'
    }
}

function Stop-LocalBridge {
    if (-not (Test-Path -LiteralPath $bridgeRecord)) { return }
    $entry = Get-Item -LiteralPath $bridgeRecord -Force
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Local bridge record cannot be a reparse point.'
    }
    $record = Get-Content -LiteralPath $bridgeRecord -Raw | ConvertFrom-Json
    $bridgeProcess = Get-Process -Id $record.processId -ErrorAction SilentlyContinue
    $expectedPython = [IO.Path]::GetFullPath((Join-Path $root '.venv/Scripts/python.exe'))
    if ($bridgeProcess) {
        if ($bridgeProcess.Path -ne $expectedPython -or
            $bridgeProcess.StartTime.ToUniversalTime().Ticks -ne $record.startedAtTicks) {
            throw 'Local bridge process identity changed; refusing to stop another process.'
        }
        Stop-Process -Id $bridgeProcess.Id
        $bridgeProcess.WaitForExit(5000) | Out-Null
    }
    Remove-Item -LiteralPath $bridgeRecord
}

Push-Location $root
$previousPath = $env:PATH
try {
    if ($Stop) {
        Stop-LocalBridge
        & docker compose -f $composePath --profile lab stop execution worker control-api watchdog
        if ($LASTEXITCODE -ne 0) { throw 'Local role shutdown failed.' }
        return
    }
    if (-not (Test-Path -LiteralPath $postgresSecret)) {
        throw 'Run scripts/bootstrap-application.ps1 first to provision the local database secret.'
    }
    Set-PrivateAcl $secretDir $true
    Set-PrivateAcl $postgresSecret $false
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    Set-PrivateAcl $runtimeDir $true
    foreach ($name in @('execution.json', 'worker.json', 'control.json', 'watchdog.json')) {
        $privateFile = Join-Path $runtimeDir $name
        if (Test-Path -LiteralPath $privateFile) { Set-PrivateAcl $privateFile $false }
    }
    $runtimeConfig = Join-Path $runtimeDir 'runtime.json'
    # The launcher supports only Fake/DISABLED. External providers require a
    # separate explicit operator workflow; no credential or activation code is read.
    if (Test-Path -LiteralPath $runtimeConfig) {
        try { $publicConfig = Get-Content -LiteralPath $runtimeConfig -Raw | ConvertFrom-Json }
        catch { throw 'Invalid public runtime configuration.' }
        if ($publicConfig.mode -ne 'DISABLED' -or $publicConfig.broker_provider -ne 'fake' -or
            $publicConfig.decision_provider -ne 'mock' -or $publicConfig.paper_enabled -ne $false) {
            throw 'Launcher refuses existing external-provider or enabled Paper configuration.'
        }
    } else {
        [IO.File]::WriteAllText($runtimeConfig,
            '{"mode":"DISABLED","broker_provider":"fake","decision_provider":"mock","paper_enabled":false}',
            [Text.UTF8Encoding]::new($false))
    }
    & docker compose -f $composePath --profile lab up -d --wait postgres
    if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL startup failed.' }
    if (-not $SkipBuild) {
        if (-not (Test-Path -LiteralPath (Join-Path $nodeRoot 'node.exe'))) {
            throw 'Verified Node22.23.3 missing; run bootstrap-contract-tools.ps1.'
        }
        $env:PATH = "$nodeRoot;$previousPath"
        & (Join-Path $nodeRoot 'npm.cmd') --prefix apps/dashboard ci --ignore-scripts
        if ($LASTEXITCODE -ne 0) { throw 'Locked dashboard dependency installation failed.' }
        & (Join-Path $nodeRoot 'npm.cmd') --prefix apps/dashboard run build
        if ($LASTEXITCODE -ne 0) { throw 'Dashboard production build failed.' }
        & docker compose -f $composePath --profile lab build execution worker control-api watchdog
        if ($LASTEXITCODE -ne 0) { throw 'Frozen runtime image build failed.' }
    }
    # Use the tag just built, rather than a stale created container's image ID.
    $image = 'agentic-trading-lab-dev-execution:latest'
    & docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true `
        --cap-add CHOWN --cap-add FOWNER `
        --network agentic-trading-lab-dev_local --tmpfs /tmp --user 0:0 `
        --mount "type=bind,source=$runtimeDir,target=/run/provision" `
        --mount "type=bind,source=$postgresSecret,target=/run/secrets/postgres_password,readonly" `
        --entrypoint python $image -m services.runtime.provision --initial-cash $InitialCash
    if ($LASTEXITCODE -ne 0) { throw 'Local runtime provisioning failed; diagnostic output is redacted.' }
    foreach ($name in @('execution.json', 'worker.json', 'control.json', 'watchdog.json')) {
        Set-PrivateAcl (Join-Path $runtimeDir $name) $false
    }
    if (-not $ProvisionOnly) {
        if (-not (Test-Path -LiteralPath (Join-Path $root 'apps/dashboard/dist/dashboard/browser/index.html'))) {
            throw 'Dashboard artifact missing; run without -SkipBuild.'
        }
        # Release only this launcher's verified helper before Docker Desktop
        # attempts to reserve the same loopback port during container recreation.
        Stop-LocalBridge
        & docker compose -f $composePath --profile lab up -d --wait --wait-timeout 60 execution worker control-api watchdog
        if ($LASTEXITCODE -ne 0) { throw 'Local runtime startup failed.' }
        $reachable = $false
        try {
            $probe = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/ready' -TimeoutSec 3
            $reachable = $probe.StatusCode -eq 200
        } catch { }
        if (-not $reachable) {
            $pythonPath = Join-Path $root '.venv/Scripts/python.exe'
            $bridgeProcess = Start-Process -FilePath $pythonPath -WorkingDirectory $root `
                -ArgumentList @('-m', 'services.runtime.host_bridge', 'host') `
                -RedirectStandardOutput (Join-Path $runtimeDir 'host-bridge.stdout.log') `
                -RedirectStandardError (Join-Path $runtimeDir 'host-bridge.stderr.log') `
                -WindowStyle Hidden -PassThru
            $record = @{ processId = $bridgeProcess.Id
                startedAtTicks = $bridgeProcess.StartTime.ToUniversalTime().Ticks }
            [IO.File]::WriteAllText($bridgeRecord, ($record | ConvertTo-Json -Compress),
                [Text.UTF8Encoding]::new($false))
            Set-PrivateAcl $bridgeRecord $false
            for ($attempt = 0; $attempt -lt 6; $attempt++) {
                try {
                    $probe = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/ready' -TimeoutSec 10
                    $reachable = $probe.StatusCode -eq 200
                } catch { }
                if ($reachable) { break }
                if ($bridgeProcess.HasExited) { break }
                Start-Sleep -Milliseconds 500
            }
            if (-not $reachable) {
                if (-not $bridgeProcess.HasExited) { Stop-Process -Id $bridgeProcess.Id }
                throw 'Fixed local transport is unavailable; no dashboard readiness confirmed.'
            }
        }
        Write-Output 'Fake/DISABLED lab started at http://127.0.0.1:8000; trading remains paused.'
        Write-Output 'Only an operator in a private terminal may run: docker compose -f infra/compose/compose.yaml --profile lab exec control-api python -m services.runtime activate'
    }
} finally {
    $env:PATH = $previousPath
    Pop-Location
}

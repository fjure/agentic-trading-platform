param([string]$Python = "python")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$uv = Join-Path $root '.tooling/application/tool-venv/Scripts/uv.exe'
if (-not (Test-Path -LiteralPath $uv)) {
    & $Python -m venv .tooling/application/tool-venv
    if ($LASTEXITCODE -ne 0) { throw 'Tool virtualenv failed' }
    & ./.tooling/application/tool-venv/Scripts/python.exe -m pip install --disable-pip-version-check uv==0.13.0
    if ($LASTEXITCODE -ne 0) { throw 'Pinned uv installation failed' }
}
& $uv sync --frozen --python $Python --no-python-downloads
if ($LASTEXITCODE -ne 0) { throw 'Frozen dependency installation failed' }
# Disposable local database secret stays outside Git, output and model context.
$secretDir = Join-Path $root '.tooling/application/secrets'
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$directoryAcl = Get-Acl -LiteralPath $secretDir
$directoryAcl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($directoryAcl.Access)) { $directoryAcl.RemoveAccessRuleSpecific($rule) }
$directoryAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
    $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    [IO.DirectoryInfo]::new($secretDir).SetAccessControl($directoryAcl)
} else {
    [System.IO.FileSystemAclExtensions]::SetAccessControl(
        [IO.DirectoryInfo]::new($secretDir), $directoryAcl)
}
$secretFile = Join-Path $secretDir 'postgres-password'
if (-not (Test-Path -LiteralPath $secretFile)) {
    $randomBytes = New-Object byte[] 48
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($randomBytes) } finally { $random.Dispose() }
    [System.IO.File]::WriteAllText($secretFile, [Convert]::ToBase64String($randomBytes))
}
# Replace the file DACL even on an existing installation, without reading the secret.
$fileAcl = Get-Acl -LiteralPath $secretFile
$fileAcl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($fileAcl.Access)) { $fileAcl.RemoveAccessRuleSpecific($rule) }
$fileAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
    $identity, 'FullControl', 'Allow'))
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    [IO.FileInfo]::new($secretFile).SetAccessControl($fileAcl)
} else {
    [System.IO.FileSystemAclExtensions]::SetAccessControl(
        [IO.FileInfo]::new($secretFile), $fileAcl)
}
foreach ($privatePath in @($secretDir, $secretFile)) {
    $verifiedAcl = Get-Acl -LiteralPath $privatePath
    if (-not $verifiedAcl.AreAccessRulesProtected -or $verifiedAcl.Access.Count -ne 1) {
        throw 'Private database secret ACL verification failed'
    }
    $verifiedRule = $verifiedAcl.Access[0]
    if ($verifiedRule.IdentityReference.Translate(
        [System.Security.Principal.SecurityIdentifier]).Value -ne $identity.Value -or
        $verifiedRule.AccessControlType -ne 'Allow' -or
        $verifiedRule.FileSystemRights -ne 'FullControl') {
        throw 'Private database secret ACL identity verification failed'
    }
}
Write-Output 'Frozen dependencies installed; local database secret provisioned without disclosure.'

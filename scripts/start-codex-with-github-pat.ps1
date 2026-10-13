# Run only in your own external PowerShell window, never through an agent tool.
# The PAT is entered privately and passed only to the new app process.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$packages = @(Get-AppxPackage -Name OpenAI.Codex)
if ($packages.Count -ne 1) {
    throw 'Expected exactly one installed OpenAI.Codex package. No token requested.'
}
$appPath = Join-Path $packages[0].InstallLocation 'app\ChatGPT.exe'
if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
    throw 'Codex app executable not found. No token requested.'
}

Write-Host 'Save your work and fully quit Codex using its Exit/Quit command.'
Write-Host 'Keep this external PowerShell window open.'
[void](Read-Host 'Press Enter after Codex has fully exited')
$remaining = @(Get-Process -Name ChatGPT,codex-code-mode-host -ErrorAction SilentlyContinue)
if ($remaining.Count -gt 0) {
    throw 'Codex or its host is still running. Fully quit it, then run this script again. No token requested.'
}

$securePat = $null
$patPointer = [IntPtr]::Zero
$startInfo = $null
$appProcess = $null
try {
    $securePat = Read-Host 'Paste the GitHub fine-grained PAT (hidden)' -AsSecureString
    if ($securePat.Length -eq 0) {
        throw 'No token entered.'
    }
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $appPath
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $patPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePat)
    $startInfo.EnvironmentVariables['GITHUB_PAT_TOKEN'] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($patPointer)
    $appProcess = [Diagnostics.Process]::Start($startInfo)
    if ($null -eq $appProcess) {
        throw 'App process did not start.'
    }
    Write-Host 'Codex start requested. Reopen this project and chat; ask to verify the GitHub MCP.'
    Write-Host 'Authentication and repository permissions have not yet been verified.'
}
catch {
    # Do not print exception objects or runtime state containing secret values.
    throw 'Private PAT input or Codex startup failed. No token was written to a file.'
}
finally {
    if ($null -ne $startInfo) { $startInfo.EnvironmentVariables.Remove('GITHUB_PAT_TOKEN') }
    if ($patPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($patPointer) }
    if ($null -ne $securePat) { $securePat.Dispose() }
    if ($null -ne $appProcess) { $appProcess.Dispose() }
    $startInfo = $null
}

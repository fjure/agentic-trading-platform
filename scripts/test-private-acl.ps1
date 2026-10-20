# Exercise the actual launcher/bootstrap ACL code without Docker or real secrets.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$fixtureBase = [IO.Path]::GetFullPath((Join-Path $repoRoot '.tooling/output/acl-tests'))
$fixtureRoot = Join-Path $fixtureBase ([Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null
try {
    $tokens = $null
    $errors = $null
    $launcher = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot 'start-local-lab.ps1'), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Launcher parse failed.' }
    $function = $launcher.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Set-PrivateAcl'
    }, $true)
    if (-not $function) { throw 'Launcher ACL function missing.' }
    . ([scriptblock]::Create($function.Extent.Text))
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $directory = Join-Path $fixtureRoot 'launcher [literal]'
    New-Item -ItemType Directory -Path $directory | Out-Null
    $file = Join-Path $directory 'synthetic.txt'
    [IO.File]::WriteAllText($file, 'synthetic fixture')
    # Existing inherited permissions must become protected owner-only permissions.
    Set-PrivateAcl $directory $true
    Set-PrivateAcl $file $false
    Set-PrivateAcl $directory $true
    Set-PrivateAcl $file $false

    $bootstrap = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot 'bootstrap-application.ps1'), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Bootstrap parse failed.' }
    $statements = @($bootstrap.EndBlock.Statements)
    $start = -1
    for ($index = 0; $index -lt $statements.Count; $index++) {
        if ($statements[$index].Extent.Text.StartsWith('$secretDir =')) {
            $start = $index
            break
        }
    }
    if ($start -lt 0) { throw 'Bootstrap private provisioning block missing.' }
    $provision = ($statements[$start..($statements.Count - 2)] |
        ForEach-Object { $_.Extent.Text }) -join [Environment]::NewLine
    # Execute only private provisioning against a fresh disposable root, never
    # the dependency installer or an existing runtime/credential directory.
    $root = Join-Path $fixtureRoot 'bootstrap'
    & ([scriptblock]::Create($provision))
    & ([scriptblock]::Create($provision))
    Write-Output ('PASS: launcher and bootstrap owner-only ACLs, fresh/existing files; PowerShell ' +
        $PSVersionTable.PSVersion.ToString())
} finally {
    $resolved = [IO.Path]::GetFullPath($fixtureRoot)
    if (-not $resolved.StartsWith($fixtureBase + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Fixture cleanup escaped the test directory.'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

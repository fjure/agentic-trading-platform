$ErrorActionPreference = 'Stop'
# Keep the project profile and telemetry overrides out of the caller's environment.
# In particular, GH CLI must retain its own private authentication configuration.
& (Join-Path $PSHOME 'pwsh.exe') -NoProfile -File (Join-Path $PSScriptRoot 'openspec-entrypoint.ps1') @args
exit $LASTEXITCODE

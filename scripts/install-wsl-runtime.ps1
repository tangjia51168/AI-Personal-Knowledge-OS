# Installs only the official WSL runtime. No Linux distribution and no automatic reboot.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator rights are required to install the WSL runtime.'
}
$logDirectory = Join-Path $repoRoot '.cache/container-prerequisites'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Start-Transcript -Path (Join-Path $logDirectory 'install-wsl-runtime.log') -Append
try {
    winget install --id Microsoft.WSL --exact --source winget --silent --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "WSL installer failed: $LASTEXITCODE" }
    Write-Output 'WSL runtime installation completed. No automatic restart requested.'
} finally { Stop-Transcript }

# Run with Windows administrator rights. Never reboots the computer.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open an Administrator PowerShell and run this script again.'
}
$logDirectory = Join-Path $repoRoot '.cache/container-prerequisites'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Start-Transcript -Path (Join-Path $logDirectory 'enable-features.log') -Append
try {
    foreach ($featureName in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
        & dism.exe /Online /Enable-Feature "/FeatureName:$featureName" /All /NoRestart
        if ($LASTEXITCODE -notin @(0, 3010)) { throw "Feature enable failed: $featureName" }
    }
    Write-Output 'Features enabled/requested. Save your work, then reboot Windows manually.'
    Write-Output 'No automatic reboot or Docker license interaction was performed.'
} finally { Stop-Transcript }

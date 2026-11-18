<#
.SYNOPSIS
Build the Windows installer (Inno Setup) from the PyInstaller executables.

.EXAMPLE
pwsh packaging/build_installer.ps1 -Version 1.2.0
pwsh packaging/build_installer.ps1 -Version 1.2.0 -GuiExe dist\wowfish-gui-1.2.0-windows-x86_64.exe `
  -CliExe dist\wowfish-1.2.0-windows-x86_64.exe -OutputBaseName wowfish-setup-1.2.0-windows-x86_64

Installs Inno Setup with Chocolatey when ISCC.exe is not found. Prints the installer's path.
#>
param(
    [Parameter(Mandatory)] [string] $Version,
    [string] $GuiExe = 'dist\wowfish-gui.exe',
    [string] $CliExe = 'dist\wowfish.exe',
    [string] $OutputDir = 'dist',
    [string] $OutputBaseName = 'wowfish-setup'
)
$ErrorActionPreference = 'Stop'

function Find-Iscc {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($dir in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, "$env:LOCALAPPDATA\Programs")) {
        if (-not $dir) { continue }
        $p = Join-Path $dir 'Inno Setup 6\ISCC.exe'
        if (Test-Path $p) { return $p }
    }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) {
    Write-Host 'Inno Setup not found; installing it with Chocolatey'
    choco install innosetup -y --no-progress | Out-Host
    if ($LASTEXITCODE) { throw "choco install innosetup failed ($LASTEXITCODE)" }
    $iscc = Find-Iscc
    if (-not $iscc) { throw 'ISCC.exe still not found after installing Inno Setup' }
}

# VersionInfoVersion must be four numbers; 1.2.0rc1 or 1.2.0.dev3+g1a2b3c4 become 1.2.0.0.
if ($Version -notmatch '^(\d+)\.(\d+)\.(\d+)') { throw "unexpected version '$Version'" }
$numeric = "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"

$iss = Join-Path $PSScriptRoot 'wowfish.iss'
$out = (New-Item -ItemType Directory -Force $OutputDir).FullName
& $iscc /Q `
    "/DAppVersion=$Version" `
    "/DNumericVersion=$numeric" `
    "/DGuiExe=$((Resolve-Path $GuiExe).Path)" `
    "/DCliExe=$((Resolve-Path $CliExe).Path)" `
    "/DOutputDir=$out" `
    "/DOutputBaseName=$OutputBaseName" `
    $iss
if ($LASTEXITCODE) { throw "ISCC failed ($LASTEXITCODE)" }
Join-Path $out "$OutputBaseName.exe"

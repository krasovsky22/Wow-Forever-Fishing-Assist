<#
.SYNOPSIS
Install the given installer silently for this user, check the files and shortcuts, then
uninstall it and check they are gone. Used by CI and release.yml.
#>
param([Parameter(Mandatory)] [string] $Installer)
$ErrorActionPreference = 'Stop'

$name = 'WoW Forever Fishing Assist'
$app = Join-Path $env:LOCALAPPDATA "Programs\$name"
$desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk"
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) "$name.lnk"

$p = Start-Process $Installer -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
if ($p.ExitCode) { throw "installer exited with $($p.ExitCode)" }

foreach ($f in "$app\wowfish-gui.exe", "$app\wowfish.exe", "$app\unins000.exe", $desktop, $startMenu) {
    if (-not (Test-Path $f)) { throw "missing after install: $f" }
}
& "$app\wowfish.exe" --version; if ($LASTEXITCODE) { exit $LASTEXITCODE }
$target = (New-Object -ComObject WScript.Shell).CreateShortcut($desktop).TargetPath
if ($target -ne "$app\wowfish-gui.exe") { throw "desktop shortcut points at '$target'" }

$p = Start-Process "$app\unins000.exe" -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
if ($p.ExitCode) { throw "uninstaller exited with $($p.ExitCode)" }
# The uninstaller finishes in a copy of itself; give it a moment to delete the folder.
for ($i = 0; $i -lt 20 -and (Test-Path $app); $i++) { Start-Sleep 1 }
foreach ($f in $app, $desktop, $startMenu) {
    if (Test-Path $f) { throw "still there after uninstall: $f" }
}
Write-Host "installer OK: $Installer"

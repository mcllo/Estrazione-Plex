$ErrorActionPreference = "Stop"
. "$PSScriptRoot\windows-build-common.ps1"

$ProjectRoot = Get-ProjectRoot
Set-ProjectLocation -ProjectRoot $ProjectRoot

Write-Host "== PlexInventory local build =="
Write-Host "Project: $ProjectRoot"

Ensure-Venv -ProjectRoot $ProjectRoot
$tools = Get-VenvTools -ProjectRoot $ProjectRoot

Install-Dependencies -VenvPython $tools.Python -VenvPip $tools.Pip

Write-Host "Pulizia build precedente..."
Remove-PreviousArtifacts

Write-Host "Compilo con PyInstaller..."
Build-Executable -PyInstallerPath $tools.PyInstaller -Clean

Package-PortableZip

Write-Host ""
Write-Host "Build completata."
Write-Host "EXE: $ProjectRoot\dist\PlexInventory\PlexInventory.exe"
Write-Host "ZIP: $ProjectRoot\PlexInventory-windows-portable.zip"

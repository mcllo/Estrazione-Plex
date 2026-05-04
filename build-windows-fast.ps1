$ErrorActionPreference = "Stop"
. "$PSScriptRoot\windows-build-common.ps1"

$ProjectRoot = Get-ProjectRoot
Set-ProjectLocation -ProjectRoot $ProjectRoot

Write-Host "== PlexInventory FAST local build =="
Write-Host "Project: $ProjectRoot"

Ensure-Venv -ProjectRoot $ProjectRoot
$tools = Get-VenvTools -ProjectRoot $ProjectRoot

if (-not (Test-Path $tools.PyInstaller)) {
    Install-Dependencies -VenvPython $tools.Python -VenvPip $tools.Pip
}

Write-Host "Compilo con PyInstaller senza --clean..."
Build-Executable -PyInstallerPath $tools.PyInstaller

Write-Host ""
Write-Host "Build FAST completata."
Write-Host "EXE: $ProjectRoot\dist\PlexInventory\PlexInventory.exe"
Write-Host ""
Write-Host "ZIP non creato. Quando ti serve condividere il programma usa: .\package-windows.ps1"
Write-Host "Per una build pulita usa: .\build-windows-local.ps1"

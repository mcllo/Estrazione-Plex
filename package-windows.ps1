$ErrorActionPreference = "Stop"
. "$PSScriptRoot\windows-build-common.ps1"

$ProjectRoot = Get-ProjectRoot
Set-ProjectLocation -ProjectRoot $ProjectRoot

Write-Host "== Packaging PlexInventory =="

Package-PortableZip

Write-Host ""
Write-Host "ZIP creato: $ProjectRoot\PlexInventory-windows-portable.zip"

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "== Packaging PlexInventory =="

if (-not (Test-Path "dist\PlexInventory")) {
    throw "Cartella dist\\PlexInventory non trovata. Compila prima il progetto."
}

if (Test-Path "PlexInventory-windows-portable.zip") {
    Remove-Item "PlexInventory-windows-portable.zip" -Force
}

Write-Host "Creo ZIP..."
Compress-Archive -Force -Path "dist\PlexInventory\*" -DestinationPath "PlexInventory-windows-portable.zip"

Write-Host ""
Write-Host "ZIP creato: $ProjectRoot\PlexInventory-windows-portable.zip"

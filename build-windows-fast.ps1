$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "== PlexInventory FAST local build =="
Write-Host "Project: $ProjectRoot"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creo ambiente virtuale .venv con Python 3.11..."
    py -3.11 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\pip.exe install -r requirements.txt
}

if (-not (Test-Path ".venv\Scripts\pyinstaller.exe")) {
    Write-Host "Installo dipendenze mancanti..."
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\pip.exe install -r requirements.txt
}

Write-Host "Compilo con PyInstaller senza --clean..."
.\.venv\Scripts\pyinstaller.exe --noconfirm PlexInventory.spec

Write-Host ""
Write-Host "Build FAST completata."
Write-Host "EXE: $ProjectRoot\dist\PlexInventory\PlexInventory.exe"
Write-Host ""
Write-Host "ZIP non creato. Quando ti serve condividere il programma usa: .\package-windows.ps1"
Write-Host "Per una build pulita usa: .\build-windows-local.ps1"

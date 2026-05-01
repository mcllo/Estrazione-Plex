$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "== PlexInventory local build =="
Write-Host "Project: $ProjectRoot"

$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $PythonLauncher) {
    $PythonCmd = "py -3.11"
} else {
    $PythonLauncher = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $PythonLauncher) {
        throw "Python non trovato. Installa Python 3.11 e riprova."
    }
    $PythonCmd = "python"
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creo ambiente virtuale .venv..."
    Invoke-Expression "$PythonCmd -m venv .venv"
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvPip = Join-Path $ProjectRoot ".venv\Scripts\pip.exe"
$VenvPyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"

Write-Host "Aggiorno pip e dipendenze..."
& $VenvPython -m pip install --upgrade pip
& $VenvPip install -r requirements.txt

Write-Host "Pulizia build precedente..."
if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
if (Test-Path "dist\PlexInventory") { Remove-Item "dist\PlexInventory" -Recurse -Force }
if (Test-Path "PlexInventory-windows-portable.zip") { Remove-Item "PlexInventory-windows-portable.zip" -Force }

Write-Host "Compilo con PyInstaller..."
& $VenvPyInstaller --clean --noconfirm PlexInventory.spec

Write-Host "Creo ZIP portabile..."
Compress-Archive -Force -Path "dist\PlexInventory\*" -DestinationPath "PlexInventory-windows-portable.zip"

Write-Host ""
Write-Host "Build completata."
Write-Host "EXE: $ProjectRoot\dist\PlexInventory\PlexInventory.exe"
Write-Host "ZIP: $ProjectRoot\PlexInventory-windows-portable.zip"

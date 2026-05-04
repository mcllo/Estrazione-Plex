Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ProjectRoot {
    $PSScriptRoot
}

function Set-ProjectLocation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    Set-Location $ProjectRoot
}

function Get-PythonCommand {
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $pythonLauncher) {
        return "py -3.11"
    }

    $pythonExecutable = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonExecutable) {
        throw "Python non trovato. Installa Python 3.11 e riprova."
    }

    return "python"
}

function Ensure-Venv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return
    }

    Write-Host "Creo ambiente virtuale .venv..."
    $pythonCommand = Get-PythonCommand
    Invoke-Expression "$pythonCommand -m venv .venv"
}

function Get-VenvTools {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $venvPath = Join-Path $ProjectRoot ".venv\Scripts"
    return @{
        Python = Join-Path $venvPath "python.exe"
        Pip = Join-Path $venvPath "pip.exe"
        PyInstaller = Join-Path $venvPath "pyinstaller.exe"
    }
}

function Install-Dependencies {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VenvPython,
        [Parameter(Mandatory = $true)]
        [string]$VenvPip
    )

    Write-Host "Aggiorno pip e dipendenze..."
    & $VenvPython -m pip install --upgrade pip
    & $VenvPip install -r requirements.txt
}

function Remove-PreviousArtifacts {
    if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
    if (Test-Path "dist\PlexInventory") { Remove-Item "dist\PlexInventory" -Recurse -Force }
    if (Test-Path "PlexInventory-windows-portable.zip") { Remove-Item "PlexInventory-windows-portable.zip" -Force }
}

function Build-Executable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PyInstallerPath,
        [switch]$Clean
    )

    $arguments = @("--noconfirm", "PlexInventory.spec")
    if ($Clean) {
        $arguments = @("--clean") + $arguments
    }

    & $PyInstallerPath @arguments
}

function Package-PortableZip {
    if (-not (Test-Path "dist\PlexInventory")) {
        throw "Cartella dist\\PlexInventory non trovata. Compila prima il progetto."
    }

    if (Test-Path "PlexInventory-windows-portable.zip") {
        Remove-Item "PlexInventory-windows-portable.zip" -Force
    }

    Write-Host "Creo ZIP..."
    Compress-Archive -Force -Path "dist\PlexInventory\*" -DestinationPath "PlexInventory-windows-portable.zip"
}

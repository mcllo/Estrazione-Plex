@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File ".\build-windows-local.ps1"
if errorlevel 1 (
  exit /b %errorlevel%
)

echo.
echo Build completata: PlexInventory-windows-portable.zip

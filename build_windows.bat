@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" non trovato. Installa Python 3.11+ per Windows.
  exit /b 1
)

py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --clean --noconfirm PlexInventory.spec
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path PlexInventory-windows-portable.zip) { Remove-Item PlexInventory-windows-portable.zip }; Compress-Archive -Path dist\PlexInventory\* -DestinationPath PlexInventory-windows-portable.zip"

echo.
echo Build completata: PlexInventory-windows-portable.zip

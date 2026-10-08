@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo OnDaGo Editor is not installed yet. Running install.bat ...
  call "%~dp0install.bat"
  exit /b
)
if /i "%1"=="debug" (
  ".venv\Scripts\python.exe" -m app.main
  pause
) else (
  start "" ".venv\Scripts\pythonw.exe" -m app.main
)

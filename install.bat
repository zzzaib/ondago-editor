@echo off
setlocal
cd /d "%~dp0"
title OnDaGo Editor - Installer
echo.
echo  OnDaGo Editor installer
echo  Downloads Python, PyTorch, FFmpeg, Ollama and all AI models into this folder.
echo  First install is about 30-45 GB and can take a while. Re-running is safe.
echo.
echo  Options:  install.bat -Lite    (skip the 16 GB AI image model)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap.ps1" %*
if errorlevel 1 (
  echo.
  echo  Install FAILED. See install.log in this folder, fix the issue and run install.bat again.
  pause
  exit /b 1
)
echo.
choice /c YN /m "Launch OnDaGo Editor now"
if errorlevel 2 exit /b 0
start "" "%~dp0.venv\Scripts\pythonw.exe" -m app.main

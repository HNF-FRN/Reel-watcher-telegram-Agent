@echo off
rem Guided setup for Reel Agent. Type ".\setup" in this folder (cmd or PowerShell). Add --check to only report.
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install it first:  winget install Python.Python.3.13
  echo Then close this window, open a new one, and run .\setup again.
  exit /b 1
)
python "%~dp0setup.py" %*

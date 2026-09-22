@echo off
rem Lets you type "bot status", "bot restart" etc. in cmd or PowerShell from this folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bot.ps1" %*

@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install.ps1" %*
pause

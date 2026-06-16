@echo off
chcp 65001 >nul

echo ============================================================
echo   Dev Test Mode
echo   Using PowerShell launcher: dev_start.ps1
echo   Close this window to exit
echo ============================================================

powershell -ExecutionPolicy Bypass -File "%~dp0dev_start.ps1"

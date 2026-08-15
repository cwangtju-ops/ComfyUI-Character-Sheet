@echo off
setlocal
title Expression Wizard

set "EW_ROOT=%~dp0"
set "EW_SERVER=%EW_ROOT%ComfyUI_Workflows\expression_wizard\server.py"
set "EW_URL=http://127.0.0.1:8765/"

if defined EXPRESSION_WIZARD_PYTHON (
  set "EW_PYTHON=%EXPRESSION_WIZARD_PYTHON%"
) else (
  set "EW_PYTHON=python"
)

if not exist "%EW_SERVER%" (
  echo Expression Wizard could not find its backend:
  echo %EW_SERVER%
  pause
  exit /b 1
)

"%EW_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo Expression Wizard could not start Python.
  echo Set EXPRESSION_WIZARD_PYTHON to your ComfyUI Python executable.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; try { $h=Invoke-RestMethod -Uri ($env:EW_URL + 'api/explore/health') -TimeoutSec 2; if ($h.service -eq 'Expression Wizard') { Start-Process $env:EW_URL; exit 10 }; Write-Host 'Port 8765 is occupied by a different HTTP service.'; exit 11 } catch { $listener=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if ($listener) { Write-Host 'Port 8765 is occupied by another process. Expression Wizard did not stop it.'; exit 11 }; exit 0 }"

if errorlevel 11 goto :conflict
if errorlevel 10 goto :existing

echo Starting Expression Wizard at %EW_URL%
echo Press Ctrl+C to stop the server cleanly.
"%EW_PYTHON%" "%EW_SERVER%" --host 127.0.0.1 --port 8765 --open-browser
if errorlevel 1 (
  echo.
  echo Expression Wizard stopped with an error.
  pause
)
exit /b %errorlevel%

:existing
echo Expression Wizard is already running. Opened %EW_URL%
exit /b 0

:conflict
echo.
echo Expression Wizard was not started because port 8765 is already in use.
pause
exit /b 1

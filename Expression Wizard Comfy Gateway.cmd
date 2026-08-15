@echo off
setlocal
title Expression Wizard ComfyUI Gateway

set "EW_ROOT=%~dp0"
set "EW_GATEWAY=%EW_ROOT%ComfyUI_Workflows\expression_wizard\comfy_gateway.py"

if defined EXPRESSION_WIZARD_PYTHON (
  set "EW_PYTHON=%EXPRESSION_WIZARD_PYTHON%"
) else (
  set "EW_PYTHON=python"
)

if not defined EXPRESSION_WIZARD_ALLOWED_CLIENTS (
  echo EXPRESSION_WIZARD_ALLOWED_CLIENTS is not set.
  echo Set it to the laptop IPv4, for example 192.168.2.242
  pause
  exit /b 1
)

"%EW_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo Could not start Python. Set EXPRESSION_WIZARD_PYTHON to the ComfyUI Python executable.
  pause
  exit /b 1
)

echo Starting the authenticated ComfyUI gateway on port 8189.
echo Allowed laptop: %EXPRESSION_WIZARD_ALLOWED_CLIENTS%
echo Keep ComfyUI bound to 127.0.0.1:8188.
echo Press Ctrl+C to stop.
if defined EXPRESSION_WIZARD_COMFY_ROOT (
  "%EW_PYTHON%" "%EW_GATEWAY%" --host 0.0.0.0 --port 8189 --api http://127.0.0.1:8188 --comfy-root "%EXPRESSION_WIZARD_COMFY_ROOT%"
) else (
  "%EW_PYTHON%" "%EW_GATEWAY%" --host 0.0.0.0 --port 8189 --api http://127.0.0.1:8188
)
if errorlevel 1 (
  echo.
  echo The ComfyUI gateway stopped with an error.
  pause
)
exit /b %errorlevel%

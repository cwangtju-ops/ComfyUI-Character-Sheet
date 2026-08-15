@echo off
setlocal
title Expression Wizard Laptop Backend

set "EW_ROOT=%~dp0"
set "EW_SERVER=%EW_ROOT%ComfyUI_Workflows\expression_wizard\server.py"
if defined EXPRESSION_WIZARD_LOCAL_PORT (
  set "EW_PORT=%EXPRESSION_WIZARD_LOCAL_PORT%"
) else (
  set "EW_PORT=8765"
)

if defined EXPRESSION_WIZARD_DEV_PYTHON (
  set "EW_PYTHON=%EXPRESSION_WIZARD_DEV_PYTHON%"
) else if defined EXPRESSION_WIZARD_PYTHON (
  set "EW_PYTHON=%EXPRESSION_WIZARD_PYTHON%"
) else if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  set "EW_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
) else (
  set "EW_PYTHON=python"
)

if not defined EXPRESSION_WIZARD_DATA_ROOT (
  echo EXPRESSION_WIZARD_DATA_ROOT is not set to the laptop anchor and output folder.
  pause
  exit /b 1
)
if not defined EXPRESSION_WIZARD_COMFY_URL (
  echo EXPRESSION_WIZARD_COMFY_URL is not set. Example: http://192.168.2.200:8189
  pause
  exit /b 1
)
if not defined EXPRESSION_WIZARD_COMFY_TOKEN (
  echo EXPRESSION_WIZARD_COMFY_TOKEN is not set. Copy the token printed by the desktop gateway.
  pause
  exit /b 1
)

"%EW_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo Could not start Python. Set EXPRESSION_WIZARD_DEV_PYTHON to Python 3.10 or newer.
  pause
  exit /b 1
)

echo Starting the laptop-owned Expression Wizard backend at http://127.0.0.1:%EW_PORT%/
echo ComfyUI gateway: %EXPRESSION_WIZARD_COMFY_URL%
if not defined EXPRESSION_WIZARD_COMFY_ADMIN_TOKEN (
  echo Read-only ComfyUI management: disabled ^(EXPRESSION_WIZARD_COMFY_ADMIN_TOKEN is not set^)
) else (
  echo Read-only ComfyUI management: enabled
)
"%EW_PYTHON%" "%EW_SERVER%" --host 127.0.0.1 --port %EW_PORT% --open-browser
if errorlevel 1 (
  echo.
  echo Expression Wizard stopped with an error.
  pause
)
exit /b %errorlevel%
